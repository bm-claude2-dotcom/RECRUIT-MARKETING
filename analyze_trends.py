#!/usr/bin/env python3
"""
analyze_trends.py

Slack の2チャンネルからメッセージを取得し、Gemini で採用テーマ分析を行い、
prompt_generator.html の DEFAULT_ANALYSIS を自動書き換えする。

初回セットアップ:
    pip install -r requirements.txt

実行:
    python analyze_trends.py

環境変数は .env ファイルか OS 環境変数に設定してください:
    SLACK_BOT_TOKEN=xoxb-...
    GEMINI_API_KEY=...
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from google import genai

# ── 設定 ──────────────────────────────────────────────────────────────────────
OWN_CHANNEL_ID  = "C0B2AF0FG91"   # 26卒メンバーチャンネル
COMP_CHANNEL_ID = "C0B9NMW0PC3"   # ClaudeCode コンテストチャンネル
DAYS_BACK       = 7
GEMINI_MODEL    = "gemini-2.5-flash"
HTML_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_generator.html")

THEME_LIST = [
    "誰と働く(社員・カルチャー)",
    "成長・育成",
    "入社後リアル(仕事・1日)",
    "選考・就活",
    "ビジョン・理念",
    "事業・専門性",
    "働き方・制度",
    "多様性・グローバル",
]

ANALYSIS_PROMPT = """\
あなたは採用マーケティングの専門家です。
以下の2つのSlackチャンネルのメッセージを分析し、指定のJSON形式のみを返してください（説明文・コードブロック記号は不要）。

## チャンネルA（自社：26卒メンバーチャンネル） - {own_count}件
{own_text}

## チャンネルB（競合参照：ClaudeCodeコンテストチャンネル） - {comp_count}件
{comp_text}

## 分析タスク
1. 各チャンネルのメッセージを以下の採用テーマに分類し、各テーマに言及したメッセージの割合(%)を算出
   テーマ: {themes}

2. 頻出キーワードTOP15を抽出（ストップワード除外・意味単位で）

3. 差分分析から採用コンテンツ企画3案を提案
   - 競合% − 自社% が大きいテーマ → "空白を埋める" 提案
   - 自社が強いテーマ → "強みを伸ばす" 提案

## 出力（JSONのみ）
{{
  "own_count": {own_count},
  "comp_count": {comp_count},
  "date": "{date}",
  "themes": [
    {{"name": "テーマ名", "own": 自社%, "comp": 競合%}}
  ],
  "keywords": [["キーワード", 件数]],
  "suggestions": [
    {{
      "id": "theme1",
      "title": "企画タイトル",
      "gap": "関連テーマ名",
      "diff": "+XXpt",
      "kind": "空白を埋める",
      "summary": "企画の狙いを1文で",
      "target": "訴求タグ",
      "pillar": "MVV/価値軸"
    }},
    {{
      "id": "theme2",
      "title": "企画タイトル",
      "gap": "関連テーマ名",
      "diff": "+XXpt",
      "kind": "空白を埋める",
      "summary": "企画の狙いを1文で",
      "target": "訴求タグ",
      "pillar": "MVV/価値軸"
    }},
    {{
      "id": "theme3",
      "title": "企画タイトル",
      "gap": "関連テーマ名",
      "diff": "強み活用",
      "kind": "強みを伸ばす",
      "summary": "企画の狙いを1文で",
      "target": "訴求タグ",
      "pillar": "MVV/価値軸"
    }}
  ]
}}
"""


def fetch_messages(client: WebClient, channel_id: str, days_back: int) -> list:
    """指定チャンネルの過去 days_back 日分のメッセージを取得"""
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


def messages_to_text(messages: list, max_msgs: int = 300) -> str:
    return "\n---\n".join(m["text"] for m in messages[:max_msgs])


def call_gemini(client, prompt: str) -> dict:
    resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    raw = resp.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"Gemini レスポンスからJSONを抽出できませんでした:\n{raw[:500]}")
    return json.loads(m.group())


def update_html(html_path: str, analysis: dict) -> None:
    """prompt_generator.html の DEFAULT_ANALYSIS をマーカー間で置換する"""
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_json = json.dumps(analysis, ensure_ascii=False, indent=2)
    new_block = (
        "// <<AUTO_ANALYSIS_DATA_START>>\n"
        f"const DEFAULT_ANALYSIS = {new_json};\n"
        "// <<AUTO_ANALYSIS_DATA_END>>"
    )

    if "// <<AUTO_ANALYSIS_DATA_START>>" in content:
        pattern = r"// <<AUTO_ANALYSIS_DATA_START>>[\s\S]*?// <<AUTO_ANALYSIS_DATA_END>>"
        new_content = re.sub(pattern, new_block, content, count=1)
    else:
        print("⚠ マーカーが見つかりません。prompt_generator.html を確認してください。")
        return

    if new_content == content:
        print("⚠ DEFAULT_ANALYSIS の置換が行われませんでした。")
        return

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"✓ {html_path} を更新しました")


def main():
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    gemini_key  = os.environ.get("GEMINI_API_KEY")
    if not slack_token:
        raise SystemExit("SLACK_BOT_TOKEN が未設定です（.env または環境変数を確認）")
    if not gemini_key:
        raise SystemExit("GEMINI_API_KEY が未設定です（.env または環境変数を確認）")

    print(f"Slack チャンネルからメッセージ取得中（過去{DAYS_BACK}日）...")
    slack = WebClient(token=slack_token)
    own_msgs  = fetch_messages(slack, OWN_CHANNEL_ID,  DAYS_BACK)
    comp_msgs = fetch_messages(slack, COMP_CHANNEL_ID, DAYS_BACK)
    print(f"  自社: {len(own_msgs)}件  競合: {len(comp_msgs)}件")

    print("Gemini API で分析中...")
    gemini_client = genai.Client(api_key=gemini_key)
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = ANALYSIS_PROMPT.format(
        own_count=len(own_msgs),
        comp_count=len(comp_msgs),
        own_text=messages_to_text(own_msgs)  or "(メッセージなし)",
        comp_text=messages_to_text(comp_msgs) or "(メッセージなし)",
        themes="、".join(THEME_LIST),
        date=today,
    )
    analysis = call_gemini(gemini_client, prompt)
    analysis["date"] = today

    print("HTML を更新中...")
    update_html(HTML_PATH, analysis)
    print("完了！ブラウザで prompt_generator.html を開いて確認してください。")


if __name__ == "__main__":
    main()
