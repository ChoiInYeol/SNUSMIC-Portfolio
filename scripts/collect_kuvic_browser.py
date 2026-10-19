"""KUVIC(고려대) 리서치 목록을 헤드리스 브라우저로 수확한다.

Wix가 페이지네이션을 클라이언트 JS로 처리해 ?page=N이 서버에서 무시되므로,
실제 브라우저로 페이지 버튼을 눌러가며 전체(~108건) 메타데이터를 수집한다.
PDF 다운로드는 하지 않는다 → 결과를 data/bronze/reports/kuvic_items.json에 저장하고,
다운로드는 collect_reports.py --source kuvic-items 가 매니페스트 규칙대로 수행한다.

사용:
    python scripts/collect_kuvic_browser.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.kuvic.com/research"
PAGE_DELAY = 2.5
MAX_PAGES = 40

sys.path.insert(0, str(ROOT / "scripts"))
from lake import BRONZE_KUVIC_ITEMS as OUT_PATH  # noqa: E402
from collect_reports import USER_AGENT, _kuvic_parse_item  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def harvest_current_page(page) -> list[dict]:
    soup = BeautifulSoup(page.content(), "html.parser")
    items = []
    for node in soup.select('div[role="listitem"]'):
        parsed = _kuvic_parse_item(node)
        if parsed:
            parsed["page_url"] = URL
            items.append(parsed)
    return items


def goto_next_page(page, next_number: int) -> bool:
    """페이지네이션에서 숫자 버튼 또는 다음 화살표를 누른다."""
    for locator in (
        page.get_by_text(str(next_number), exact=True),
        page.locator('[aria-label="Next Page"], [aria-label="next page"]'),
    ):
        try:
            target = locator.first
            if target.is_visible(timeout=1500):
                target.click(timeout=4000)
                return True
        except Exception:  # noqa: BLE001 - 다음 후보 셀렉터 시도
            continue
    return False


def main() -> int:
    collected: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1440, "height": 2200})
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector('div[role="listitem"]', timeout=30_000)

        for page_no in range(1, MAX_PAGES + 1):
            time.sleep(PAGE_DELAY)
            fresh = 0
            for item in harvest_current_page(page):
                if item["pdf_url"] not in collected:
                    collected[item["pdf_url"]] = item
                    fresh += 1
            print(f"page {page_no}: +{fresh} (total {len(collected)})", flush=True)
            if not goto_next_page(page, page_no + 1):
                print("no next page control — done", flush=True)
                break
            time.sleep(1.0)  # 클릭 후 repeater 갱신 대기 (다음 루프에서 PAGE_DELAY 추가 대기)

        browser.close()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(list(collected.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(collected)} items -> {OUT_PATH.relative_to(ROOT).as_posix()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
