#!/usr/bin/env python3
"""
analyze_trends.py  ─ ワンコマンド完結パイプライン

  1. Slack から最新メッセージを取得
  2. Gemini で「10個の切り口 + 各本文」を一括生成
  3. prompt_generator.html に結果を書き込み完了

セットアップ:
    pip install -r requirements.txt

.env ファイルを作成して実行:
    SLACK_BOT_TOKEN=xoxb-...
    GEMINI_API_KEY=...

    python analyze_trends.py
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

COMPANY_CTX = """\
【株式会社プリンシプル（Principle Co.,Ltd.）】
・事業: データ解析を軸としたデジタルマーケティング（GA4・GTM・BigQuery・SEO・Web広告など）
・Mission: データとアクションをつなぎ、よりよい世界を実現します
・Vision: 世界で最も信頼されるマーケティングDXパートナー
・Value: 自立したプロフェッショナル / Win-Win / 世界基準・多様性
・従業員数: 約100名、東京都千代田区
・ターゲット: 新卒・大学3-4年生。「誰と働くか」「成長できるか」「入社後のリアル」「ビジョンが叶うか」
・トンマナ: 誠実・等身大・データドリブン。煽らない・誇張しない。"""

PROMPT_TEMPLATE = """\
あなたは株式会社プリンシプルの採用マーケティング専門家です。
以下のSlackデータを分析し、採用SNS・ブログ用コンテンツ企画を【必ず10個】生成してください。
各企画に対して、そのまま掲載できるレベルの本文も同時に生成してください。
JSONのみ返してください（コードブロック・説明文は一切不要）。

{company_ctx}

## 自社Slackメッセージ（{own_label}） - {own_count}件 / 取得日時: {fetched_at}
{own_text}

## 参照Slackメッセージ（{comp_label}） - {comp_count}件
{comp_text}

## 生成ルール
- 10個の企画はすべて異なる切り口にする（社員/成長/カルチャー/技術/リアル/ビジョン等の複数軸を使う）
- Slackの具体的な話題・雰囲気を反映させること（抽象的より具体的エピソード重視）
- 競合採用コンテンツのトレンドも意識する
- 各本文は250〜420文字で、末尾にハッシュタグ3〜5個必須
- 確証のない情報には [要確認: ◯◯] と明示すること

## 出力JSON（必ず items を10件生成）
{{
  "last_updated": "{last_updated}",
  "slack_status": {{
    "company_messages": {own_count},
    "competitor_messages": {comp_count}
  }},
  "items": [
    {{
      "id": 1,
      "title": "タイトル（20文字以内）",
      "hook": "この切り口の狙い・フック（60文字以内）",
      "category": "カテゴリ（働く人/成長/カルチャー/技術/リアル/ビジョンなど）",
      "content": "採用ブログ・長文SNS用の本文（250〜420文字・ハッシュタグ込み）"
    }},
    ...合計10件...
  ]
}}"""


# ── Slack ─────────────────────────────────────────────────────────────────────
def fetch_messages(client: WebClient, channel_id: str) -> list:
    oldest = str((datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).timestamp())
    messages, cursor = [], None
    while True:
        try:
            kw = {"channel": channel_id, "oldest": oldest, "limit": 200}
            if cursor:
                kw["cursor"] = cursor
            resp = client.conversations_history(**kw)
            messages.extend(resp.get("messages", []))
            if not resp.get("has_more"):
                break
            cursor = resp["response_metadata"]["next_cursor"]
            time.sleep(0.5)
        except SlackApiError as e:
            print(f"  Slack API エラー ({channel_id}): {e.response['error']}")
            break
    return [m for m in messages if m.get("text") and not m.get("bot_id")]


def msgs_to_text(msgs: list, max_n: int = 150) -> str:
    return "\n---\n".join(m["text"] for m in msgs[:max_n])


# ── Gemini ────────────────────────────────────────────────────────────────────
def call_gemini(client, prompt: str) -> dict:
    try:
        from google.genai import types as gt
        cfg = gt.GenerateContentConfig(max_output_tokens=8192, temperature=0.8)
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=cfg)
    except (ImportError, TypeError):
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

    raw = resp.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"JSONを抽出できませんでした:\n{raw[:400]}")
    return json.loads(m.group())


# ── HTML 更新 ─────────────────────────────────────────────────────────────────
def update_html(data: dict) -> None:
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "// <<AUTO_ANALYSIS_DATA_START>>" not in content:
        raise RuntimeError("マーカーが見つかりません。prompt_generator.html を確認してください。")

    new_json = json.dumps(data, ensure_ascii=False, indent=2)
    new_block = (
        "// <<AUTO_ANALYSIS_DATA_START>>\n"
        f"const DEFAULT_ANALYSIS = {new_json};\n"
        "// <<AUTO_ANALYSIS_DATA_END>>"
    )
    new_content = re.sub(
        r"// <<AUTO_ANALYSIS_DATA_START>>[\s\S]*?// <<AUTO_ANALYSIS_DATA_END>>",
        new_block, content, count=1
    )
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)


# ── メイン ────────────────────────────────────────────────────────────────────
def main():
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    gemini_key  = os.environ.get("GEMINI_API_KEY")
    if not slack_token:
        raise SystemExit("❌ SLACK_BOT_TOKEN が未設定です（.env を確認）")
    if not gemini_key:
        raise SystemExit("❌ GEMINI_API_KEY が未設定です（.env を確認）")

    # 1. Slack 取得
    print(f"[1/3] Slackからメッセージを取得中（過去{DAYS_BACK}日）...")
    slack    = WebClient(token=slack_token)
    own_msgs = fetch_messages(slack, OWN_CHANNEL_ID)
    cmp_msgs = fetch_messages(slack, COMP_CHANNEL_ID)
    print(f"      自社: {len(own_msgs)}件 / 参照: {len(cmp_msgs)}件")

    # 2. Gemini 生成
    print("[2/3] Geminiで10切り口 + 本文を一括生成中（数十秒かかります）...")
    gemini  = genai.Client(api_key=gemini_key)
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt  = PROMPT_TEMPLATE.format(
        company_ctx = COMPANY_CTX,
        own_label   = "26卒メンバーチャンネル",
        own_count   = len(own_msgs),
        comp_label  = "ClaudeCodeコンテストチャンネル",
        comp_count  = len(cmp_msgs),
        own_text    = msgs_to_text(own_msgs) or "(メッセージなし)",
        comp_text   = msgs_to_text(cmp_msgs) or "(メッセージなし)",
        fetched_at  = now,
        last_updated= now,
    )
    data = call_gemini(gemini, prompt)
    data["last_updated"] = now

    n = len(data.get("items", []))
    if n == 0:
        raise ValueError("items が空です。Geminiのレスポンスを確認してください。")
    print(f"      {n}個の切り口を生成しました")

    # 3. HTML 書き込み
    print("[3/3] prompt_generator.html を更新中...")
    update_html(data)
    print(f"✅ 完了！ブラウザで prompt_generator.html を開いて確認してください。")


if __name__ == "__main__":
    main()
