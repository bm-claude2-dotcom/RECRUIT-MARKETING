#!/usr/bin/env python3
"""
analyze_trends.py  ─ 究極の1ファイル完結型パイプライン

  1. .env から SLACK_BOT_TOKEN / GEMINI_API_KEY を読み込む
  2. Slack から自社・競合の直近メッセージを取得
  3. Gemini（gemini-2.5-flash）で「10選 × ショート文 + 長文」を JSON で一括生成
  4. 内包 HTML テンプレートにデータを埋め込み、prompt_generator.html を新規書き出し
  5. webbrowser.open() でブラウザを自動起動

セットアップ:
    pip install -r requirements.txt
    # .env に SLACK_BOT_TOKEN と GEMINI_API_KEY を記入して実行
    python analyze_trends.py
"""

import json
import os
import time
import webbrowser
import pathlib
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


# ── 会社情報コンテキスト ──────────────────────────────────────────────────────
COMPANY_CTX = """\
【株式会社プリンシプル（Principle Co.,Ltd.）】
・事業: データ解析を軸としたデジタルマーケティング（GA4・GTM・BigQuery・SEO・Web広告など）
・Mission: データとアクションをつなぎ、よりよい世界を実現します
・Vision: 世界で最も信頼されるマーケティングDXパートナー
・Value: 自立したプロフェッショナル / Win-Win / 世界基準・多様性
・従業員数: 約100名、東京都千代田区
・採用ターゲット: 新卒・大学3-4年生。「誰と働くか」「成長できるか」「入社後のリアル」「ビジョンが叶うか」
・トンマナ: 誠実・等身大・データドリブン。煽らない・誇張しない。確証のない情報は [要確認: ◯◯] で明示。"""


# ── Gemini プロンプト ─────────────────────────────────────────────────────────
PROMPT_TEMPLATE = """\
あなたは株式会社プリンシプルの採用マーケティング専門家です。
以下のSlackデータを分析し、採用広報コンテンツ企画を【必ず10個】生成してください。
各企画に「X（Twitter）用ショート文」と「採用ブログ用の長文本文」を同時に生成してください。
以下のJSONスキーマに厳密に従い、JSONのみ出力してください（説明文不要）。

{company_ctx}

## 自社Slackメッセージ（{own_label}） - 直近{days_back}日間 {own_count}件
{own_text}

## 参照Slackメッセージ（{comp_label}） - {comp_count}件
{comp_text}

## 生成ルール
- 10個の企画はすべて異なる切り口（成長/技術/カルチャー/リアル/働く人/ビジョンなど複数軸）
- Slackの具体的なエピソード・雰囲気を最大限に反映させること（抽象的テーマより実体験ベース）
- short_text: X用、ハッシュタグ3〜4個込みで130〜180文字
- long_text: ブログ・長文SNS用、ハッシュタグ3〜5個込みで380〜550文字
- 確証のない情報は [要確認: ◯◯] と明記
- category は次の6つから選択: 成長 / 技術 / カルチャー / リアル / 働く人 / ビジョン

## 出力JSONスキーマ（items 10件必須）
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
      "hook": "切り口の狙い・フック（60文字以内）",
      "category": "カテゴリ名",
      "short_text": "X用ショート文（130〜180文字・ハッシュタグ込み）",
      "long_text": "ブログ用長文本文（380〜550文字・ハッシュタグ込み）"
    }},
    ... 合計10件 ...
  ]
}}"""


