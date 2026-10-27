import importlib

import pytest

import lake
from report_sources import transcription_complete, transcription_version


@pytest.fixture
def transcribed(tmp_path, monkeypatch):
    monkeypatch.setattr(lake, "SILVER_MARKDOWN", tmp_path / "markdown")
    row = {"school": "smic", "pdf_url": "http://example.test/report.pdf", "file": str(tmp_path / "missing.pdf"), "sha256": "a" * 64, "error": None}
    md = lake.SILVER_MARKDOWN / "smic" / "missing.md"
    md.parent.mkdir(parents=True)
    md.write_text(f"<!-- kordoc {transcription_version()} ocr_pages= -->\n\n" + "Verified report body " * 20, encoding="utf-8")
    return row, md


def test_only_current_nonempty_manifest_backed_transcription_counts_as_complete(transcribed):
    row, md = transcribed
    assert transcription_complete(row)
    assert not transcription_complete({**row, "sha256": None})
    assert not transcription_complete({**row, "error": "failed source"})
    md.write_text("<!-- kordoc old-version ocr_pages= -->\n\n" + "old body " * 20, encoding="utf-8")
    assert not transcription_complete(row)
    md.write_text(f"<!-- kordoc {transcription_version()} ocr_pages= -->\n", encoding="utf-8")
    assert not transcription_complete(row)


@pytest.mark.parametrize("module", ["collect_reports", "collect_smic", "collect_ewha", "collect_voera"])
def test_completed_transcript_skips_pdf_download_on_a_fresh_runner(transcribed, monkeypatch, module):
    row, _ = transcribed
    collector = importlib.import_module(module)
    monkeypatch.setattr(collector, "polite_get", lambda *a, **kw: pytest.fail("completed transcript must not download its missing PDF"))
    collector.download_pdf({"pdf_url": row["pdf_url"]}, [row], {row["pdf_url"]: row}, {})
