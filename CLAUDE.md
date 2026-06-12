# XAgent — プロジェクト指針（毎セッション自動読込）

半自動の X 運用ツール。FastAPI + SQLModel/SQLite（バックエンド）/ React+Vite+TS（フロント）/ Typer CLI / FastMCP サーバ。

## まず読む

**作業前に `docs/PROJECT_OVERVIEW.md` を読む。** 機能・データモデル・下書きライフサイクル・API/画面一覧・スケジューラ/デーモン・Runbook・ハマりどころを網羅した一次資料。このファイルは要点と不変の制約だけを置き、詳細はそちらに委ねる。コードを変えたら概要docの該当箇所も同コミットで更新する（特に投稿分岐・状態遷移・API挙動・画面）。

## 不変の背骨（凍結回避・例外なし）

- **自分のXアカウントへの書き込み（投稿/RT/リスト作成・メンバー追加）＝公式X API（tweepy/OAuth1.0a）のみ。** twitterapi.io は**読み取り専用**（ハンドル解決・メンバー閲覧）。書き込みに使わない。
- **自動投稿はしない。** 下書きは必ず人間の承認を要する。スケジューラ/デーモンが投稿してよいのは「ユーザーが承認し予約済みで発火時刻に達した下書き」だけ（`status==QUEUED` かつ `scheduled_at` 設定済みかつ期限到来）。生成系（monitor）は下書きを作るだけで投稿しない。
- **秘密情報（`.env`, `.env.*`, APIキー, credentials, `*.pem`, `*.key`）は読まない/出力しない/コミットしない。**
- 緊急停止は `config.posting_enabled`。予約投稿の発火は常時動かす方針（止めない）。**絡み案の自動生成（monitor_tick）は `MonitorSettings.auto_monitor_enabled` でUIオンオフ（既定OFF・乱造防止。終わったらOFFに戻す運用）**。手動1回は Inbox「監視を1回実行」/ CLI `xagent monitor-once`。絡み案は**直近24時間の候補を1回のバッチAI判断で分散選定**（同一アカウント原則1件・最大2件）し、reply/quote と本文まで AI が決める（`formatter.select_engagements`）。ニュース速報の自動生成（news_tick）も同様に `NewsSettings.auto_news_enabled`（既定OFF）、手動は News 画面「今すぐ生成」。有名人ウォッチ（celeb_tick、AI言及の即時検出）も `MonitorSettings.celeb_watch_enabled`（既定OFF）、手動は Settings「今すぐチェック」。XNewsBot の DB は**読み取り専用**（mode=ro）で、XNewsBot 側のコード・DBは変更しない。
- **LLM 処理は全て Claude Code CLI のヘッドレス使い捨てセッション**（`xagent/claude_cli.py`、subscription/OAuth 課金、モデル `claude-opus-4-8`）。Anthropic API（従量課金）と `anthropic` SDK は使わない。CLI に `--bare` を付けない（OAuth が読めなくなる）。

## 常駐構成（→ memory `xagent-daemon-architecture` と概要doc §10）