# ── HTML テンプレート（__DATA_JSON__ をデータで置換して書き出す） ───────────────
# raw string を使用: JavaScript 内の \n がそのまま保持される
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>採用コンテンツ 生成ノート | PRINCIPLE</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN", Meiryo, sans-serif;
      background: #eef0f4;
      color: #1a1a2e;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    /* ── Header ─────────────────────────────────── */
    .hd {
      flex-shrink: 0;
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #1a3a5c 100%);
      color: #fff;
      padding: 20px 28px 16px;
    }
    .hd-brand {
      font-size: 10px; font-weight: 700; letter-spacing: 0.3em;
      color: #475569; margin-bottom: 5px;
    }
    .hd-title { font-size: 22px; font-weight: 700; margin-bottom: 10px; }
    .hd-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .badge-live {
      display: inline-flex; align-items: center; gap: 5px;
      background: #dcfce7; color: #15803d;
      font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 20px;
    }
    .badge-live::before { content: "●"; font-size: 7px; color: #22c55e; }
    .hd-meta { font-size: 12px; color: #64748b; }

    /* ── 2-column layout ─────────────────────────── */
    .layout { flex: 1; display: flex; overflow: hidden; min-height: 0; }

    /* ── Left column (card list) ──────────────────── */
    .col-l {
      width: 320px; flex-shrink: 0;
      overflow-y: auto;
      padding: 14px 10px 40px 14px;
      border-right: 1px solid #e2e8f0;
      background: #f8fafc;
    }
    .col-l-label {
      font-size: 10px; font-weight: 700; letter-spacing: 0.15em;
      color: #94a3b8; text-transform: uppercase;
      padding: 4px 4px 10px;
    }
    .card {
      background: #fff;
      border: 2px solid transparent;
      border-radius: 11px;
      padding: 11px 13px;
      text-align: left; cursor: pointer; width: 100%;
      display: flex; flex-direction: column; gap: 5px;
      margin-bottom: 7px;
      box-shadow: 0 1px 3px rgba(0,0,0,.05);
      transition: border-color .15s, box-shadow .15s;
    }
    .card:hover { border-color: #c7d2fe; box-shadow: 0 2px 8px rgba(99,102,241,.1); }
    .card.on { border-color: #6366f1; background: #fafbff; box-shadow: 0 0 0 4px rgba(99,102,241,.08); }
    .card-row { display: flex; align-items: center; gap: 7px; }
    .num {
      width: 25px; height: 25px; border-radius: 6px;
      background: #f1f5f9; color: #64748b;
      font-size: 11px; font-weight: 700;
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0; transition: background .15s, color .15s;
    }
    .card.on .num { background: #6366f1; color: #fff; }
    .cat { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; }
    .card-title { font-size: 13px; font-weight: 700; color: #1e293b; line-height: 1.4; }
    .card.on .card-title { color: #4338ca; }
    .card-hook { font-size: 11px; color: #64748b; line-height: 1.5; }

    /* ── Right column (preview) ───────────────────── */
    .col-r {
      flex: 1; overflow-y: auto;
      padding: 14px 18px 40px;
    }
    .pv-wrap {
      background: #fff;
      border-radius: 14px;
      border: 1.5px solid #e2e8f0;
      box-shadow: 0 2px 8px rgba(0,0,0,.05);
      display: flex; flex-direction: column;
      min-height: calc(100% - 8px);
      overflow: hidden;
    }

    /* Empty state */
    .empty {
      flex: 1;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      gap: 10px; color: #94a3b8; font-size: 14px;
      line-height: 1.9; text-align: center; padding: 60px 40px;
    }
    .empty-icon { font-size: 28px; color: #cbd5e1; }

    /* Preview header */
    .pv-hd { padding: 18px 22px 12px; border-bottom: 1px solid #f1f5f9; }
    .pv-title { font-size: 18px; font-weight: 700; color: #1e293b; margin-bottom: 4px; }
    .pv-hook { font-size: 13px; color: #6366f1; font-weight: 500; }

    /* Tabs */
    .tabs {
      display: flex; padding: 0 22px;
      background: #fafbff; border-bottom: 1px solid #f1f5f9;
    }
    .tab {
      padding: 10px 18px; font-size: 13px; font-weight: 600;
      color: #94a3b8; background: none; border: none;
      border-bottom: 2.5px solid transparent;
      cursor: pointer; margin-bottom: -1px;
      transition: color .15s, border-color .15s;
    }
    .tab:hover { color: #6366f1; }
    .tab.on { color: #6366f1; border-bottom-color: #6366f1; }

    /* Text block */
    .txt-block {
      flex: 1; display: flex; flex-direction: column;
      padding: 18px 22px 20px; gap: 14px;
    }
    .txt-body {
      flex: 1;
      background: #f8fafc;
      border-left: 3px solid #6366f1;
      border-radius: 0 10px 10px 0;
      padding: 14px 16px;
      font-size: 14px; line-height: 1.9; color: #334155;
      word-break: break-all; min-height: 100px;
    }
    .txt-ft { display: flex; align-items: center; justify-content: space-between; }
    .char-cnt { font-size: 12px; color: #94a3b8; }
    .copy-btn {
      background: #6366f1; color: #fff; border: none;
      border-radius: 8px; padding: 8px 20px;
      font-size: 13px; font-weight: 600; cursor: pointer;
      transition: background .2s; white-space: nowrap;
    }
    .copy-btn:hover { background: #4f46e5; }
    .copy-btn.done { background: #16a34a; }

    /* Scrollbar */
    .col-l::-webkit-scrollbar,
    .col-r::-webkit-scrollbar { width: 4px; }
    .col-l::-webkit-scrollbar-track,
    .col-r::-webkit-scrollbar-track { background: transparent; }
    .col-l::-webkit-scrollbar-thumb,
    .col-r::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 4px; }
  </style>
</head>
<body>

<header class="hd">
  <div class="hd-brand">PRINCIPLE</div>
  <div class="hd-title">採用コンテンツ 生成ノート</div>
  <div class="hd-row" id="hd-row"></div>
</header>

<div class="layout">
  <div class="col-l">
    <div class="col-l-label">特選 10 切り口</div>
    <div id="cards"></div>
  </div>
  <div class="col-r">
    <div class="pv-wrap" id="pv"></div>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;

const CAT = {
  '成長':       { bg: '#e0f2fe', fg: '#0369a1' },
  '技術':       { bg: '#ede9fe', fg: '#6d28d9' },
  'カルチャー': { bg: '#fef3c7', fg: '#92400e' },
  'リアル':     { bg: '#fce7f3', fg: '#9d174d' },
  '工く人':     { bg: '#dcfce7', fg: '#15803d' },
  'ビジョン':  { bg: '#fff7ed', fg: '#c2410c' }
};

var sel = null;
var tab = 'short';

function cc(c)  { return CAT[c] || { bg: '#f1f5f9', fg: '#475569' }; }
function e(s)   { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function pad(n) { return ('0' + n).slice(-2); }

function renderStatus() {
  var el = document.getElementById('hd-row');
  if (DATA.last_updated) {
    el.innerHTML =
      '<span class="badge-live"> 実データに更新済み: ' + e(DATA.last_updated) + '</span>' +
      '<span class="hd-meta">自社 ' + DATA.slack_status.company_messages + '件 ／ 参照 ' + DATA.slack_status.competitor_messages + '件</span>';
  }
}

function renderCards() {
  var el = document.getElementById('cards');
  el.innerHTML = DATA.items.map(function(it) {
    var on = sel === it.id;
    var c  = cc(it.category);
    return (
      '<button class="card' + (on ? ' on' : '') + '" data-id="' + it.id + '" onclick="pick(+this.dataset.id)">' +
        '<div class="card-row">' +
          '<span class="num">' + pad(it.id) + '</span>' +
          '<span class="cat" style="background:' + c.bg + ';color:' + c.fg + '">' + e(it.category) + '</span>' +
        '</div>' +
        '<div class="card-title">' + e(it.title) + '</div>' +
        '<div class="card-hook">'  + e(it.hook)  + '</div>' +
      '</button>'
    );
  }).join('');
}

function renderPreview() {
  var el = document.getElementById('pv');
  if (!sel) {
    el.innerHTML =
      '<div class="empty">' +
        '<div class="empty-icon">←</div>' +
        '<div>左のカードを選択すると</div>' +
        '<div>コンテンツが表示されます</div>' +
      '</div>';
    return;
  }
  var it  = DATA.items.find(function(i) { return i.id === sel; });
  var txt = tab === 'short' ? (it.short_text || '') : (it.long_text || '');
  var cnt = Array.from(txt).length;
  el.innerHTML =
    '<div class="pv-hd">' +
      '<div class="pv-title">' + e(it.title) + '</div>' +
      '<div class="pv-hook">'  + e(it.hook)  + '</div>' +
    '</div>' +
    '<div class="tabs">' +
      '<button class="tab' + (tab === 'short' ? ' on' : '') + '" data-t="short" onclick="sw(this.dataset.t)">𝕏 用ショート文</button>' +
      '<button class="tab' + (tab === 'long'  ? ' on' : '') + '" data-t="long"  onclick="sw(this.dataset.t)">ブログ用長文</button>' +
    '</div>' +
    '<div class="txt-block">' +
      '<div class="txt-body">' + e(txt).replace(/\n/g, '<br>') + '</div>' +
      '<div class="txt-ft">' +
        '<span class="char-cnt">' + cnt + ' 文字</span>' +
        '<button class="copy-btn" id="cb" onclick="doCopy()">📋 コピー</button>' +
      '</div>' +
    '</div>';
}

function pick(id) {
  sel = (sel === id) ? null : id;
  tab = 'short';
  renderCards();
  renderPreview();
}

function sw(t) { tab = t; renderPreview(); }

function doCopy() {
  var it  = DATA.items.find(function(i) { return i.id === sel; });
  if (!it) return;
  var txt = tab === 'short' ? it.short_text : it.long_text;
  var btn = document.getElementById('cb');
  var done = function() {
    btn.textContent = 'コピー完了! ✓';
    btn.classList.add('done');
    setTimeout(function() {
      btn.textContent = '📋 コピー';
      btn.classList.remove('done');
    }, 2000);
  };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(txt).then(done);
  } else {
    var ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    done();
  }
}

renderStatus();
renderCards();
renderPreview();
</script>
</body>
</html>"""


# ── Slack ─────────────────────────────────────────────────────────────────────
def fetch_messages(client: WebClient, channel_id: str) -> list:
    oldest   = str((datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).timestamp())
    messages = []
    cursor   = None
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
        except SlackApiError as ex:
            print(f"  Slack API エラー ({channel_id}): {ex.response['error']}")
            break
    return [m for m in messages if m.get("text") and not m.get("bot_id")]


def msgs_to_text(msgs: list, max_n: int = 150) -> str:
    return "\n---\n".join(m["text"] for m in msgs[:max_n])


# ── Gemini ────────────────────────────────────────────────────────────────────
def call_gemini(client, prompt: str) -> dict:
    try:
        from google.genai import types as gt
        cfg = gt.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=16384,
            temperature=0.8,
        )
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=cfg)
    except (ImportError, TypeError) as ex:
        print(f"  設定なしでフォールバック: {ex}")
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)

    raw = resp.text.strip()
    # mime_type 非対応時のマークダウン除去フォールバック
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0].strip()

    return json.loads(raw)


# ── HTML 書き出し ──────────────────────────────────────────────────────────────
def write_html(data: dict) -> None:
    safe_json = json.dumps(data, ensure_ascii=False)
    # </script> がデータ内にあるとスクリプトタグを閉じてしまうため回避
    safe_json = safe_json.replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__DATA_JSON__", safe_json)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)


# ── メイン ─────────────────────────────────────────────────────────────────────
def main():
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    gemini_key  = os.environ.get("GEMINI_API_KEY")
    if not slack_token:
        raise SystemExit("❌ SLACK_BOT_TOKEN が未設定です（.env を確認）")
    if not gemini_key:
        raise SystemExit("❌ GEMINI_API_KEY が未設定です（.env を確認）")

    # 1. Slack 取得
    print(f"[1/3] Slack からメッセージを取得中（過去 {DAYS_BACK} 日）...")
    slack    = WebClient(token=slack_token)
    own_msgs = fetch_messages(slack, OWN_CHANNEL_ID)
    cmp_msgs = fetch_messages(slack, COMP_CHANNEL_ID)
    print(f"      自社: {len(own_msgs)}件 ／ 参照: {len(cmp_msgs)}件")

    # 2. Gemini 生成（Slack テキスト内の { } を .format() 干渉から保護）
    print("[2/3] Gemini で 10選 × (X用ショート文 + ブログ用長文) を一括生成中...")
    gemini = genai.Client(api_key=gemini_key)
    now    = datetime.now().strftime("%Y-%m-%d %H:%M")
    own_text_safe = msgs_to_text(own_msgs).replace("{", "{{").replace("}", "}}")
    cmp_text_safe = msgs_to_text(cmp_msgs).replace("{", "{{").replace("}", "}}")
    prompt = PROMPT_TEMPLATE.format(
        company_ctx  = COMPANY_CTX,
        own_label    = "26卒メンバーチャンネル",
        own_count    = len(own_msgs),
        comp_label   = "ClaudeCodeコンテストチャンネル",
        comp_count   = len(cmp_msgs),
        days_back    = DAYS_BACK,
        own_text     = own_text_safe or "(メッセージなし)",
        comp_text    = cmp_text_safe or "(メッセージなし)",
        last_updated = now,
    )
    data = call_gemini(gemini, prompt)
    data["last_updated"] = now
    n = len(data.get("items", []))
    if n == 0:
        raise ValueError("items が空です。Gemini のレスポンスを確認してください。")
    print(f"      {n} 個の切り口を生成しました")

    # 3. HTML 書き出し
    print("[3/3] prompt_generator.html を生成中...")
    write_html(data)
    print(f"      → {HTML_PATH}")

    # 4. ブラウザ自動起動
    url = pathlib.Path(HTML_PATH).as_uri()
    webbrowser.open(url)
    print("✅ 完了！ブラウザが自動で開きます。")


if __name__ == "__main__":
    main()
