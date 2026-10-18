"""Layout oracles for the candidate-scoring cover extractor (scripts/report_extract.py)."""
from __future__ import annotations

import pytest

from report_extract import extract

LAYOUTS = {
    # kordoc OCR keeps label and value on one tab-separated line
    "ocr tab pairs": ("목표주가\t5,100원\tKey Point\n\n현재주가\t3,630원\t중국\n\n상승여력\t40.5%", "KR", (5100, 3630, 40.5)),
    "range midpoint, dash current": ("# Buy\n\n적정주가: 27,500 ~ 36,300원 현재주가: -\n\n상승여력: -", "KR", (31900, None, None)),
    "label row then value row": ("| 현재주가 | 목표주가 | 상승여력 |\n| --- | --- | --- |\n| 19,400 원 | 30,000 원 | 54.6 % |", "KR", (30000, 19400, 54.6)),
    "label block in one segment": ("현재주가 목표주가 상승여력 19,400 원 30,000 원 54.6 %", "KR", (30000, 19400, 54.6)),
    "us dollars": ("Target Price $210.00\nCurrent Price $168.50\nUpside 24.6%", "US", (210, 168.5, 24.6)),
    "key-value table": ("| 투자의견 | BUY |\n| --- | --- |\n| 목표주가 | 46,000원 |\n| 현재주가 | 30,200원 |\n| 상승여력 | 52.3% |", "KR", (46000, 30200, 52.3)),
    "label qualifiers and downside": ("목표주가(12M) 28,700원\n현재주가(2023.09.20) 30,200원\n하락여력 5.0%", "KR", (28700, 30200, -5.0)),
    "multiples are not prices": ("목표주가 46,000원\nPER 12.3배\n현재주가 30,200원 상승여력 52.3%", "KR", (46000, 30200, 52.3)),
}


@pytest.mark.parametrize("text,market,want", LAYOUTS.values(), ids=LAYOUTS.keys())
def test_cover_layouts(text, market, want):
    e = extract(text, market)
    assert (e.target, e.current, e.upside) == tuple(None if w is None else float(w) for w in want)


def test_arithmetic_consistency_beats_a_stronger_looking_wrong_pair():
    # Two "목표주가" readings; only 52,000 agrees with current 40,000 and the stated 30% upside.
    text = "목표주가 46,000원\n현재주가 40,000원\n상승여력 30.0%\n\n목표주가 52,000원"
    e = extract(text, "KR")
    assert e.target == 52000 and e.consistent


def test_market_close_breaks_a_tie_on_current_price():
    text = "현재주가 30,200원\n현재주가 3,020원\n목표주가 46,000원"
    assert extract(text, "KR", market_close=30_000).current == 30200
    assert extract(text, "KR", market_close=3_000).current == 3020


@pytest.mark.parametrize("text,want", [
    ("| 투자의견 | BUY (유지) |", "Buy"),
    ("Rating\n\n# Strong Buy", "Strong Buy"),
    ("# 매수\n\n본문에서 매도 압력이 커졌다", "Buy"),
    ("투자의견: 중립", "Neutral"),
])
def test_rating(text, want):
    assert extract(text, "KR").rating == want


def test_rating_leading_a_segment_with_change_marker():
    assert extract("We seek further insight\n\nBuy(신규) 목표주가(신규): 190,500원", "KR").rating == "Buy"
