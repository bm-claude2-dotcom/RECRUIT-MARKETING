#!/usr/bin/env python3
"""
analyze_trends.py

Slack の2チャンネルからメッセージを取得し、Gemini で社内イベントを抽出・SNS投稿案を生成し、
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

ANALYSIS_PROMPT = """\
あなたは株式会社プリンシプル（Principle Co., Ltd.）の採用マーケティング専門家です。
以下のSlackメッセージを分析し、JSONのみを返してください（コードブロック・説明文は不要）。

【プリンシプル会社情報（SNS文章生成に使用）】
- 事業: データ解析を軸としたデジタルマーケティング。GA4・GTM・BigQuery・SEO・Web広告など
- Mission: データとアクションをつなぎ、よりよい世界を実現します
- Value: 自立したプロフェッショナル / Win-Win / 世界基準・多様性
- ブランドトーン: 誠実・等身大・データドリブン・煽らない・上から目線にしない

## 自社Slackメッセージ（26卒メンバーチャンネル） - {company_messages}件
実行日時: {last_updated}（過去{days_back}日分）

{own_text}

## 参照Slackメッセージ（ClaudeCodeコンテストチャンネル） - {competitor_messages}件

{comp_text}

## タスク1: 直近の社内イベント抽出（最重要タスク）
過去{days_back}日以内に実施・報告されたイベントを血眼になって見つけ出す。
対象: 勉強会・懇親会・全社会議・プロジェクトリリース・部活動・歓迎会・社内発表会など。
最低1件・最大5件を抽出すること。
明確なイベント記述がない場合も、メッセージのトーン・話題・言及内容から推測して必ず1件以上出力すること。

## タスク2: イベントごとのSNS投稿案（採用マーケティング目的）
各イベントに対して、X（旧Twitter）/LinkedIn にそのまま投稿できる文章を生成。
【必須条件】
- 140文字程度（前後15文字は許容）
- 社内の熱量・カルチャー・若手の活躍・技術へのこだわりが伝わる文脈にする
- 単なる告知ではなく、共感・拡散される文章にする
- ハッシュタグ（#企業公式中の人 #採用 #Principle など）を必ず文末に含める
- プリンシプルのMVV・ブランドトーン（誠実・等身大）に合わせる

## タスク3: 採用テーマ別の市場トレンド分析
自社 vs 競合のメッセージを以下のテーマで比較分類し、各テーマの言及割合(%)を算出:
誰と働く(社員・カルチャー) / 成長・育成 / 入社後リアル(仕事・1日) / 選考・就活 / ビジョン・理念 / 事業・専門性 / 働き方・制度 / 多様性・グローバル

## 出力JSON（このフォーマットを厳守。他のテキストは一切出力しない）
{{
  "last_updated": "{last_updated}",
  "slack_status": {{
    "company_messages": {company_messages},
    "competitor_messages": {competitor_messages}
  }},
  "recent_events": [
    {{
      "title": "イベント名（具体的に。例: 入社3ヶ月メンバーの初LT登壇）",
      "date": "開催時期（例: 今週水曜、昨日、今週など）",
      "summary": "Slackから読み取ったイベントの概要・社内の盛り上がり（2〜3文で具体的に）",
      "source_topic": "元になったSlack会話の文脈（例: デベロッパーchでのスライド共有より）",
      "sns_draft": "X/LinkedIn投稿文（140文字程度・ハッシュタグ込み・改行なし）"
    }}
  ],
  "market_trends": [
    {{
      "theme": "テーマ名",
      "own_pct": 自社言及割合(整数),
      "comp_pct": 競合言及割合(整数),
      "gap": 競合%-自社%(整数)
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
    lines = []
    for m in messages[:max_msgs]:
        ts = m.get("ts", "")
        text = m.get("text", "").strip()
        if text:
            lines.append(f"[{ts}] {text}")
    return "\n---\n".join(lines)


def call_gemini(client, prompt: str) -> dict:
    resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    raw = resp.text.strip()
    # マークダウンコードブロックを除去
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # 最外の {...} を抽出
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"Gemini レスポンスからJSONを抽出できませんでした:\n{raw[:500]}")
    return json.loads(m.group())


def update_html(html_path: str, data: dict) -> None:
    """prompt_generator.html の DEFAULT_ANALYSIS をマーカー間で置換する"""
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_json = json.dumps(data, ensure_ascii=False, indent=2)
    new_block = (
        "// <<AUTO_ANALYSIS_DATA_START>>\n"
        f"const DEFAULT_ANALYSIS = {new_json};\n"
        "// <<AUTO_ANALYSIS_DATA_END>>"
    )

    if "// <<AUTO_ANALYSIS_DATA_START>>" not in content:
        print("⚠ マーカーが見つかりません。prompt_generator.html を確認してください。")
        return

    pattern = r"// <<AUTO_ANALYSIS_DATA_START>>[\s\S]*?// <<AUTO_ANALYSIS_DATA_END>>"
    new_content = re.sub(pattern, new_block, content, count=1)

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

    # 1. Slack メッセージ取得
    print(f"Slack チャンネルからメッセージ取得中（過去{DAYS_BACK}日）...")
    slack = WebClient(token=slack_token)
    own_msgs  = fetch_messages(slack, OWN_CHANNEL_ID,  DAYS_BACK)
    comp_msgs = fetch_messages(slack, COMP_CHANNEL_ID, DAYS_BACK)
    print(f"  自社: {len(own_msgs)}件  参照: {len(comp_msgs)}件")

    # 2. Gemini で分析・生成
    print("Gemini API で分析・SNS投稿案を生成中...")
    gemini_client = genai.Client(api_key=gemini_key)
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = ANALYSIS_PROMPT.format(
        company_messages=len(own_msgs),
        competitor_messages=len(comp_msgs),
        own_text=messages_to_text(own_msgs)  or "(メッセージなし)",
        comp_text=messages_to_text(comp_msgs) or "(メッセージなし)",
        last_updated=last_updated,
        days_back=DAYS_BACK,
    )
    data = call_gemini(gemini_client, prompt)
    data["last_updated"] = last_updated

    # 3. HTML 更新
    print("HTML を更新中...")
    update_html(HTML_PATH, data)
    print("完了！ブラウザで prompt_generator.html を開いて確認してください。")


if __name__ == "__main__":
    main()
