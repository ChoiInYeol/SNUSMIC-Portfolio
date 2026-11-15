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


@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_scheduled_refresh_failures_alert_without_a_telegram_request(env, monkeypatch, result):
    monkeypatch.delenv("TELEGRAM_REQUEST_ID", raising=False)
    monkeypatch.setenv("REFRESH_RESULT", result)
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")
    monkeypatch.setattr(notify, "validate", lambda *args: pytest.fail("failed runs must not read a success book"))
    calls = []
    monkeypatch.setattr(notify, "deliver", lambda chunks, **kw: calls.append((chunks, kw)) or 0)
    assert notify.main() == 0
    assert len(calls) == 1 and calls[0][1]["scope"] == "refresh-run:1234"
    assert "https://github.com/owner/repo/actions/runs/1234" in calls[0][0][0]


def test_unconfigured_scheduled_failure_warns_without_attempting_delivery(env, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_REQUEST_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.setenv("REFRESH_RESULT", "failure")
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")
    monkeypatch.setattr(notify, "deliver", lambda *a, **kw: pytest.fail("not configured"))
    assert notify.main() == 0
    assert "::warning::" in capsys.readouterr().out


def test_all_refresh_modes_build_the_same_publication_inputs_and_report_failure():
    import re
    import lake
    producers = []
    for name, job in (("refresh-daily.yml", "refresh-daily"), ("refresh-reports.yml", "refresh-weekly")):
        text = (lake.ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        block = re.search(r"- name: Rebuild dataset, charts, backtest\n(.*?)(?=\n      - )", text, re.S).group(1)
        commands = re.findall(r"uv run python (scripts/\w+\.py)", block)
        producers.append(commands)
        assert commands.index("scripts/export_signals_api.py") < commands.index("scripts/build_forward_record.py") < commands.index("scripts/export_api_v2.py")
        assert "scripts/build_stats.py" in commands
        commit = text.split("- name: Commit refreshed artifacts", 1)[1]
        assert "src/data/forward-record.json" in commit and "src/data/stats.json" in commit
        reply = text.split("  telegram-result:", 1)[1]
        assert f"needs: {job}" in reply and "always()" in reply and f"needs.{job}.result != 'success'" in reply
        assert "scripts/notify_telegram_refresh.py" in reply
    assert producers[0] == producers[1]
