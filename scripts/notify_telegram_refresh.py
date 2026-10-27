"""Short /refresh completion reply, validated against the published SOTA book."""
import json
import os
import re

import lake
from send_telegram_portfolio import deliver
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
        chunks = [f"✅ 데이터 갱신 완료\n\n📅 평가 기준일 {doc['as_of']}\n\n/status 명령으로 최신 포트폴리오 이미지를 확인하세요.\n\n실행 내역\n{url}"]
    else:
        status = "취소되었습니다" if result in ("cancelled", "skipped") else "실패했습니다"
        chunks = [f"⚠️ 데이터 갱신 알림\n\n요청한 데이터 갱신이 {status}.\n새 데이터는 게시되지 않았습니다.\n\n실행 내역\n{url}"]
    return deliver(chunks, scope=f"refresh:{request_id}", receipt=lake.ROOT / "artifacts" / "telegram" / f"refresh-{request_id}.json")


if __name__ == "__main__":
    raise SystemExit(main())
