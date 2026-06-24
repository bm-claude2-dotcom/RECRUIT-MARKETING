#!/usr/bin/env python3
"""
analyze_trends.py  ─ データ収集専用スクリプト

Slack の2チャンネルからメッセージを取得し、slack_raw_data.json に保存します。
分析・コンテンツ生成はブラウザ側（prompt_generator.html + Gemini API）で行います。

セットアップ:
    pip install -r requirements.txt

実行:
    python analyze_trends.py

環境変数 (.env または OS 環境変数):
    SLACK_BOT_TOKEN=xoxb-...
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ── 設定 ──────────────────────────────────────────────────────────────────────
OWN_CHANNEL_ID  = "C0B2AF0FG91"   # 26卒メンバーチャンネル
COMP_CHANNEL_ID = "C0B9NMW0PC3"   # ClaudeCode コンテストチャンネル
DAYS_BACK       = 7
OUTPUT_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slack_raw_data.json")


def fetch_messages(client: WebClient, channel_id: str, days_back: int) -> list:
    oldest = str((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
    messages, cursor = [], None
    while True:
        try:
            kwargs = {"channel": channel_id, "oldest": oldest, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.conversations_history(**kwargs)
            messages.extend(resp.get("messages", []))
            if not resp.get("has_more"):
                break
            cursor = resp["response_metadata"]["next_cursor"]
            time.sleep(0.5)
        except SlackApiError as e:
            print(f"  Slack API エラー ({channel_id}): {e.response['error']}")
            break
    return [m for m in messages if m.get("text") and not m.get("bot_id")]


def main():
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    if not slack_token:
        raise SystemExit("SLACK_BOT_TOKEN が未設定です（.env または環境変数を確認）")

    print(f"Slack チャンネルからメッセージ取得中（過去{DAYS_BACK}日）...")
    slack = WebClient(token=slack_token)

    own_msgs  = fetch_messages(slack, OWN_CHANNEL_ID,  DAYS_BACK)
    comp_msgs = fetch_messages(slack, COMP_CHANNEL_ID, DAYS_BACK)
    print(f"  自社: {len(own_msgs)}件  参照: {len(comp_msgs)}件")

    data = {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days_back": DAYS_BACK,
        "own_channel": {
            "id": OWN_CHANNEL_ID,
            "label": "26卒メンバーチャンネル",
            "message_count": len(own_msgs),
            "messages": [{"ts": m.get("ts", ""), "text": m.get("text", "").strip()} for m in own_msgs]
        },
        "comp_channel": {
            "id": COMP_CHANNEL_ID,
            "label": "ClaudeCodeコンテストチャンネル",
            "message_count": len(comp_msgs),
            "messages": [{"ts": m.get("ts", ""), "text": m.get("text", "").strip()} for m in comp_msgs]
        }
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✓ {OUTPUT_PATH} に保存しました")
    print("  ブラウザで prompt_generator.html を開いてコンテンツを生成してください。")


if __name__ == "__main__":
    main()