- バックエンドは **launchd `com.tomato.xagent`**（KeepAlive=true）が `127.0.0.1:8000` に常駐。API内蔵の `BackgroundScheduler` が `queue_tick`(60s, 予約発火・常時) と `monitor_tick`(180s, 絡み生成・`auto_monitor_enabled`既定OFFで内部制御) と `news_tick`(600s, ニュース速報生成・`auto_news_enabled`既定OFFで内部制御) と `celeb_tick`(600s, 有名人のAI言及検出・`celeb_watch_enabled`既定OFFで内部制御) を回す。**ローカルでは別プロセスの `xagent daemon` は使わない。**
- **リモートアクセス（スマホ）**: `tailscale serve --bg 8000` で tailnet 内に HTTPS 公開（`https://<mac名>.<tailnet>.ts.net` → localhost:8000。uvicorn は 127.0.0.1 のまま、インターネット非公開）。認証は `.env` の `API_TOKEN`（フロントはログイン画面で入力→`X-API-Token` 自動付与）。`/health` と `/media/files` のみ無認証。
- **コード変更は自動反映（2026-06-09〜）**: plist の uvicorn に `--reload --reload-dir <repo>/xagent` を追加済みで、`xagent/` 配下の `.py` を保存した瞬間(約1秒)に worker が再起動し新コードを読む（手動 kickstart 不要）。保険として git `post-commit` フック（`githooks/post-commit`・`core.hooksPath=githooks`）がコミット毎に `kickstart -k`。**「コミットしたのに反映されない」は解消済み**（過去の不具合主因＝再起動忘れ。`lessons.md` 2026-06-09）。
- **手動再起動が要るのは plist変更・依存追加・マイグレーション・.env変更のときだけ**（`.env` は `--reload-dir` の対象外。`API_TOKEN` 等を変えたら kickstart）: plist定義の変更は kickstart では反映されない→ `launchctl bootout gui/$(id -u)/com.tomato.xagent` → `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tomato.xagent.plist`。プロセスだけ作り直すなら `launchctl kickstart -k "gui/$(id -u)/com.tomato.xagent"`。確認は `curl -s localhost:8000/health`。`kill` は launchd が即再生成するので使わない。`--reload` で reloader(親)+worker(子)の2プロセス構成。
- ログ: `~/Library/Logs/xagent.{out,err}.log`（uvicorn は `--log-level warning` なのでINFOは出ない）。
- フロント: 開発は `cd frontend && npm run dev`。**ポートは固定 5180**（`vite.config.ts` の `strictPort:true`、Hermesの5173回避）。`http://localhost:5180/`。**本番UIは `:8000` が `frontend/dist` を配信。フロントを変えたら `npm run build` しないと古いUIが配信され続ける**（API再起動は不要。UIのサイドバー下部に build 時刻表示あり）。

## 頻用コマンド

- テスト: `.venv/bin/python -m pytest -q`
- 構文確認（編集後・フック自動）: `.venv/bin/python -m py_compile <file>`
- フロントビルド/型: `cd frontend && npm run build`

## ハマりどころ（先に知らないと事故る）

- **DB は enum 名を大文字で保存**（`'QUEUED'`, `'APPROVED'`）。小文字SQLは空振りする。状態判定は enum で。
- **日時は内部 naive UTC で統一。** 外部由来の datetime は `service.to_naive_utc()` を通す。フロント表示は naive UTC ISO に `"Z"` 付与、予約送信は JST 壁時計 `:00+09:00`。混在で不正値。
- **状態遷移は `guards.ALLOWED_TRANSITIONS` で強制**（`_set_status`）。違反は `PolicyViolation`。
- **引用の403**: X API v2 の `quote_tweet_id` は引用可ツイートでも403「Quoting this post is not allowed」を返すことがある（手動UIでは可）。`service._post_quote` が本文末尾にURLを埋め込む通常投稿へフォールバックする。安易に「引用不可＝却下」にしない。
- 予約失効: PCオフ等で発火を逃した予約は `reconcile_missed_schedules`（猶予30分）で QUEUED→APPROVED に戻し `schedule_missed=True`。Queueタブ表示時にも復旧を1回実行。

## このプロジェクトの作法（グローバル指針に上乗せ）

- FastAPI は Pydantic で入出力を型付けし、ルータ単位（`xagent/api/routes/`）で分割。
- 周辺リファクタを混ぜない（Surgical）。既存の単一設定 idiom（`style.py`/`StyleProfile`）・素の `<select>`・`components/ui` を再利用し新機構を増やさない。
- UIの選択肢には可能な範囲で「AIに任せる（自動選択）」を用意する方針（型の自動選択など委任系）。
- Git: `main` へ直接 push しない（feature ブランチ経由）。現行ブランチ `feature/templates-and-engage-lists`。
