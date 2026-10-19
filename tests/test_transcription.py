from transcribe_pdfs import merge_ocr_pages, needs_ocr, ocr_page_numbers, render
import transcribe_pdfs as transcription


def test_only_bad_cover_pages_are_ocrd_in_an_otherwise_readable_document():
    quality = [{"page": n, "needsOcr": n in {1, 5}} for n in range(1, 11)]
    doc = {"pageQuality": quality}
    assert needs_ocr(doc)
    assert ocr_page_numbers(doc) == (1,)


def test_mostly_scanned_documents_ocr_all_bad_pages_but_keep_good_ones():
    doc = {"pageQuality": [{"page": n, "needsOcr": n != 2} for n in range(1, 7)]}
    assert ocr_page_numbers(doc) == (1, 3, 4, 5, 6)


def test_broken_numeral_fonts_require_ocr_even_when_engine_quality_passes():
    doc = {"pageQuality": [{"page": n, "needsOcr": False} for n in range(1, 5)],
           "pages": [{"pageNumber": 1, "markdown": "塩塧塩塭.塧塬.塩塧\n목표주가 塨填塨,塮塧塧원"},
                     {"pageNumber": 2, "markdown": "정상 본문 141,700원, 2026.05.20"},
                     {"pageNumber": 4, "markdown": "증가율 塪塰.塰%"}]}
    assert needs_ocr(doc)
    assert ocr_page_numbers(doc) == (1, 4)
    assert not needs_ocr({"pageQuality": [{"page": 1, "needsOcr": False}],
                          "pages": [{"pageNumber": 1, "markdown": "三星電子와 Tokyo Electron: 정상 회사명"}]})


def test_overprinted_cover_code_is_ocrd_without_guessing_its_digits():
    doc = {"pageQuality": [{"page": 1, "needsOcr": False}],
           "pages": [{"pageNumber": 1, "markdown": "회사명(023890(023890))"}]}
    assert needs_ocr(doc) and ocr_page_numbers(doc) == (1,)


def test_missing_image_placeholders_do_not_push_the_real_cover_out_of_view():
    from report_text import strip_missing_images

    text = "authors\n\n" + "![image](image_001.png)\n\n" * 100 + "real cover\n![chart](https://example.com/chart.png)"
    normalized = strip_missing_images(text)
    assert normalized == "authors\n\nreal cover\n![chart](https://example.com/chart.png)"


def test_partial_ocr_preserves_the_full_document_and_original_page_order():
    base = {"success": True, "pageCount": 4,
            "pages": [{"pageNumber": n, "markdown": f"body {n}"} for n in range(1, 5)],
            "pageQuality": [{"page": n, "needsOcr": n == 1} for n in range(1, 5)]}
    recovered = {"pages": [{"pageNumber": 1, "markdown": "recovered cover"},
                            {"pageNumber": 3, "markdown": "unrequested chart OCR"}],
                 "pageQuality": [{"page": 1, "ocrApplied": True}, {"page": 3, "ocrApplied": True}]}
    merged = merge_ocr_pages(base, recovered, (1,))
    assert merged["pageCount"] == 4
    assert merged["markdown"] == "recovered cover\n\nbody 2\n\nbody 3\n\nbody 4"
    assert render(merged, "4.22.13").startswith("<!-- kordoc 4.22.13 ocr_pages=1 -->")


def test_batch_requests_only_bad_pages_and_writes_a_complete_transcript(tmp_path, monkeypatch):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"test input")
    output = tmp_path / "markdown"
    output.mkdir()
    calls = []
    base = {"success": True, "pages": [{"pageNumber": n, "markdown": f"page {n}"} for n in range(1, 11)],
            "markdown": "original text", "pageQuality": [{"page": n, "needsOcr": n == 1} for n in range(1, 11)]}

    def engine(pdfs, out_dir, jobs, ocr, pages):
        calls.append((jobs, ocr, pages))
        return {pdf: {"success": True, "pages": [{"pageNumber": 1, "markdown": "OCR cover"}],
                      "pageQuality": [{"page": 1, "ocrApplied": True}]}} if ocr else {pdf: base}

    monkeypatch.setattr(transcription, "run_kordoc", engine)
    assert transcription.transcribe_batch([pdf], output, "4.22.13", 4) == (1, 1, [])
    assert calls == [(4, False, None), (1, True, "1")]
    text = (output / "sample.md").read_text(encoding="utf-8")
    assert "OCR cover" in text and "page 10" in text and "page 1\n" not in text


