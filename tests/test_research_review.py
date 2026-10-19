import json

import pytest

import build_research_review as review


@pytest.fixture
def proposal(tmp_path, monkeypatch):
    md = tmp_path / "markdown"; md.mkdir(); (md / "report.md").write_text("Target 100")
    corrections = tmp_path / "corrections.json"; corrections.write_text("{}")
    monkeypatch.setattr(review.lake, "SILVER_CORRECTIONS", corrections)
    monkeypatch.setattr(review.lake, "SILVER_MARKDOWN", md)
    row = {"id": "report.md", "transcript_sha256": "abc", "values": {"target_price": 10}, "correction": {}}
    monkeypatch.setattr(review, "build", lambda: {"records": [row]})
    return {"schema_version": "1.0", "reviewer": "reviewer", "reason": "OCR decimal", "changes": [{
        "id": "report.md", "transcript_sha256": "abc", "before": {"target_price": 10}, "before_correction": {},
        "after": {"target_price": 100}, "evidence": "p1 Target 100",
    }]}


def test_review_and_history_commit_atomically(proposal):
    assert review.apply(proposal) == 1
    result = json.loads(review.lake.SILVER_CORRECTIONS.read_text())
    assert result["report.md"]["target_price"] == 100
    assert result["_review_history"][0]["changes"][0]["before"]["target_price"] == 10


@pytest.mark.parametrize("case", ["stale", "before", "history", "evidence", "unknown", "nan", "date"])
def test_invalid_review_never_changes_silver(proposal, case):
    c = proposal["changes"][0]
    if case == "stale": c["transcript_sha256"] = "changed"
    if case == "before": c["before"] = {}
    if case == "history": c["before_correction"] = {"target_price": 5}
    if case == "evidence": c["evidence"] = ""
    if case == "unknown": c["after"] = {"source_file": "elsewhere"}
    if case == "nan": c["after"] = {"target_price": float("nan")}
    if case == "date": c["after"] = {"report_date": "2026-02-30"}
    with pytest.raises((ValueError, KeyError)): review.apply(proposal)
    assert review.lake.SILVER_CORRECTIONS.read_text() == "{}"


def test_source_escape_cannot_publish_arbitrary_local_files(tmp_path, monkeypatch):
    web = tmp_path / "web"; web.mkdir()
    (web / "report-performance.json").write_text('{"as_of":"2026-10-19","records":[]}')
    parsed = tmp_path / "parsed.csv"; parsed.write_text("source_file,school\n../secret,smic\n")
    corrections = tmp_path / "corrections.json"; corrections.write_text("{}")
    monkeypatch.setattr(review.lake, "WEB_DATA", web)
    monkeypatch.setattr(review.lake, "SILVER_REPORT_PARSED", parsed)
    monkeypatch.setattr(review.lake, "SILVER_CORRECTIONS", corrections)
    monkeypatch.setattr(review.lake, "SILVER_MARKDOWN", tmp_path / "markdown")
    with pytest.raises(ValueError, match="escapes"):
        review.build()
