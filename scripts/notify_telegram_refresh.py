"""Requested refresh replies and best-effort alerts for any failed refresh run."""
import json
import os
import re

import lake
from send_telegram_portfolio import deliver
from sota_portfolio import validate


def main():
    request_id = os.environ.get("TELEGRAM_REQUEST_ID", "")
    if request_id and not re.fullmatch(r"\d{1,30}", request_id):
        raise ValueError("invalid Telegram request id")
    result = os.environ.get("REFRESH_RESULT", "")
    if result not in ("success", "failure", "cancelled", "skipped"):
        raise ValueError("invalid refresh result")
    if not request_id and result == "success":
        return 0  # Scheduled successes use the regular digest, not a completion reply.
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not run_id.isdigit():
        raise ValueError("invalid workflow identity")
    url = f"https://github.com/{repository}/actions/runs/{run_id}"
    if result == "success":
        doc = json.loads(lake.GOLD_SOTA_PORTFOLIO.read_text(encoding="utf-8"))
        model = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
        validate(doc, model)
        chunks = [f"✅ 데이터 갱신 완료\n\n📅 평가 기준일 {doc['as_of']}\n\n/status 명령으로 최신 포트폴리오 이미지를 확인하세요.\n\n실행 내역\n{url}"]
    else:
        status = "취소되었습니다" if result in ("cancelled", "skipped") else "실패했습니다"
        chunks = [f"⚠️ 데이터 갱신 알림\n\n데이터 갱신 실행이 {status}.\n실행 내역에서 게시 단계 완료 여부를 확인하세요.\n\n실행 내역\n{url}"]
    if not request_id and not (os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip()):
        print(f"::warning::Refresh {result}; Telegram secrets not configured. {url}")
        return 0
    scope = f"refresh:{request_id}" if request_id else f"refresh-run:{run_id}"
    receipt_id = request_id or f"run-{run_id}"
    return deliver(chunks, scope=scope, receipt=lake.ROOT / "artifacts" / "telegram" / f"refresh-{receipt_id}.json")


if __name__ == "__main__":
    raise SystemExit(main())
