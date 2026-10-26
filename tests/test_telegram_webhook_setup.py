import pytest

import configure_telegram_webhook as setup
import notify_telegram_refresh as notify


@pytest.fixture
def env(monkeypatch):
    values = {"TELEGRAM_BOT_TOKEN": "secret-bot", "TELEGRAM_CHAT_ID": "123", "VERCEL_TOKEN": "secret-vercel",
              "VERCEL_PROJECT_ID": "prj_test", "VERCEL_ORG_ID": "team_test", "GITHUB_REPOSITORY": "owner/repo",
              "TELEGRAM_ACTIONS_TOKEN": "secret-actions", "GITHUB_TOKEN": "ephemeral-gh-token", "READ_ONLY": "false"}
    for key, value in values.items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)


def test_missing_dispatch_secret_fails_before_any_configuration(env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_ACTIONS_TOKEN")
    monkeypatch.setattr(setup, "api", lambda *a, **kw: pytest.fail("must not change production"))
    with pytest.raises(ValueError, match="TELEGRAM_ACTIONS_TOKEN"):
        setup.main()


def test_setup_uses_production_only_secrets_and_registers_after_a_verified_deployment(env, monkeypatch, capsys):
    calls = []
    def api(method, url, **kw):
        calls.append((url, kw))
        if url.endswith("getChat"): return {"ok": True, "result": {"type": "private"}}
        if url == "https://api.github.com/repos/owner/repo": return {"id": 123}
        if "/v9/projects/" in url: return {"name": "verdict-archive"}
        if url.endswith("/deployments"): return {"id": "dpl_test"}
        if "/deployments/dpl_test" in url: return {"readyState": "READY"}
        return {"ok": True}
    monkeypatch.setattr(setup, "api", api)
    assert setup.main() == 0
    env_calls = [kw["json"] for url, kw in calls if url.endswith("/env")]
    assert all(e["type"] == "sensitive" and e["target"] == ["production"] for e in env_calls)
    assert next(e["value"] for e in env_calls if e["key"] == "TELEGRAM_ACTIONS_TOKEN") == "secret-actions"
    assert not any(e["value"] == "ephemeral-gh-token" for e in env_calls)
    probe = next(i for i, (url, _) in enumerate(calls) if url == setup.WEBHOOK_URL)
    register = next(i for i, (url, _) in enumerate(calls) if url.endswith("setWebhook"))
    assert probe < register
    assert calls[register][1]["json"]["max_connections"] == 1
    menus = [kw["json"] for url, kw in calls if url.endswith("setMyCommands")]
    assert not any(c["command"] == "refresh" for c in menus[0]["commands"])
    assert menus[1]["scope"] == {"type": "chat", "chat_id": "123"}
    assert any(c["command"] == "refresh" for c in menus[1]["commands"])
    output = capsys.readouterr().out
    assert "secret-actions" not in output and "secret-bot" not in output and "secret-vercel" not in output


def test_failed_refresh_reply_does_not_publish_a_success_book(env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_REQUEST_ID", "456")
    monkeypatch.setenv("REFRESH_RESULT", "failure")
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")
    calls = []
    monkeypatch.setattr(notify, "deliver", lambda chunks, **kw: calls.append(chunks) or 0)
    assert notify.main() == 0
    assert len(calls[0]) == 1 and "실패" in calls[0][0] and "SOTA 모델" not in calls[0][0]
