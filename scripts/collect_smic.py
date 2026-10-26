"""서울대 SMIC 리서치 리포트 PDF 수집기.

원칙:
- 모든 HTTP 요청 사이에 DELAY_SECONDS 지연을 두어 원 서버에 부하를 주지 않는다.
- 각 PDF는 1회만 다운로드한다: data/bronze/reports/manifest.json에 기록하고,
  재실행 시 manifest에 URL이 있고 파일이 존재하면 건너뛴다.
- SHA256 중복 검사: 기존에 다른 경로로 수집된 동일 파일은 재다운로드하지 않는다.
- 증분 수집: 기본적으로 목록 첫 페이지(들)에서 이미 알려진 항목을 만나면 조기 종료한다.
  --full 플래그로 전체 789건을 순회할 수 있다.

사용:
    python scripts/collect_smic.py [--limit N] [--full]

데이터 소스: http://snusmic.com/wp-json/wp/v2/posts
  - WordPress REST API, 최신순 정렬 (newest-first)
  - 각 게시글 content에 wp-content/uploads/... .pdf 링크 1개
  - 총 789개 게시글, 79페이지 (10개/페이지)
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
from urllib.parse import urljoin

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

SMIC_BASE = "http://snusmic.com"
SMIC_API = f"{SMIC_BASE}/wp-json/wp/v2/posts"
SMIC_PER_PAGE = 10
SMIC_MAX_PAGES = 100  # safety ceiling; actual total ~79 pages

PDF_RE = re.compile(r'href=["\']([^"\']*\.pdf)["\']')

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


def extract_pdf_url(content_html: str) -> str | None:
    """게시글 HTML 본문에서 PDF 다운로드 URL을 추출한다."""
    links = PDF_RE.findall(html.unescape(content_html))
    for link in links:
        if link.startswith("http"):
            return link
        if link.startswith("/"):
            return urljoin(SMIC_BASE, link)
    return None


def fetch_posts_page(page: int) -> tuple[list[dict], int]:
    """WP REST API에서 게시글 목록 한 페이지를 가져온다.

    Returns:
        (posts, total_pages)
    """
    for attempt in range(3):
        try:
            resp = polite_get(SMIC_API, headers={"Accept": "application/json"}, params={
                "per_page": SMIC_PER_PAGE, "page": page, "_fields": "id,date,title,link,content",
            })
            resp.raise_for_status()
            posts = resp.json()
            total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
            if not isinstance(posts, list) or (page == 1 and not posts) or total_pages < 1:
                raise ValueError("invalid/empty SMIC WordPress page")
            for post in posts:
                if not isinstance(post, dict) or type(post.get("id")) is not int or \
                        not isinstance(post.get("date"), str) or not isinstance(post.get("link"), str) or \
                        not isinstance(post.get("title"), dict) or not isinstance(post["title"].get("rendered"), str) or \
                        not isinstance(post.get("content"), dict) or not isinstance(post["content"].get("rendered"), str):
                    raise ValueError("invalid SMIC post fields")
                datetime.fromisoformat(post["date"])
            return posts, total_pages
        except (requests.RequestException, ValueError) as exc:
            if attempt == 2:
                raise requests.RequestException(f"SMIC page {page} unavailable after 3 attempts") from exc
            print(f"  ! page {page}: invalid/unavailable response, retrying ({attempt + 1}/3)", flush=True)
    raise AssertionError("unreachable")


def download_pdf(
    entry: dict,
    manifest: list[dict],
    known_urls: dict[str, dict],
    sha_index: dict[str, Path],
) -> None:
    """manifest에 없을 때만 PDF를 내려받아 기록한다.

    SHA256 중복 검사를 통해 이미 다른 이름으로 존재하는 파일은 재다운로드하지 않는다.
    """
    pdf_url = entry["pdf_url"]

    # URL 기준 중복 검사
    existing = known_urls.get(pdf_url)
    if existing:
        path = resolve_data_path(existing["file"]) if existing.get("file") else None
        if path and path.exists():
            # 메타데이터만 갱신
            changed = False
            for key in ("title", "published_hint", "page_url", "source_id"):
                if entry.get(key) and existing.get(key) != entry[key]:
                    existing[key] = entry[key]
                    changed = True
            if changed:
                save_manifest(manifest)
            return

    target_dir = PDF_ROOT / "smic"
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

    # SHA256 기준 중복 검사: 기존 파일과 동일하면 파일 저장 생략
    if sha in sha_index:
        existing_path = sha_index[sha]
        print(
            f"  = already exists (SHA match): {existing_path.relative_to(ROOT).as_posix()}",
            flush=True,
        )
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
        _record(entry, manifest, known_urls)
        sha_index[sha] = existing_path  # keep existing path in index
        return

    # 신규 파일 저장
    prefix = (entry.get("published_hint") or "undated")[:10].replace(".", "-")
    filename = slugify(f"{prefix}_{entry.get('title') or entry['source_id']}") + ".pdf"
    target = target_dir / filename
    if target.exists():
        target = target_dir / (slugify(f"{prefix}_{entry['source_id']}") + ".pdf")

    target.write_bytes(body)
    entry.update(
        {
            "file": target.relative_to(ROOT).as_posix(),
            "sha256": sha,
            "size": len(body),
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": None,
        }
    )
    sha_index[sha] = target
    _record(entry, manifest, known_urls)
    print(f"  + smic/{target.name} ({len(body) // 1024} KB)", flush=True)


def _record(entry: dict, manifest: list[dict], known_urls: dict[str, dict]) -> None:
    if entry["pdf_url"] in known_urls:
        known_urls[entry["pdf_url"]].update(entry)
    else:
        manifest.append(entry)
        known_urls[entry["pdf_url"]] = entry
    save_manifest(manifest)


def collect_smic(limit: int | None, full: bool) -> list[dict]:
    """SMIC 게시글 목록을 WP REST API로 순회하며 엔트리를 수집한다.

    증분 모드(기본): 한 페이지의 모든 URL이 이미 manifest에 있으면 조기 종료.
    --full 모드: 전체 페이지를 순회한다.
    """
    manifest = load_manifest()
    known_urls: dict[str, dict] = {e["pdf_url"]: e for e in manifest if e.get("pdf_url")}
    sha_index = build_sha_index()

    print(f"  manifest: {len(manifest)} entries, sha_index: {len(sha_index)} PDFs", flush=True)

    total_pages = SMIC_MAX_PAGES
    new_count = 0
    skipped_count = 0
    error_count = 0

    for page in range(1, SMIC_MAX_PAGES + 1):
        if limit is not None and (new_count + skipped_count) >= limit:
            break
        if page > total_pages:
            break

        print(f"  fetching page {page}/{total_pages} ...", flush=True)
        try:
            posts, total_pages = fetch_posts_page(page)
        except requests.RequestException as exc:
            raise RuntimeError(f"SMIC collection incomplete: {exc}") from exc

        if not posts:
            print(f"  page {page}: empty — done", flush=True)
            break

        page_all_known = True
        page_entries: list[dict] = []

        for post in posts:
            content_html = post.get("content", {}).get("rendered", "")
            pdf_url = extract_pdf_url(content_html)
            if not pdf_url:
                continue

            title = html.unescape(post["title"]["rendered"]).strip()
            published_hint = post["date"][:10]  # WP date is accurate for recent posts
            source_id = f"smic-{post['id']}"
            page_url = post.get("link", "")

            if pdf_url not in known_urls:
                page_all_known = False

            entry = {
                "school": "smic",
                "source_id": source_id,
                "title": title,
                "page_url": page_url,
                "pdf_url": pdf_url,
                "published_hint": published_hint,
            }
            page_entries.append(entry)

        print(
            f"  page {page}: {len(page_entries)} posts"
            f" ({sum(1 for e in page_entries if e['pdf_url'] not in known_urls)} new)",
            flush=True,
        )

        # Process entries on this page
        for entry in page_entries:
            if limit is not None and (new_count + skipped_count) >= limit:
                break
            pdf_url = entry["pdf_url"]
            if pdf_url in known_urls:
                existing = known_urls[pdf_url]
                if existing.get("file") and (resolve_data_path(existing["file"])).exists():
                    skipped_count += 1
                    continue
            download_pdf(entry, manifest, known_urls, sha_index)
            if entry.get("error"):
                error_count += 1
            elif entry.get("note") == "sha256_dedup" or (
                entry.get("file") and not entry.get("error")
            ):
                new_count += 1

        # Incremental early-stop: if not --full and entire page was already known
        if not full and page_all_known:
            print(
                f"  page {page}: all entries already in manifest — stopping early (use --full to walk all pages)",
                flush=True,
            )
            break

    print(
        f"  smic collection done: {new_count} processed, {skipped_count} skipped (already in manifest), {error_count} errors",
        flush=True,
    )
    if error_count:
        raise RuntimeError(f"SMIC collection incomplete: {error_count} PDF download failures")
    return [e for e in manifest if e.get("school") == "smic"]


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

    print("== collecting smic ==", flush=True)
    try:
        entries = collect_smic(limit=args.limit, full=args.full)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1

    manifest = load_manifest()
    ok = sum(1 for e in manifest if e.get("file") and e.get("school") == "smic")
    failed = sum(1 for e in manifest if e.get("error") and e.get("school") == "smic")
    print(f"== smic: {len(entries)} total entries, {ok} with files, {failed} errors ==", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
