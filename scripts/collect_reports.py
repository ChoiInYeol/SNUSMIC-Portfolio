"""대학 투자동아리 리서치 리포트 PDF 수집기 (YIG/STAR/KUVIC).

원칙:
- 모든 HTTP 요청 사이에 DELAY_SECONDS 지연을 두어 원 서버에 부하를 주지 않는다.
- 각 PDF는 1회만 다운로드한다: data/bronze/reports/manifest.json에 기록하고,
  재실행 시 manifest에 있고 파일이 존재하면 건너뛴다.
- 다운로드 실패는 기록만 하고 전체 수집을 중단하지 않는다.

사용:
    python scripts/collect_reports.py --source yig|star|kuvic|all [--limit N]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, unquote_to_bytes, urljoin, urlparse

import requests

from lake import BRONZE_KUVIC_ITEMS, BRONZE_PDFS, BRONZE_REPORT_MANIFEST, SILVER_MARKDOWN, resolve_data_path

# Windows 콘솔(cp949)에서 NFD 한글 등 출력 크래시 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
PDF_ROOT = BRONZE_PDFS
MANIFEST_PATH = BRONZE_REPORT_MANIFEST

DELAY_SECONDS = 2.5
MAX_PDF_BYTES = 120 * 1024 * 1024
# 일부 호스팅(가비아)이 "compatible;" 류 봇 UA를 403 처리하므로 일반 브라우저 UA를 사용한다.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT

_last_request = 0.0


def polite_get(url: str, **kwargs) -> requests.Response:
    """요청 간 최소 DELAY_SECONDS를 보장하는 GET."""
    global _last_request
    wait = DELAY_SECONDS - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    kwargs.setdefault("timeout", 60)
    return session.get(requests.utils.requote_uri(url), **kwargs)


def load_manifest() -> list[dict]:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return []


def save_manifest(entries: list[dict]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def slugify(name: str, max_len: int = 80) -> str:
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:max_len] or "report"


def build_sha_index(manifest: list[dict]) -> dict[str, Path]:
    """manifest의 SHA256 → 기존 파일 경로 인덱스 (디스크에 실재하는 항목만).

    같은 PDF가 다른 URL로 재등장할 때 중복 저장을 막는다 —
    collect_smic/ewha/voera와 동일한 dedup 규약 (이 파일만 빠져 있었음).
    """
    index: dict[str, Path] = {}
    for e in manifest:
        sha, file = e.get("sha256"), e.get("file")
        if sha and file and resolve_data_path(file).exists():
            index[sha] = resolve_data_path(file)
    return index


def download_pdf(entry: dict, manifest: list[dict], known: dict[str, dict],
                 sha_index: dict[str, Path]) -> None:
    """manifest에 없을 때만 PDF를 내려받아 기록한다."""
    pdf_url = entry["pdf_url"]
    existing = known.get(pdf_url)
    if existing and existing.get("file") and existing.get("sha256"):
        path = resolve_data_path(existing["file"])
        if path.exists():
            # 이미 다운로드됨 → 재다운로드 없이 메타데이터만 갱신
            changed = False
            for key in ("title", "published_hint", "page_url", "source_id", "company_hint", "author_hint"):
                if entry.get(key) and existing.get(key) != entry[key]:
                    existing[key] = entry[key]
                    changed = True
            if changed:
                save_manifest(manifest)
            return
    target_dir = PDF_ROOT / entry["school"]
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = (entry.get("published_hint") or "undated")[:10].replace(".", "-")
    filename = slugify(f"{prefix}_{entry.get('title') or entry['source_id']}") + ".pdf"
    target = target_dir / filename
    if target.exists() and not existing:
        # 파일명이 겹치는 다른 리포트 — source_id로 구분
        target = target_dir / (slugify(f"{prefix}_{entry['source_id']}_{entry.get('title') or ''}") + ".pdf")

    try:
        resp = polite_get(pdf_url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! download failed: {pdf_url} ({exc})", flush=True)
        entry.update({"file": None, "sha256": None, "error": str(exc)})
        _record(entry, manifest, known)
        return

    body = resp.content
    if len(body) > MAX_PDF_BYTES or not body.startswith(b"%PDF"):
        print(f"  ! not a pdf or too large ({len(body)} bytes): {pdf_url}", flush=True)
        entry.update({"file": None, "sha256": None, "error": f"invalid body ({len(body)} bytes)"})
        _record(entry, manifest, known)
        return

    sha = hashlib.sha256(body).hexdigest()

    # SHA256 기준 중복 검사: 동일 PDF가 다른 URL로 오면 기존 파일을 가리키고 저장 생략
    if sha in sha_index:
        existing_path = sha_index[sha]
        print(f"  = already exists (SHA match): {existing_path.relative_to(ROOT).as_posix()}", flush=True)
        entry.update(
            {
                "file": existing_path.relative_to(ROOT).as_posix(),
                "sha256": sha,
                "size": len(body),
                "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "error": None,
                "note": "sha256_dedup",
            }
        )
        _record(entry, manifest, known)
        return

    target.write_bytes(body)
    sha_index[sha] = target
    entry.update(
        {
            "file": target.relative_to(ROOT).as_posix(),
            "sha256": sha,
            "size": len(body),
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": None,
        }
    )
    _record(entry, manifest, known)
    print(f"  + {entry['school']}/{target.name} ({len(body)//1024} KB)", flush=True)


def _record(entry: dict, manifest: list[dict], known: dict[str, dict]) -> None:
    if entry["pdf_url"] in known:
        known[entry["pdf_url"]].update(entry)
    else:
        manifest.append(entry)
        known[entry["pdf_url"]] = entry
    save_manifest(manifest)


def smart_unquote(value: str) -> str:
    """percent-인코딩을 UTF-8 우선, 실패 시 EUC-KR(cp949)로 디코드."""
    raw = unquote_to_bytes(value)
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return unquote(value, errors="replace")


def page_title(text: str) -> str | None:
    match = re.search(r"<title>([^<]+)</title>", text)
    return html.unescape(match.group(1)).strip() if match else None


# ---------------------------------------------------------------- YIG (연세대)

YIG_PDF_RE = re.compile(r"https://storage\.googleapis\.com/yighub/research-report-files/[^\"\\]+?\.pdf")
YIG_MAX_ID = 130


def collect_yig(limit: int | None, known_ids: set[str] | None = None) -> list[dict]:
    """known_ids: manifest에 파일까지 확보된 source_id 집합 — 해당 게시글은 상세
    페이지 요청 자체를 생략한다 (게시글당 2.5초 × 전 게시글 재열거가 daily CI
    45분 타임아웃의 주범이었음). --full이면 None으로 들어와 전체를 순회한다."""
    entries: list[dict] = []
    for rid in range(1, YIG_MAX_ID + 1):
        if limit and len(entries) >= limit:
            break
        if known_ids and f"yig-{rid}" in known_ids:
            continue
        url = f"https://yig.yonsei.ac.kr/research/{rid}"
        try:
            resp = polite_get(url)
        except requests.RequestException as exc:
            print(f"  ! {url}: {exc}", flush=True)
            continue
        if resp.status_code != 200:
            continue
        match = YIG_PDF_RE.search(resp.text)
        if not match:
            continue
        pdf_url = match.group(0)
        # 업로드 타임스탬프(예: 20260607_225843_064)를 발간일 힌트로 사용
        stamp = re.search(r"research-report-files/(\d{8})_", pdf_url)
        hint = None
        if stamp:
            raw = stamp.group(1)
            hint = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        entries.append(
            {
                "school": "yig",
                "source_id": f"yig-{rid}",
                "title": page_title(resp.text),
                "page_url": url,
                "pdf_url": pdf_url,
                "published_hint": hint,
            }
        )
        print(f"  found yig/{rid}: {pdf_url.rsplit('/', 1)[-1]}", flush=True)
    return entries


# ---------------------------------------------------------------- STAR (성균관대)

STAR_BASE = "http://starskku.com"
STAR_VIEW_RE = re.compile(r"board_view\?code=research&(?:amp;)?no=(\d+)")
STAR_FILE_RE = re.compile(r"/fileRequest/download\?file=[^\"'\s]+")
STAR_MAX_PAGES = 60


def collect_star(limit: int | None, known_ids: set[str] | None = None) -> list[dict]:
    """known_ids 동작은 collect_yig와 동일 — 이미 확보된 게시글의 상세 페이지
    요청을 생략하고, 목록 페이지도 전부 아는 글뿐이면 조기 종료한다
    (게시판은 최신순이므로 그 뒤 페이지에 새 글이 있을 수 없다)."""
    post_ids: list[int] = []
    seen: set[int] = set()
    for page in range(1, STAR_MAX_PAGES + 1):
        # CodeIgniter 게시판: 페이지 번호 파라미터 이름이 per_page다
        url = f"{STAR_BASE}/board/board_list?code=research&per_page={page}"
        try:
            resp = polite_get(url)
        except requests.RequestException as exc:
            print(f"  ! {url}: {exc}", flush=True)
            break
        ids = [int(n) for n in STAR_VIEW_RE.findall(resp.text)]
        fresh = [n for n in ids if n not in seen]
        if not fresh:
            break
        seen.update(fresh)
        post_ids.extend(n for n in fresh if not (known_ids and f"star-{n}" in known_ids))
        print(f"  star list page {page}: +{len(fresh)} posts", flush=True)
        if known_ids and ids and all(f"star-{n}" in known_ids for n in ids):
            break  # 이 페이지 전체가 기수집분 — 증분 조기 종료

    entries: list[dict] = []
    for no in post_ids:
        if limit and len(entries) >= limit:
            break
        url = f"{STAR_BASE}/board/board_view?code=research&no={no}"
        try:
            resp = polite_get(url)
        except requests.RequestException as exc:
            print(f"  ! {url}: {exc}", flush=True)
            continue
        text = html.unescape(resp.text)
        for raw in STAR_FILE_RE.findall(text):
            query = parse_qs(urlparse(raw).query)
            save_name = smart_unquote(query.get("save", [""])[0])
            if not save_name.lower().endswith(".pdf"):
                continue
            file_param = unquote(query.get("file", [""])[0])
            stamp = re.search(r"/(\d{8})\d*_", file_param)
            hint = None
            if stamp:
                raw_date = stamp.group(1)
                hint = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            entries.append(
                {
                    "school": "star",
                    "source_id": f"star-{no}",
                    "title": save_name[:-4],
                    "page_url": url,
                    "pdf_url": urljoin(STAR_BASE, raw),
                    "published_hint": hint,
                }
            )
            print(f"  found star/{no}: {save_name}", flush=True)
    return entries


# ---------------------------------------------------------------- KUVIC (고려대)

KUVIC_MAX_PAGES = 30
KUVIC_DATE_RE = re.compile(r"(20\d{2})[.\-/]\s?(\d{1,2})[.\-/]\s?(\d{1,2})")


def _kuvic_parse_item(item) -> dict | None:
    """Wix repeater listitem 하나에서 회사명·제목·작성자·작성일·PDF 링크를 짝지어 추출."""
    link = item.select_one('a[href*="_files/ugd"]')
    if not link or not link["href"].lower().endswith(".pdf"):
        return None
    texts = []
    for node in item.select('[data-testid="richTextElement"]'):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True).replace("\xa0", " ")).strip()
        if text:
            texts.append(text)

    def value_after(label: str) -> str | None:
        for i, text in enumerate(texts):
            if text.replace(" ", "") == label and i + 1 < len(texts):
                return texts[i + 1]
        return None

    company = texts[0] if texts else None
    title = value_after("제목")
    team = value_after("작성자")
    date_raw = value_after("작성일")
    hint = None
    if date_raw:
        match = KUVIC_DATE_RE.search(date_raw)
        if match:
            hint = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return {
        "school": "kuvic",
        "source_id": f"kuvic-{link['href'].rsplit('/', 1)[-1].removesuffix('.pdf')}",
        "title": " - ".join(t for t in (company, title) if t) or None,
        "company_hint": company,
        "author_hint": team,
        "page_url": None,  # caller sets
        "pdf_url": link["href"],
        "published_hint": hint,
    }


def collect_kuvic(limit: int | None) -> list[dict]:
    from bs4 import BeautifulSoup

    entries: list[dict] = []
    seen: set[str] = set()
    for page in range(1, KUVIC_MAX_PAGES + 1):
        if limit and len(entries) >= limit:
            break
        url = f"https://www.kuvic.com/research?page={page}"
        try:
            resp = polite_get(url)
        except requests.RequestException as exc:
            print(f"  ! {url}: {exc}", flush=True)
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        fresh = 0
        for item in soup.select('div[role="listitem"]'):
            parsed = _kuvic_parse_item(item)
            if not parsed or parsed["pdf_url"] in seen:
                continue
            seen.add(parsed["pdf_url"])
            parsed["page_url"] = url
            entries.append(parsed)
            fresh += 1
        if fresh == 0:
            break
        print(f"  kuvic page {page}: +{fresh} reports", flush=True)
    return entries[:limit] if limit else entries


def collect_kuvic_items(limit: int | None) -> list[dict]:
    """collect_kuvic_browser.py가 수확한 전체 목록(JSON)을 읽는다."""
    path = BRONZE_KUVIC_ITEMS
    if not path.exists():
        print("  ! kuvic_items.json 없음 — scripts/collect_kuvic_browser.py 먼저 실행", flush=True)
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    return items[:limit] if limit else items


# ---------------------------------------------------------------- main

# kuvic 주의: Wix 서버가 ?page=N을 무시하므로 collect_kuvic은 사실상 1페이지(최신글)
# 전용 증분 수집기다. 전체 스윕은 collect_kuvic_browser.py → kuvic-items 경로가 담당.
COLLECTORS = {"yig": collect_yig, "star": collect_star, "kuvic": collect_kuvic, "kuvic-items": collect_kuvic_items}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=[*COLLECTORS, "all"], required=True)
    parser.add_argument("--limit", type=int, default=None, help="소스당 최대 수집 건수 (테스트용)")
    parser.add_argument("--full", action="store_true",
                        help="기수집 게시글도 전부 재방문 (복구용 — daily는 증분 모드)")
    args = parser.parse_args()

    manifest = load_manifest()
    known = {e["pdf_url"]: e for e in manifest}
    sha_index = build_sha_index(manifest)
    sources = list(COLLECTORS) if args.source == "all" else [args.source]

    # 증분 모드: "완전히 처리된" source_id의 상세 페이지 요청을 생략 —
    # yig/star가 매일 전 게시글을 2.5초 간격으로 재열거하며 CI 45분
    # 타임아웃을 유발하던 것의 수정 (2026-06-11~12 daily run 연속 사망 원인).
    #
    # 완전 처리 = manifest에 파일 기록 AND 전사 markdown 존재.
    # PDF는 gitignore라 CI 러너에 없다 — 전사가 안 끝난 게시글을 건너뛰면
    # 재다운로드 경로가 사라져 영구 누락된다 (markdown까지 확인하는 이유).
    known_ids: set[str] | None = None
    if not args.full:
        md_root = SILVER_MARKDOWN
        known_ids = {
            e["source_id"] for e in manifest
            if e.get("file") and e.get("source_id")
            and (md_root / e["school"] / f"{Path(e['file']).stem}.md").exists()
        }

    for source in sources:
        print(f"== collecting {source} ==", flush=True)
        if source in ("yig", "star"):
            entries = COLLECTORS[source](args.limit, known_ids=known_ids)
        else:
            entries = COLLECTORS[source](args.limit)
        print(f"== {source}: {len(entries)} reports discovered ==", flush=True)
        for entry in entries:
            download_pdf(entry, manifest, known, sha_index)

    ok = sum(1 for e in manifest if e.get("file"))
    failed = sum(1 for e in manifest if e.get("error"))
    print(f"== done: {ok} pdfs in manifest, {failed} errors ==", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