def test_partial_cli_failure_blocks_publication_even_when_other_files_succeed(tmp_path, monkeypatch):
    (tmp_path / "good.pdf").write_bytes(b"test input")
    (tmp_path / "bad.pdf").write_bytes(b"test input")
    monkeypatch.setattr(transcription.sys, "argv", ["transcribe_pdfs.py", "--school", "smic"])
    monkeypatch.setattr(transcription, "kordoc_version", lambda: "4.22.13")
    monkeypatch.setattr(transcription, "targets", lambda *args: (tmp_path, tmp_path / "markdown"))
    monkeypatch.setattr(transcription, "transcribe_batch", lambda *args: (1, 0, ["bad.pdf"]))
    assert transcription.main() == 1


def test_completed_text_document_survives_an_interrupted_ocr_batch(tmp_path, monkeypatch):
    import pytest

    readable, scanned = tmp_path / "readable.pdf", tmp_path / "scan.pdf"
    for pdf in (readable, scanned):
        pdf.write_bytes(b"PDF")
    output = tmp_path / "markdown"
    output.mkdir()

    def engine(pdfs, out_dir, jobs, ocr, pages):
        if ocr:
            raise RuntimeError("worker interrupted")
        return {readable: {"markdown": "Complete text", "pageQuality": []},
                scanned: {"markdown": "broken", "pages": [{"pageNumber": 1, "markdown": "broken"}],
                          "pageQuality": [{"page": 1, "needsOcr": True}]}}

    monkeypatch.setattr(transcription, "run_kordoc", engine)
    with pytest.raises(RuntimeError, match="worker interrupted"):
        transcription.transcribe_batch([readable, scanned], output, "4.22.13", 4)
    assert transcription.is_current(output / "readable.md", "4.22.13")
    assert "Complete text" in (output / "readable.md").read_text()
    assert not (output / "scan.md").exists()


def test_empty_collision_alias_uses_only_a_verified_canonical_pdf(tmp_path, monkeypatch):
    import hashlib
    import json

    folder = tmp_path / "ewha"
    folder.mkdir()
    alias = folder / "2020-10-10_[201010] Device(187870).pdf"
    alias.write_bytes(b"")
    canonical = folder / "2020-10-10_ewha-14218835.pdf"
    original = b"%PDF-original bytes"
    canonical.write_bytes(original)
    manifest = tmp_path / "manifest.json"
    row = {"school": "ewha", "title": "[201010] Device(187870)", "published_hint": "2020-10-10",
           "file": str(canonical), "sha256": hashlib.sha256(original).hexdigest()}
    manifest.write_text(json.dumps([row]), encoding="utf-8")
    monkeypatch.setattr(transcription, "BRONZE_REPORT_MANIFEST", manifest)
    monkeypatch.setattr(transcription, "PDF_ROOT", tmp_path)
    calls = []

    def engine(pdfs, *args, **kwargs):
        calls.extend(pdfs)
        return {canonical: {"markdown": "Complete original report", "pageQuality": []}}

    monkeypatch.setattr(transcription, "run_kordoc", engine)
    output = tmp_path / "markdown"
    output.mkdir()
    assert transcription.transcribe_batch([alias, canonical], output, "4.22.13", 4) == (2, 0, [])
    assert calls == [canonical]
    assert "Complete original report" in (output / f"{alias.stem}.md").read_text()
    assert alias.read_bytes() == b"" and canonical.read_bytes() == original
    row["sha256"] = "incorrect"
    manifest.write_text(json.dumps([row]), encoding="utf-8")
    assert transcription.canonical_pdf(alias) is None
