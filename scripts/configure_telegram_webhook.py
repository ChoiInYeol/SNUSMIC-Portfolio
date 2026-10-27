"""Install the command webhook using existing Actions secrets; never logs credential values.

Run via telegram-webhook-setup.yml. Installs production-only Vercel secrets, deploys main,
verifies the protected route, then registers Telegram's webhook and command menu.
"""
import os
import re
import secrets
import time

import requests

WEBHOOK_URL = "https://verdict-archive.vercel.app/api/telegram/webhook"
PUBLIC_COMMANDS = [
    {"command": "status", "description": "SOTA 현황과 KOSPI 200MA 레짐"},
    {"command": "holdings", "description": "보유 수량, 현재 비중, 평가손익"},
    {"command": "signals", "description": "매수 대기와 스탑 근접 종목"},
    {"command": "trades", "description": "최근 수익 실현과 손실 청산"},
    {"command": "help", "description": "명령어 도움말"},
]
ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    {"command": "refresh", "description": "보고서, 시세, 백테스트 갱신 (관리자)"},
    {"command": "chatid", "description": "현재 방 ID 확인 (관리자)"},
]


def api(method, url, *, label, **kwargs):
    """No exception bodies or URLs: Telegram URLs contain the bot token."""
    try:
        response = requests.request(method, url, timeout=30, allow_redirects=False, **kwargs)
        if not response.ok:
            raise RuntimeError(f"{label} failed (HTTP {response.status_code})")
        return response.json()
    except (requests.RequestException, ValueError):
        raise RuntimeError(f"{label} connection/JSON failure") from None


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing Actions secret/environment: {name}")
    return value


