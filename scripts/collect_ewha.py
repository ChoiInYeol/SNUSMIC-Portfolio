"""이화여자대학교 가치투자학회 EIA 리서치 리포트 PDF 수집기.

원칙:
- 모든 HTTP 요청 사이에 DELAY_SECONDS 지연을 두어 원 서버에 부하를 주지 않는다.
- 각 PDF는 1회만 다운로드한다: data/bronze/reports/manifest.json에 기록하고,
  재실행 시 manifest에 있고 파일이 존재하면 건너뛴다.
- SHA256 중복 검사: 기존에 다른 경로로 수집된 동일 파일은 재다운로드하지 않는다.
- 증분 수집: 기본적으로 목록 첫 페이지에서 이미 알려진 항목을 만나면 조기 종료한다.
  --full 플래그로 전체 페이지를 순회할 수 있다.

사이트: https://ewhainvest.com/research
  - imweb 호스팅, 서버 사이드 렌더링 (plain requests 작동)
  - 목록 페이지: ?page=N (N=1..10)
  - 상세 페이지: ?bmode=view&idx=<ID>&t=board
  - 파일 다운로드: /post_file_download.cm?c=<base64>
  - 제목 형식: [YYMMDD] 종목명(티커) 또는 유사

사용:
    python scripts/collect_ewha.py [--limit N] [--full]
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
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

from lake import BRONZE_PDFS, BRONZE_REPORT_MANIFEST, resolve_data_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
PDF_ROOT = BRONZE_PDFS
MANIFEST_PATH = BRONZE_REPORT_MANIFEST

DELAY_SECONDS = 2.5
MAX_PDF_BYTES = 120 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

EWHA_BASE = "https://ewhainvest.com"
EWHA_LIST = EWHA_BASE + "/research"
EWHA_MAX_PAGES = 20  # safety ceiling; currently ~10 pages

# [YYMMDD] or [YYYYMMDD] prefix in titles
_DATE_BRACKET_RE = re.compile(r"\[(\d{6}|\d{8})\]")
# Post ID pattern in listing HTML
_POST_ID_RE = re.compile(r"bmode=view.*?idx=([0-9]+)")
# Download link c= param
_DL_C_RE = re.compile(r"post_file_download\.cm\?c=([^\"'<\s&]+)")
# PDF display filename in <p class="tit">
_PDF_NAME_RE = re.compile(r'<p class="tit">([^<]+\.pdf)</p>', re.IGNORECASE)

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
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:max_len] or "report"


def build_sha_index() -> dict[str, Path]:
    """data/bronze/reports/pdfs/ 아래 모든 PDF의 SHA256 → Path 인덱스를 구축한다."""
    index: dict[str, Path] = {}
    for p in PDF_ROOT.rglob("*.pdf"):
        try:
            data = p.read_bytes()
            index[hashlib.sha256(data).hexdigest()] = p
        except OSError:
            pass
    return index


def _record(entry: dict, manifest: list[dict], known_urls: dict[str, dict]) -> None:
    if entry["pdf_url"] in known_urls:
        known_urls[entry["pdf_url"]].update(entry)
    else:
        manifest.append(entry)
        known_urls[entry["pdf_url"]] = entry
    save_manifest(manifest)


def _parse_date_hint(title: str) -> str | None:
    """제목에서 날짜 힌트를 추출한다. [YYMMDD] 또는 [YYYYMMDD] 형식."""
    m = _DATE_BRACKET_RE.search(title)
    if not m:
        return None
    raw = m.group(1)
    if len(raw) == 6:
        # YYMMDD → assume 20xx
        yy, mm, dd = raw[:2], raw[2:4], raw[4:]
        return f"20{yy}-{mm}-{dd}"
    # YYYYMMDD
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _parse_ticker(title: str) -> str | None:
    """제목에서 티커 힌트를 추출한다. 예: S-Oil(010950) → 010950."""
    m = re.search(r"\(([0-9A-Z]{5,6})\)", title)
    return m.group(1) if m else None


def _fetch_listing_page(page_num: int) -> list[str]:
    """목록 페이지에서 중복 없는 순서화된 post ID 목록을 반환한다."""
    url = EWHA_LIST if page_num == 1 else f"{EWHA_LIST}?page={page_num}"
    try:
        resp = polite_get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! listing page {page_num} failed: {exc}", flush=True)
        return []
    text = html.unescape(resp.text)
    seen: dict[str, bool] = {}
    for pid in _POST_ID_RE.findall(text):
        seen[pid] = True
    return list(seen.keys())


def _fetch_detail(post_id: str) -> dict | None:
    """상세 페이지에서 메타데이터와 PDF 다운로드 URL을 추출한다."""
    url = f"{EWHA_LIST}?bmode=view&idx={post_id}&t=board"
    try:
        resp = polite_get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! detail {post_id} failed: {exc}", flush=True)
        return None
    text = html.unescape(resp.text)

    # Title from <title> tag: "[260529] 종목명(티커) : 이화여자대학교 가치투자학회 EIA"
    title_m = re.search(r"<title>([^<]+)</title>", text)
    raw_title = title_m.group(1).strip() if title_m else ""
    # Strip site suffix after " : "
    title = re.sub(r"\s*:\s*이화여자대학교.*$", "", raw_title).strip()

    # Download URL: /post_file_download.cm?c=<base64>
    c_vals = _DL_C_RE.findall(text)
    if not c_vals:
        print(f"  ! no download link on post {post_id}", flush=True)
        return None

    # PDF display name (may have multiple files; take first .pdf)
    pdf_names = _PDF_NAME_RE.findall(text)

    results = []
    for i, cv in enumerate(c_vals):
        cv_decoded = urllib.parse.unquote(cv)
        pdf_url = EWHA_BASE + "/post_file_download.cm?c=" + cv_decoded
        display_name = pdf_names[i] if i < len(pdf_names) else ""
        results.append((pdf_url, display_name))

    if not results:
        return None

    # Use first PDF (reports typically have one attachment)
    pdf_url, display_name = results[0]

    # Date hint from title bracket
    published_hint = _parse_date_hint(title)
    ticker_hint = _parse_ticker(title)

    return {
        "school": "ewha",
        "source_id": f"ewha-{post_id}",
        "title": title or display_name.replace(".pdf", ""),
        "company_hint": ticker_hint,
        "page_url": url,
        "pdf_url": pdf_url,
        "published_hint": published_hint,
    }


def download_pdf(
    entry: dict,
    manifest: list[dict],
    known_urls: dict[str, dict],
    sha_index: dict[str, Path],
) -> None:
    """manifest에 없을 때만 PDF를 내려받아 기록한다."""
    pdf_url = entry["pdf_url"]

    existing = known_urls.get(pdf_url)
    if existing:
        path = resolve_data_path(existing["file"]) if existing.get("file") else None
        if path and path.exists():
            changed = False
            for key in ("title", "published_hint", "page_url", "source_id", "company_hint"):
                if entry.get(key) and existing.get(key) != entry[key]:
                    existing[key] = entry[key]
                    changed = True
            if changed:
                save_manifest(manifest)
            return

    target_dir = PDF_ROOT / "ewha"
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        resp = polite_get(pdf_url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! download failed: {pdf_url} ({exc})", flush=True)
        entry.update({"file": None, "sha256": None, "error": str(exc)})
        _record(entry, manifest, known_urls)
        return

    body = resp.content
    if len(body) > MAX_PDF_BYTES or not body.startswith(b"%PDF"):
        print(f"  ! not a pdf or too large ({len(body)} bytes): {pdf_url}", flush=True)
        entry.update({"file": None, "sha256": None, "error": f"invalid body ({len(body)} bytes)"})
        _record(entry, manifest, known_urls)
        return

    sha = hashlib.sha256(body).hexdigest()
    if sha in sha_index:
        existing_path = sha_index[sha]
        print(f"  = sha256 dedup: {existing_path.relative_to(ROOT).as_posix()}", flush=True)
        entry.update({
            "file": existing_path.relative_to(ROOT).as_posix(),
            "sha256": sha,
            "size": len(body),
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": None,
            "note": "sha256_dedup",
        })
        _record(entry, manifest, known_urls)
        return

    prefix = (entry.get("published_hint") or "undated")[:10].replace(".", "-")
    filename = slugify(f"{prefix}_{entry.get('title') or entry['source_id']}") + ".pdf"
    target = target_dir / filename
    if target.exists():
        target = target_dir / (slugify(f"{prefix}_{entry['source_id']}") + ".pdf")

    target.write_bytes(body)
    sha_index[sha] = target
    entry.update({
        "file": target.relative_to(ROOT).as_posix(),
        "sha256": sha,
        "size": len(body),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": None,
    })
    _record(entry, manifest, known_urls)
    print(f"  + ewha/{target.name} ({len(body) // 1024} KB)", flush=True)


def collect_ewha(limit: int | None, full: bool) -> list[dict]:
    """EIA 게시글 목록을 순회하며 PDF를 수집한다.

    증분 모드(기본): 한 페이지의 모든 post ID가 이미 manifest에 있으면 조기 종료.
    --full 모드: 전체 페이지를 순회한다.
    """
    manifest = load_manifest()
    known_urls: dict[str, dict] = {e["pdf_url"]: e for e in manifest if e.get("pdf_url")}
    # Build known source_id set for fast incremental check
    known_source_ids: set[str] = {e["source_id"] for e in manifest if e.get("source_id")}
    sha_index = build_sha_index()

    print(f"  manifest: {len(manifest)} entries, sha_index: {len(sha_index)} PDFs", flush=True)

    new_count = 0
    skipped_count = 0
    error_count = 0
    total_discovered = 0

    for page_num in range(1, EWHA_MAX_PAGES + 1):
        if limit is not None and (new_count + skipped_count) >= limit:
            break

        print(f"  fetching listing page {page_num} ...", flush=True)
        post_ids = _fetch_listing_page(page_num)
        if not post_ids:
            print(f"  page {page_num}: empty — done", flush=True)
            break

        total_discovered += len(post_ids)

        # Incremental early-stop check
        page_source_ids = {f"ewha-{pid}" for pid in post_ids}
        page_all_known = page_source_ids.issubset(known_source_ids)

        print(
            f"  page {page_num}: {len(post_ids)} posts"
            f" ({sum(1 for pid in post_ids if f'ewha-{pid}' not in known_source_ids)} new)",
            flush=True,
        )

        for pid in post_ids:
            if limit is not None and (new_count + skipped_count) >= limit:
                break
            source_id = f"ewha-{pid}"
            if source_id in known_source_ids:
                # Check if file actually exists
                existing = next((e for e in manifest if e.get("source_id") == source_id), None)
                if existing and existing.get("file") and (resolve_data_path(existing["file"])).exists():
                    skipped_count += 1
                    continue
            # Fetch detail page
            entry = _fetch_detail(pid)
            if entry is None:
                error_count += 1
                continue
            known_source_ids.add(source_id)
            download_pdf(entry, manifest, known_urls, sha_index)
            if entry.get("error"):
                error_count += 1
            else:
                new_count += 1

        if not full and page_all_known:
            print(
                f"  page {page_num}: all entries already in manifest — stopping early"
                " (use --full to walk all pages)",
                flush=True,
            )
            break

    print(
        f"  ewha collection done: {new_count} processed, {skipped_count} skipped,"
        f" {error_count} errors",
        flush=True,
    )
    return [e for e in manifest if e.get("school") == "ewha"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 건수 (테스트용)")
    parser.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="전체 페이지 순회 (기본: 기존 항목 발견 시 조기 종료)",
    )
    args = parser.parse_args()

    print("== collecting ewha ==", flush=True)
    entries = collect_ewha(limit=args.limit, full=args.full)

    manifest = load_manifest()
    ok = sum(1 for e in manifest if e.get("file") and e.get("school") == "ewha")
    failed = sum(1 for e in manifest if e.get("error") and e.get("school") == "ewha")
    print(f"== ewha: {len(entries)} total entries, {ok} with files, {failed} errors ==", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
