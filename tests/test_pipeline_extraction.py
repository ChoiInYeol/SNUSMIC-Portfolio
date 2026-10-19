"""Exercise the production parser, including identity, candidate prices, rating fallback and QA."""
from pathlib import Path

import pytest

from build_report_performance import parse_report


def report(tmp_path: Path, cover: str) -> Path:
    path = tmp_path / "2024-01-02_삼성전자.md"
    path.write_text("# 삼성전자 (005930)\n2024년 1월 2일\n" + cover, encoding="utf-8")
    return path


def test_pipeline_scores_competing_prices_instead_of_taking_the_first(tmp_path):
    path = report(tmp_path, "투자의견 Buy\n목표주가 46,000원\n현재주가 40,000원\n상승여력 30%\n목표주가 52,000원")
    parsed = parse_report(path)
    assert (parsed.market, parsed.ticker, parsed.report_date) == ("KR", "005930", "2024-01-02")
    assert (parsed.target_price, parsed.report_current_price, parsed.stated_upside_pct) == (52000, 40000, 30)
    assert parsed.rating == "Buy"
    assert parse_report(path, extractor="legacy").target_price == 46000


def test_rating_fallback_only_fills_a_missing_candidate_opinion(tmp_path):
    path = report(tmp_path, "2024년 1월 2일 Strong Buy\n목표주가 52,000원")
    assert parse_report(path).rating == "Strong Buy"
    path = report(tmp_path, "# Buy\n투자의견 Sell\n목표주가 52,000원")
    assert parse_report(path).rating == "Sell"


def test_explicit_not_rated_cannot_be_replaced_by_body_text_or_title_hint(tmp_path):
    path = report(tmp_path, "Researcher's Opinion\n# N/R [Not Rated]\n본문: 외국인 보유 비중")
    assert parse_report(path, "voera", {"rating_hint": "Buy"}).rating is None


def test_invalid_extractor_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown extractor"):
        parse_report(report(tmp_path, ""), extractor="typo")


def test_gfm_company_cell_wins_over_a_stray_code_in_the_same_row(tmp_path):
    path = tmp_path / "2020-10-31_report.md"
    path.write_text("| | (075580) | 진성티이씨(036890) | 2020년 10월 31일 |\n목표주가 20,350원", encoding="utf-8")
    parsed = parse_report(path)
    assert parsed.ticker == "036890" and parsed.company == "진성티이씨"


def test_gfm_code_only_cell_uses_the_preceding_company_heading(tmp_path):
    path = tmp_path / "2025-10-10_report.md"
    path.write_text("# 로보티즈\n| | (108490, KOSDAQ) |\n| 목표 주가 | 254,100원 |", encoding="utf-8")
    assert parse_report(path).ticker == "108490"
    path.write_text("| 목표주가 | 254100 |\n| 현재주가 | 185300 |", encoding="utf-8")
    assert parse_report(path).ticker is None


def test_competition_bracket_in_filename_does_not_hide_the_real_code(tmp_path):
    path = tmp_path / "2025-05-27_[250527] (KEYSS 대회) 현대건설(000720).md"
    path.write_text("2025/05/27\n# 현대의 건설을 넘어 미래의 원전으로", encoding="utf-8")
    assert parse_report(path, "ewha").ticker == "000720"


def test_sector_coverage_table_is_not_a_single_company_identity(tmp_path):
    path = tmp_path / "2025-09-30_industry.md"
    path.write_text("Industry In-depth | 2025.09.30\n| 종목 | 노바렉스(194700) |", encoding="utf-8")
    assert parse_report(path, "kuvic").ticker is None


def test_explicit_publisher_code_and_clipped_old_table_code_are_supported(tmp_path):
    path = tmp_path / "undated_[BUY] 플랫폼 네이버.md"
    path.write_text("# 금융분석연구회 VOERA 035420\n투자의견 Buy", encoding="utf-8")
    assert parse_report(path, "voera").ticker == "035420"
    path = tmp_path / "2013-09-18_Equity Research, 국민은행.md"
    path.write_text("Company Analysis Report\n| | 2007년 5월 26일<br>060000) 은행업 |", encoding="utf-8")
    parsed = parse_report(path)
    assert parsed.ticker == "060000" and parsed.company == "국민은행"


def test_new_official_names_are_replayable_without_rewriting_bronze(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import build_report_performance as pipeline

    snapshot = tmp_path / "names.json"
    original = b'{"005930": "Samsung"}\n'
    snapshot.write_bytes(original)
    observations = tmp_path / "names.jsonl"
    monkeypatch.setattr(pipeline.lake, "BRONZE_KRX_NAMES", snapshot)
    monkeypatch.setattr(pipeline.lake, "BRONZE_KRX_NAME_OBSERVATIONS", observations)
    monkeypatch.setattr(pipeline, "compute_issues", lambda row: None)
    calls = []
    monkeypatch.setattr(pipeline.stock, "get_market_ticker_name", lambda ticker: calls.append(ticker) or "New name")
    row = SimpleNamespace(market="KR", ticker="123456", company="Old name")
    pipeline.canonicalize_kr_names([row])
    assert row.company == "New name" and snapshot.read_bytes() == original
    first = observations.read_bytes()
    event = json.loads(first)
    assert event["ticker"] == "123456" and event["name"] == "New name"
    assert event["collected_at"] and event["library_version"] and event["source"]
    row.company = "Old name"
    pipeline.canonicalize_kr_names([row])
    assert row.company == "New name" and calls == ["123456"]
    assert observations.read_bytes() == first and snapshot.read_bytes() == original


def test_committed_transcripts_meet_the_pdf_labelled_quality_gate():
    from collections import Counter
    from build_report_performance import load_hints
    from eval_extraction import FIELDS, MD_ROOT, gold_rows, grade, md_path

    hints = load_hints()
    outcomes = Counter()
    fields = Counter()
    for gold in gold_rows():
        if gold.get("report_type") != "company":
            continue
        parsed = parse_report(md_path(gold["source_pdf"], MD_ROOT), gold["school"],
                              hints.get(Path(gold["source_pdf"]).stem))
        prediction = {"rating": parsed.rating, "target": parsed.target_price,
                      "current": parsed.report_current_price, "upside": parsed.stated_upside_pct, "ticker": parsed.ticker}
        for field in FIELDS:
            outcome = grade(field, prediction, gold)
            outcomes[outcome] += 1
            if outcome in {"wrong", "spurious", "missed"}:
                fields[f"{field}:{outcome}"] += 1
    total = sum(outcomes.values())
    assert total == 224 * len(FIELDS), "the complete PDF-labelled company set must be graded"
    critical = outcomes["wrong"] + outcomes["spurious"]
    errors = critical + outcomes["missed"]
    # Legacy full transcripts: 19.4% error / 4.1% critical. Do not regress toward that baseline.
    assert critical / total <= 0.03, (outcomes, fields)
    assert errors / total <= 0.15, (outcomes, fields)