def main():
    read_only = os.environ.get("READ_ONLY", "false").lower() == "true"
    bot = required("TELEGRAM_BOT_TOKEN")
    chat = required("TELEGRAM_CHAT_ID")
    vercel = required("VERCEL_TOKEN")
    project = required("VERCEL_PROJECT_ID")
    team = required("VERCEL_ORG_ID")
    repo = required("GITHUB_REPOSITORY")
    token = os.environ.get("TELEGRAM_ACTIONS_TOKEN", "").strip()
    if not read_only and not token:
        raise ValueError("add repository secret TELEGRAM_ACTIONS_TOKEN (this repository only, Actions: read/write)")
    if not re.fullmatch(r"-?\d+", chat) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("invalid configured chat/repository")
    if not re.fullmatch(r"[\w-]+", project) or not re.fullmatch(r"[\w-]+", team):
        raise ValueError("invalid Vercel project/team")
    tg = f"https://api.telegram.org/bot{bot}"
    me = api("POST", f"{tg}/getMe", label="Telegram bot validation")
    destination = api("POST", f"{tg}/getChat", label="Telegram chat validation", json={"chat_id": chat})
    if me.get("ok") is not True or destination.get("ok") is not True:
        raise ValueError("Telegram bot/chat validation failed")
    owner = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not owner and destination["result"]["type"] == "private":
        owner = chat
    if not re.fullmatch(r"\d+", owner):
        raise ValueError("group/channel destination requires TELEGRAM_ALLOWED_USER_ID (the command owner's user id)")
    rooms = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if rooms and any(not re.fullmatch(r"-?\d+", room.strip()) for room in rooms.split(",")):
        raise ValueError("TELEGRAM_ALLOWED_CHAT_IDS must be comma-separated numeric chat ids")
    gh_headers = {"Authorization": f"Bearer {token or required('GITHUB_TOKEN')}", "Accept": "application/vnd.github+json"}
    repository = api("GET", f"https://api.github.com/repos/{repo}", label="GitHub repository validation", headers=gh_headers)
    if token:
        api("GET", f"https://api.github.com/repos/{repo}/actions/workflows/refresh-daily.yml",
            label="GitHub workflow token validation", headers=gh_headers)
    vh = {"Authorization": f"Bearer {vercel}"}
    params = {"teamId": team}
    project_doc = api("GET", f"https://api.vercel.com/v9/projects/{project}", label="Vercel project validation", headers=vh, params=params)
    secret = secrets.token_urlsafe(32)
    env = {"TELEGRAM_BOT_TOKEN": bot, "TELEGRAM_CHAT_ID": chat, "TELEGRAM_ALLOWED_USER_ID": owner,
           "TELEGRAM_WEBHOOK_SECRET": secret, "TELEGRAM_GITHUB_REPOSITORY": repo,
           "TELEGRAM_REFRESH_ENABLED": "false" if read_only else "true"}
    # A nonempty sentinel disables additional rooms when the allowlist is empty; the route ignores it.
    env["TELEGRAM_ALLOWED_CHAT_IDS"] = rooms or "none"
    if not read_only:
        env["TELEGRAM_ACTIONS_TOKEN"] = token
    for key, value in env.items():
        saved = api("POST", f"https://api.vercel.com/v10/projects/{project}/env", label=f"Vercel {key} configuration",
                    headers=vh, params={**params, "upsert": "true"},
                    json={"key": key, "value": value, "type": "sensitive", "target": ["production"]})
        if saved.get("failed"):
            raise RuntimeError(f"Vercel {key} configuration was rejected")
    print("Production command environment configured (values hidden).", flush=True)
    deploy = api("POST", "https://api.vercel.com/v13/deployments", label="Vercel deployment", headers=vh, params=params,
                 json={"name": project_doc["name"], "project": project, "target": "production",
                       "gitSource": {"type": "github", "repoId": str(repository["id"]), "ref": "main"}})
    deployment_id = deploy["id"]
    print(f"Deploying main: {deployment_id}", flush=True)
    for _ in range(150):
        state = api("GET", f"https://api.vercel.com/v13/deployments/{deployment_id}", label="Vercel deployment status", headers=vh, params=params)
        if state.get("readyState") == "READY":
            break
        if state.get("readyState") in ("ERROR", "CANCELED"):
            raise RuntimeError("Vercel deployment failed; webhook registration was not changed")
        time.sleep(4)
    else:
        raise RuntimeError("Vercel deployment timed out; webhook registration was not changed")
    # Probe the stable production alias, not a preview domain, before moving Telegram delivery.
    for attempt in range(15):
        try:
            probe = api("POST", WEBHOOK_URL, label="Production webhook verification",
                        headers={"X-Telegram-Bot-Api-Secret-Token": secret}, json={"update_id": 0})
            if probe.get("ok") is True:
                break
        except RuntimeError:
            if attempt == 14:
                raise
        time.sleep(2)
    else:
        raise RuntimeError("Production webhook verification failed")
    registered = api("POST", f"{tg}/setWebhook", label="Telegram webhook registration",
                     json={"url": WEBHOOK_URL, "secret_token": secret, "allowed_updates": ["message"],
                           "max_connections": 1, "drop_pending_updates": False})
    if registered.get("ok") is not True:
        raise RuntimeError("Telegram webhook registration rejected")
    scopes = [(None, PUBLIC_COMMANDS), ({"type": "chat", "chat_id": owner}, ADMIN_COMMANDS)]
    for room in sorted({chat, *(r.strip() for r in rooms.split(",") if r.strip())}):
        if room.startswith("-"):
            scopes.append(({"type": "chat_member", "chat_id": room, "user_id": int(owner)}, ADMIN_COMMANDS))
    for scope, commands in scopes:
        body = {"commands": commands, **({"scope": scope} if scope else {})}
        menu = api("POST", f"{tg}/setMyCommands", label="Telegram command menu", json=body)
        if menu.get("ok") is not True:
            raise RuntimeError("Telegram command menu registration rejected")
    print(f"Command webhook ready: {WEBHOOK_URL} ({'read-only' if read_only else 'queries and refresh'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
