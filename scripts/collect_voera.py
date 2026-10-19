"""홍익대학교 중앙 금융동아리 Voera 리서치 리포트 PDF 수집기.

원칙:
- 모든 요청/페이지 전환 사이에 DELAY_SECONDS 지연을 두어 원 서버에 부하를 주지 않는다.
- 각 PDF는 1회만 다운로드한다: data/bronze/reports/manifest.json에 기록하고,
  재실행 시 manifest에 있고 파일이 존재하면 건너뛴다.
- SHA256 중복 검사: 기존에 다른 경로로 수집된 동일 파일은 재다운로드하지 않는다.
- 증분 수집: 기본적으로 목록 첫 페이지에서 이미 알려진 항목을 만나면 조기 종료한다.
  --full 플래그로 전체 페이지를 순회할 수 있다.

사이트: https://www.voera.co.kr/Research
  - imweb 호스팅, JS 렌더링 → Playwright 사용
  - 목록 페이지네이션: li.tpl-forum-page data-page-num="N" 버튼 클릭 (총 ~13페이지)
  - 상세 페이지: /forum/view/<ID>
  - PDF: href="//storage.googleapis.com/cr-resource/forum/<hash>/<hash>.pdf"
  - 제목 형식: [BUY/HOLD/SELL] 기업명 TP: N원 (rating + TP in title)

사용:
    python scripts/collect_voera.py [--limit N] [--full]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
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

VOERA_BASE = "https://www.voera.co.kr"
VOERA_LIST = VOERA_BASE + "/Research"
VOERA_MAX_PAGES = 20  # safety ceiling; currently ~13 pages

# Patterns
_FORUM_ID_RE = re.compile(r"/forum/view/([0-9]+)")
_GCS_PDF_RE = re.compile(r'href="((?:https?:)?//storage\.googleapis\.com/cr-resource/forum/[^"]+\.pdf)"')
_DATE_META_RE = re.compile(r'<meta[^>]+name="date"[^>]+content="([^"]+)"', re.IGNORECASE)
_TITLE_META_RE = re.compile(r'<meta[^>]+name="title"[^>]+content="([^"]+)"', re.IGNORECASE)
_RATING_RE = re.compile(r"\[(BUY|HOLD|SELL|REDUCE|NEUTRAL|TRADING BUY)[^\]]*\]", re.IGNORECASE)
_TP_RE = re.compile(r"TP\s*:\s*([0-9,]+)\s*원")

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


def _normalise_pdf_url(href: str) -> str:
    """Protocol-relative //storage... → https://storage..."""
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_detail_html(post_id: str, text: str) -> dict | None:
    """렌더링된 상세 페이지 HTML에서 메타데이터와 PDF URL을 추출한다."""
    url = f"{VOERA_BASE}/forum/view/{post_id}"

    # Title from meta name="title"
    tm = _TITLE_META_RE.search(text)
    title = tm.group(1).strip() if tm else ""

    # PDF link (protocol-relative anchor href)
    pdf_matches = _GCS_PDF_RE.findall(text)
    if not pdf_matches:
        print(f"  ! no PDF on post {post_id}: {url}", flush=True)
        return None
    pdf_url = _normalise_pdf_url(pdf_matches[0])

    # Rating hint from title bracket
    rating_m = _RATING_RE.search(title)
    rating_hint = rating_m.group(1).upper() if rating_m else None

    # TP hint
    tp_m = _TP_RE.search(title)
    tp_hint = tp_m.group(1).replace(",", "") if tp_m else None

    # Date from meta name="date"
    date_m = _DATE_META_RE.search(text)
    published_hint: str | None = None
    if date_m:
        raw = date_m.group(1)[:10]
        published_hint = raw if re.match(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", raw) else None

    if not published_hint:
        og_m = re.search(
            r'property="article:published_time"[^>]+content="([^"]+)"', text
        )
        if og_m:
            published_hint = og_m.group(1)[:10]

    return {
        "school": "voera",
        "source_id": f"voera-{post_id}",
        "title": title,
        "rating_hint": rating_hint,
        "tp_hint": tp_hint,
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
            for key in ("title", "published_hint", "page_url", "source_id", "rating_hint", "tp_hint"):
                if entry.get(key) and existing.get(key) != entry[key]:
                    existing[key] = entry[key]
                    changed = True
            if changed:
                save_manifest(manifest)
            return

    target_dir = PDF_ROOT / "voera"
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
    print(f"  + voera/{target.name} ({len(body) // 1024} KB)", flush=True)


def collect_voera(limit: int | None, full: bool) -> list[dict]:
    """Voera 게시글 목록을 Playwright로 순회하며 PDF를 수집한다.

    상세 페이지도 JS 렌더링이므로 단일 Playwright 세션으로 목록 탐색 + 상세 조회를 모두 처리한다.
    """
    from playwright.sync_api import sync_playwright

    manifest = load_manifest()
    known_urls: dict[str, dict] = {e["pdf_url"]: e for e in manifest if e.get("pdf_url")}
    known_source_ids: set[str] = {e["source_id"] for e in manifest if e.get("source_id")}
    sha_index = build_sha_index()

    print(f"  manifest: {len(manifest)} entries, sha_index: {len(sha_index)} PDFs", flush=True)

    new_count = 0
    skipped_count = 0
    error_count = 0
    processed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pw_page = browser.new_page(
            user_agent=USER_AGENT, viewport={"width": 1440, "height": 2200}
        )

        # ---- Phase 1: collect post IDs from all listing pages ----
        print("  phase 1: collecting post IDs via Playwright ...", flush=True)
        all_ids: list[str] = []
        seen_ids: set[str] = set()

        pw_page.goto(VOERA_LIST, wait_until="networkidle", timeout=60_000)
        time.sleep(DELAY_SECONDS)

        for page_num in range(1, VOERA_MAX_PAGES + 1):
            html_content = pw_page.content()
            page_ids = list(dict.fromkeys(_FORUM_ID_RE.findall(html_content)))
            fresh = [pid for pid in page_ids if pid not in seen_ids]
            seen_ids.update(fresh)
            all_ids.extend(fresh)

            print(
                f"  voera list page {page_num}: +{len(fresh)} posts (total {len(all_ids)})",
                flush=True,
            )

            # Incremental early-stop
            if not full:
                page_source_ids = {f"voera-{pid}" for pid in page_ids}
                if page_source_ids and page_source_ids.issubset(known_source_ids):
                    print(
                        f"  page {page_num}: all entries already in manifest — stopping early"
                        " (use --full to walk all pages)",
                        flush=True,
                    )
                    break

            next_num = page_num + 1
            next_sel = f'li.tpl-forum-page[data-page-num="{next_num}"]'
            try:
                next_btn = pw_page.locator(next_sel).first
                if not next_btn.is_visible(timeout=2000):
                    print("  no more pages", flush=True)
                    break
                next_btn.click(timeout=5000)
                time.sleep(DELAY_SECONDS)
                pw_page.wait_for_load_state("networkidle", timeout=15_000)
                time.sleep(1.0)
            except Exception as exc:
                print(f"  pagination stop at page {page_num}: {exc}", flush=True)
                break

        if limit is not None:
            all_ids = all_ids[:limit]

        print(f"  discovered {len(all_ids)} post IDs total", flush=True)

        # ---- Phase 2: visit each detail page in same browser session ----
        print("  phase 2: fetching details and downloading PDFs ...", flush=True)

        for pid in all_ids:
            source_id = f"voera-{pid}"
            if source_id in known_source_ids:
                existing = next(
                    (e for e in manifest if e.get("source_id") == source_id), None
                )
                if existing and existing.get("file") and (resolve_data_path(existing["file"])).exists():
                    skipped_count += 1
                    continue

            detail_url = f"{VOERA_BASE}/forum/view/{pid}"
            try:
                pw_page.goto(detail_url, wait_until="networkidle", timeout=60_000)
                time.sleep(DELAY_SECONDS)
                detail_html = pw_page.content()
            except Exception as exc:
                print(f"  ! detail {pid} nav failed: {exc}", flush=True)
                error_count += 1
                continue

            entry = _parse_detail_html(pid, detail_html)
            if entry is None:
                error_count += 1
                continue

            known_source_ids.add(source_id)
            download_pdf(entry, manifest, known_urls, sha_index)
            if entry.get("error"):
                error_count += 1
            else:
                new_count += 1

            processed += 1
            if limit is not None and processed >= limit:
                break

        browser.close()

    print(
        f"  voera collection done: {new_count} processed, {skipped_count} skipped,"
        f" {error_count} errors",
        flush=True,
    )
    return [e for e in manifest if e.get("school") == "voera"]


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

    print("== collecting voera ==", flush=True)
    entries = collect_voera(limit=args.limit, full=args.full)

    manifest = load_manifest()
    ok = sum(1 for e in manifest if e.get("file") and e.get("school") == "voera")
    failed = sum(1 for e in manifest if e.get("error") and e.get("school") == "voera")
    print(f"== voera: {len(entries)} total entries, {ok} with files, {failed} errors ==", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
