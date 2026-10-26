"""Completion reply for a /refresh request, including the new SOTA book only after publication."""
import datetime as dt
import json
import os
import re
from zoneinfo import ZoneInfo

import lake
from send_telegram_portfolio import deliver, sota_messages
from sota_portfolio import validate


def main():
    request_id = os.environ.get("TELEGRAM_REQUEST_ID", "")
    if not re.fullmatch(r"\d{1,30}", request_id):
        raise ValueError("invalid Telegram request id")
    result = os.environ.get("REFRESH_RESULT", "")
    if result not in ("success", "failure", "cancelled", "skipped"):
        raise ValueError("invalid refresh result")
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not run_id.isdigit():
        raise ValueError("invalid workflow identity")
    url = f"https://github.com/{repository}/actions/runs/{run_id}"
    if result == "success":
        doc = json.loads(lake.GOLD_SOTA_PORTFOLIO.read_text(encoding="utf-8"))
        model = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
        validate(doc, model)
        day = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        chunks = [f"✅ 요청한 데이터 갱신이 완료되었습니다. 평가 기준일 {doc['as_of']}\n{url}"] + sota_messages(doc, day)
    else:
        status = "취소되었습니다" if result in ("cancelled", "skipped") else "실패했습니다"
        chunks = [f"요청한 데이터 갱신이 {status}. 새 데이터는 게시되지 않았습니다.\n{url}"]
    return deliver(chunks, scope=f"refresh:{request_id}", receipt=lake.ROOT / "artifacts" / "telegram" / f"refresh-{request_id}.json")


if __name__ == "__main__":
    raise SystemExit(main())
