"""CI must fail rather than write or skip reviewed regression expectations."""
from __future__ import annotations

import pytest
import test_golden as golden


def test_ci_update_attempt_fails_before_any_golden_write(monkeypatch, tmp_path):
    path = tmp_path / "strategies.json"
    path.write_text("original reviewed evidence", encoding="utf-8")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("UPDATE_GOLDEN", "1")
    monkeypatch.setattr(golden, "GOLDEN_PATH", path)
    with pytest.raises(pytest.fail.Exception, match="CI cannot update or skip"):
        golden.test_golden_snapshot({})
    assert path.read_text(encoding="utf-8") == "original reviewed evidence"
