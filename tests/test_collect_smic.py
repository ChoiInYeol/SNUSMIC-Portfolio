"""A non-JSON SMIC response must not be reported as a successful incremental collection."""
import pytest

import collect_smic as smic


POST = {"id": 12714, "date": "2026-10-18T06:43:26", "link": "http://snusmic.com/equity-research-nebius-group/",
        "title": {"rendered": "Equity Research, nebius group N.V."},
        "content": {"rendered": '<a href="http://snusmic.com/nebius.pdf">download</a>'}}


class Response:
    headers = {"X-WP-TotalPages": "79"}
    def __init__(self, value): self.value = value
    def raise_for_status(self): pass
    def json(self):
        if isinstance(self.value, Exception): raise self.value
        return self.value


def test_non_json_response_retries_and_recovers_without_an_empty_success(monkeypatch):
    responses = iter([Response(ValueError("HTML instead of JSON")), Response([POST])])
    calls = []
    def get(*args, **kwargs): calls.append(kwargs); return next(responses)
    monkeypatch.setattr(smic, "polite_get", get)
    assert smic.fetch_posts_page(1) == ([POST], 79)
    assert len(calls) == 2 and calls[0]["headers"]["Accept"] == "application/json"


def test_source_javascript_verification_uses_a_browser_before_retrying_json(monkeypatch):
    blocked = Response(ValueError("HTML"))
    blocked.text = '<html><script src="/cupid.js"></script></html>'
    responses = iter([blocked, Response([POST])])
    calls = []
    def get(url, **kwargs): return next(responses)
    monkeypatch.setattr(smic, "polite_get", get)
    monkeypatch.setattr(smic, "establish_browser_session", lambda url: calls.append(url))
    assert smic.fetch_posts_page(1) == ([POST], 79)
    assert len(calls) == 1 and calls[0].startswith(smic.SMIC_API)


@pytest.mark.parametrize("bad", [ValueError("HTML"), {}, [], [{"id": 1}]])
def test_repeated_malformed_response_fails_collection(monkeypatch, bad):
    monkeypatch.setattr(smic, "polite_get", lambda *a, **kw: Response(bad))
    monkeypatch.setattr(smic, "load_manifest", lambda: [])
    monkeypatch.setattr(smic, "build_sha_index", lambda: {})
    with pytest.raises(RuntimeError, match="collection incomplete"):
        smic.collect_smic(limit=None, full=False)


def test_cli_returns_nonzero_on_collection_failure(monkeypatch):
    monkeypatch.setattr("sys.argv", ["collect_smic.py"])
    def fail(**kwargs): raise RuntimeError("SMIC collection incomplete")
    monkeypatch.setattr(smic, "collect_smic", fail)
    assert smic.main() == 1


def test_pdf_download_failure_is_not_a_successful_refresh(monkeypatch):
    monkeypatch.setattr(smic, "load_manifest", lambda: [])
    monkeypatch.setattr(smic, "build_sha_index", lambda: {})
    monkeypatch.setattr(smic, "fetch_posts_page", lambda page: ([POST], 1))
    monkeypatch.setattr(smic, "download_pdf", lambda entry, *args: entry.update(error="download failed"))
    with pytest.raises(RuntimeError, match="PDF download failures"):
        smic.collect_smic(limit=None, full=False)
