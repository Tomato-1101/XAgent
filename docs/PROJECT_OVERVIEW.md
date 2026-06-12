# XAgent プロジェクト全体ドキュメント

半自動X(旧Twitter)運用ツール XAgent の恒久的な参照ドキュメント。読者は「将来このプロジェクトを担当するエンジニア/AIエージェント」を想定する。機能の挙動・フロー・ポリシー・エッジケースを厚く記述し、技術スタックの詳細は簡潔にとどめる。

正確性を最優先とし、関数/フィールド/エンドポイント/状態の実名を保つ。記述の根拠が調査済みコード(またはテスト)である。本体未読で推測の箇所は【推測】、確認できなかった箇所は【未確認】と明示する。

## 目次

1. [プロジェクト概要](#1-プロジェクト概要)
2. [不変の制約・ポリシー](#2-不変の制約ポリシー)
3. [アーキテクチャ概観](#3-アーキテクチャ概観)
4. [データモデル](#4-データモデル)
5. [下書きライフサイクル(状態機械)](#5-下書きライフサイクル状態機械)
6. [機能カタログ](#6-機能カタログ)
7. [APIエンドポイント一覧](#7-apiエンドポイント一覧)
8. [フロントエンド画面一覧](#8-フロントエンド画面一覧)
9. [CLIコマンド / MCPツール一覧](#9-cliコマンド--mcpツール一覧)
10. [監視・絡み生成・スケジューラ・デーモンの動作詳細](#10-監視絡み生成スケジューラデーモンの動作詳細)
11. [運用Runbook](#11-運用runbook)
12. [既知の注意点・ハマりどころ](#12-既知の注意点ハマりどころ)

付録: [環境変数一覧](#付録a-環境変数一覧) / [パッケージング・依存関係](#付録b-パッケージング依存関係) / [Claude Code連携資産](#付録c-claude-code連携資産)

---

## 1. プロジェクト概要

XAgent は、テキストを投げると AI(Claude)が運用者のノウハウ口調へ整形し → **人間が承認** → X へ半自動で投稿/エンゲージするエージェントである。React 製の Web UI ダッシュボード、Typer 製 CLI、FastMCP 製 MCP サーバ(Claude Code 連携)の3つの操作面を持ち、いずれも同一のサービス層 (`xagent.service`) の上に乗る。

対象ユーザーは、X を専業に近い形で本気で運用するオーナー。精度・リアルタイム性を最優先し、コストを厭わない運用を前提とする。設計の背骨は「完全自動はしない」こと(human-in-the-loop)で、AI が下書きを作るところまでを自走させ、送信(`post`)は必ず人間の承認を経る。投稿頻度ガード・制限時間帯(ブラックアウト)・緊急停止スイッチを内蔵し、X 規約違反や凍結リスクを決定論的に回避する。

---

## 2. 不変の制約・ポリシー

これらは全モジュール横断の不変条件であり、変更時は影響範囲が広い。各項目の enforcement 位置を併記する。

- **完全自動はしない(承認ゲート)**。X 規約上、自動いいね/フォロー/RT/一斉リプライは禁止のため、AI は下書き(`status=DRAFT`)を作るだけで投稿はしない。投稿経路は `service.post_draft` に集約され、CLI/API/MCP/scheduler の全経路がこのガードを通る(§5, §6)。
- **書き込みは公式 X API のみ**。自分のアカウント操作(投稿/リプライ/引用/リポスト/リスト書込)はすべて tweepy(公式 X API、OAuth1.0a)で行う。非公式 API で自アカウントを操作すると BAN リスクがあるため(`x_client.py` / `twitterapi_client.py` docstring)。
- **twitterapi.io は読み取り専用**。他人の投稿の読み取り(元投稿取得・監視・検索・学習)はコスト削減のため twitterapi.io を優先し、`TwitterApiIoError` 等の失敗時のみ公式 X へフォールバックする。読み取りは資格情報を使わないため BAN とは無関係。`TWITTERAPI_IO_KEY` 未設定なら読み取りも全て公式 X(オプトイン方式)。
- **デーモンは承認済み予約のみ投稿する**。常駐スケジューラの `SCHEDULED` 経路は「`status==QUEUED` かつ `scheduled_at` が設定済み かつ 到来済み」の3条件をすべて満たす下書きだけを投稿する(誤爆防止の要、`ensure_post_authorized`)。
- **ブラックアウト(制限時間帯)**。平日の指定帯(JST、既定 月〜金の 09:00–12:00 / 13:00–19:00)は自分の公開書き込みを一切ブロックする。監視(読み取り)はブロックしない。二段階確認を通した `override` または予約時に保存した `Draft.blackout_override` でのみ突破できる(§10)。
- **頻度ガード**。1日上限(自然な範囲、既定10件)・ハード上限(既定100件)・連投の最小間隔(既定300秒)を `RateLimiter` が決定論的に判定する(§6)。
- **緊急停止スイッチ**。`config.posting_enabled=False` で手動/予約とも全投稿を即停止する。これが他のすべてのガードに優先する(キルスイッチ)。
- **秘密情報の非読込**。API キー等は `.env` / 環境変数で与え、コードに置かない。全 API キーは Optional で、未設定でも import 時にクラッシュしない(実行時に未設定なら明示エラー)。`.env` / `*.db` 等は `.gitignore` 済み。

---

## 3. アーキテクチャ概観

### プロセス構成

- **API プロセス(常駐)**: FastAPI (`xagent/api/main.py`) を uvicorn で起動。launchd (`com.tomato.xagent`、`KeepAlive` 有効)で `127.0.0.1:8000` に常駐する。
- **内蔵 BackgroundScheduler**: API プロセスの `lifespan` 内で `init_db()` 後、`scheduler_enabled` が真なら APScheduler の `BackgroundScheduler(timezone="UTC")` を生成。別デーモンを起こさず同1プロセスで予約投稿(`queue_tick`、既定60秒)・絡み案生成(`monitor_tick`、既定180秒)・ニュース速報生成(`news_tick`、既定600秒)・有名人ウォッチ(`celeb_tick`、既定600秒)を回す。**ただし monitor は `MonitorSettings.auto_monitor_enabled`、news は `NewsSettings.auto_news_enabled`、celeb は `MonitorSettings.celeb_watch_enabled`(いずれも既定OFF)でゲートされ、OFFの間は即returnしてAPIを消費しない**(UIトグルでオンオフ。乱造防止のため既定OFF＝必要な時だけオンにする)。launchd 常駐構成では実際に回るのはこの内蔵スケジューラ。
- **フロントエンド**: Vite + React + TypeScript の SPA (`frontend/`)。開発時は `vite.config.ts` で `port: 5180, strictPort: true` に固定(自動フォールバックしない)。**本番UIは FastAPI が `frontend/dist` を同一オリジンで配信**(`GET /` が index.html を no-cache 返却、`/assets` を StaticFiles マウント。dist 未ビルドなら `/` は 503 で自己説明エラー)。
- **リモートアクセス(スマホ)**: `tailscale serve --bg 8000` で tailnet 内に HTTPS プロキシ(`https://<mac名>.<tailnet>.ts.net` → `127.0.0.1:8000`)。uvicorn のバインドは `127.0.0.1` のまま変えない(インターネット非公開)。スマホは Tailscale アプリで接続し、ブラウザで上記 URL を開く。同一オリジン配信のため CORS 設定は不要。認証は `API_TOKEN`(下記 §7)。
- **永続化**: SQLite (`xagent.db`、`DB_PATH` 既定)。SQLModel(SQLAlchemy 上)経由でアクセスし、生 SQL は原則不使用。

### 主要モジュール地図

| モジュール | 役割 |
|---|---|
| `xagent/service.py` | コアサービス層。下書きの生成・状態遷移・投稿を束ねる。`post_draft` が投稿の唯一の経路 |
| `xagent/guards.py` | 投稿頻度リミッタ・承認ゲート・状態遷移表(`ALLOWED_TRANSITIONS`)・例外階層 |
| `xagent/models.py` | SQLModel エンティティと Enum 定義 |
| `xagent/db.py` | エンジン・初期化・冪等マイグレーション・シード |
| `xagent/config.py` | `.env` 設定(`Settings`) |
| `xagent/formatter.py` | LLM(Claude)抽象。整形・案複数生成・返信/引用生成・絡み候補/ニュース速報のバッチ選定 |
| `xagent/claude_cli.py` | Claude Code CLI のヘッドレス使い捨てセッション実行(`run_claude`、subscription課金) |
| `xagent/prompts.py` | バズの型(playbook)定数(A〜P / R1〜R6 / 引用 / ニュース速報N1〜N5) |
| `xagent/templates.py` | 「型」(`PromptTemplate`)の CRUD・既定切替・AI 自動選択・シード |
| `xagent/style.py` | 文体ガイド(`StyleProfile`)・過去投稿学習(`PastPost`) |
| `xagent/text.py` | X 加重文字数・折りたたみ判定・スレッド分割(LLM 不使用) |
| `xagent/profiles.py` | アカウント学習・プロフィール抽出(`AccountProfile`) |
| `xagent/cost.py` | API 従量課金の記録(`ApiCostLog`) |
| `xagent/media.py` | メディアの保存・種別判定・検証 |
| `xagent/lists.py` | X ネイティブ「リスト」の作成・一括メンバー追加 |
| `xagent/commands.py` | 自由文「指令」パーサ |
| `xagent/maintenance.py` | DB 容量管理(古い端末状態の物理削除) |
| `xagent/notify.py` | macOS 通知(承認待ち) |
| `xagent/blackout.py` | 制限時間帯の設定・判定 |
| `xagent/scheduler.py` | 最適時間スロット算出・予約キュー消化 |
| `xagent/monitor.py` | 受信監視・絡み案生成 |
| `xagent/news.py` | ニュース速報の下書き生成(XNewsBotのDBを読み取り専用で参照) |
| `xagent/daemon.py` | 常駐ティック関数(`monitor_tick` / `news_tick` / `queue_tick`)と `run` |
| `xagent/x_client.py` | `XClient`(公式 X API ラッパ、書込/読取の窓口) |
| `xagent/twitterapi_client.py` | `TwitterApiIoClient`(twitterapi.io 読取専用バックエンド) |
| `xagent/cli.py` | Typer CLI |
| `xagent/mcp_server.py` | FastMCP サーバ(Claude Code 連携) |
| `xagent/api/` | FastAPI ルータ群 + 共通基盤(`deps.py` / `schemas.py`) |

---

## 4. データモデル

### 全体規約

- **時刻は naive UTC に統一**。`models._utcnow()` が `datetime.now(timezone.utc).replace(tzinfo=None)` を返す。SQLite が tz 情報を保持しないため、内部表現を naive UTC に揃えて比較ズレを防ぐ。`templates._utcnow` / `service._utcnow` も同実装。`service.to_naive_utc(dt)` が外部入力(aware/naive)を内部表現へ正規化する(§12)。
- タイムスタンプ列の既定値は `Field(default_factory=_utcnow)`。`updated_at` に自動更新トリガはなく、更新側コードが明示的に `_utcnow()` を代入する必要がある。
- JSON を文字列カラムとして保持する設計が多い(`segments_json`, `media_paths_json`, `active_hours_json`, `profile_json`, `weekdays_json`, `windows_json`)。パース/直列化は各サービス層が担う。
- 全テーブルが `id: int | None = Field(default=None, primary_key=True)` を共通で持つ。

### Enum 一覧

| Enum | 値(リテラル) | 用途・補足 |
|---|---|---|
| `DraftKind` | `POST="post"`, `REPLY="reply"`, `QUOTE="quote"`, `REPOST="repost"` | 下書きの種別。REPOST はコメント無しリポスト(自分の過去投稿の再拡散用) |
| `DraftStatus` | `DRAFT="draft"`, `APPROVED="approved"`, `QUEUED="queued"`, `POSTED="posted"`, `REJECTED="rejected"`, `CANCELED="canceled"` | 下書きの状態。CANCELED は「ゴミ箱」=DB に残し、容量超過時に古い順で物理削除 |
| `TargetKind` | `MANUAL="manual"`, `LIST="list"`, `GENRE="genre"`, `FOLLOWING="following"` | 絡み対象の種別。LIST は `list_id` のメンバーを巡回時に毎回展開、GENRE は `keyword` 探索 |
| `TemplateKind` | `POST="post"`, `REPLY="reply"`, `QUOTE="quote"`, `NEWS="news"` | 「型」のカテゴリ。POST=バズの型A〜P、REPLY=絡みリプの型R1〜R6、QUOTE=引用RTの型、NEWS=ニュース速報の型N1〜N5(大手の実投稿から抽出) |
| `CostKind` | `READ="read"`, `WRITE="write"`, `TL="tl"`, `LLM="llm"` | コストログ種別 |
| `PostTrigger` | `MANUAL="manual"`, `SCHEDULED="scheduled"` | 投稿の発火経路(`guards.py`)。この2値のみが投稿を許す |

すべて `str` 混入の Enum なので、DB には文字列値が保存され、API/JSON でもその文字列で表現される。

### テーブルエンティティ

#### `Draft`(`draft`)— 投稿/リプライ/引用RT/リポストの下書き

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `kind` | `DraftKind` | `POST` | 種別 |
| `status` | `DraftStatus` | `DRAFT` | 状態 |
| `source_text` | `str` | `""` | 自分/エージェントの入力(整形前の種) |
| `segments_json` | `str` | `"[]"` | 整形後のセグメント(スレッド)を JSON 配列で保持 |
| `media_paths_json` | `str` | `"[]"` | 添付画像のローカルパス(任意) |
| `target_tweet_id` | `str?` | `None` | reply/quote/repost の対象ツイートID |
| `target_handle` | `str?` | `None` | 表示用ハンドル |
| `target_text` | `str` | `""` | 絡む相手の元ポスト本文(`source_text` とは別) |
| `target_created_at` | `datetime?` | `None` | 元ポストの投稿時刻(naive UTC、取得できた時のみ)。鮮度判断用 |
| `scheduled_at` | `datetime?` | `None` | 予約時刻/最適時間 |
| `posted_at` | `datetime?` | `None` | 投稿完了時刻 |
| `posted_tweet_id` | `str?` | `None` | 投稿後のツイートID |
| `blackout_override` | `bool` | `False` | 制限帯でも投稿許可か。二段階確認を通すと True。予約発火時(無人)に投稿許可を列で保持 |
| `schedule_missed` | `bool` | `False` | 予約時刻に投稿できず失効した印。再予約(`queue_draft`)時にクリア |
| `created_at` / `updated_at` | `datetime` | `_utcnow` | `updated_at` は自動更新なし |

#### `EngageTarget`(`engagetarget`)— 絡む対象

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `kind` | `TargetKind` | `MANUAL` | 対象種別 |
| `handle` | `str?` | `None` | @なしスクリーンネーム。kind=LIST ではリスト名(表示用) |
| `user_id` | `str?` | `None` | X user id |
| `list_id` | `str?` | `None` | kind=LIST 時の X リストID(巡回時に現メンバーへ毎回展開) |
| `keyword` | `str?` | `None` | genre 探索用 |
| `active` | `bool` | `True` | 有効フラグ |
| `notes` | `str?` | `None` | メモ |
| `created_at` | `datetime` | `_utcnow` | |

#### `MonitorCursor`(`monitorcursor`)— 監視ストリームの since_id

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `stream` | `str` | `index=True` | ストリーム識別子。例 `"mentions"` / `"target:<user_id>"` / `"genre:<keyword>"` / `"follow:<user_id>"` |
| `last_seen_id` | `str?` | `None` | 最終取得ID(重複処理回避) |
| `updated_at` | `datetime` | `_utcnow` | |

#### `StyleProfile`(`styleprofile`)— 常時適用の口調ガイド【単一行 default 運用】

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `name` | `str` | `"default"` | プロファイル名 |
| `guide_text` | `str` | `""` | 文章で指定する口調・NG・テンプレ |
| `active` | `bool` | `True` | 有効フラグ |
| `created_at` | `datetime` | `_utcnow` | |

#### `PromptTemplate`(`prompttemplate`)— 投稿/リプ生成の「型」

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `name` | `str` | `""` | 型名 |
| `kind` | `TemplateKind` | `POST` | カテゴリ |
| `body` | `str` | `""` | 型の中身(LLM へ渡す指示文) |
| `active` | `bool` | `False` | 同 kind で1つだけ既定。monitor の自動生成が使う |
| `builtin` | `bool` | `False` | シード投入(buzz-playbook)の目印 |
| `created_at` / `updated_at` | `datetime` | `_utcnow` | |

不変条件: **同 kind で `active=True` は最大1件**。DB 制約ではなく `templates.set_active()` が対象 kind の全行を `o.active = (o.id == template_id)` で一括代入して保証する。`active_body(kind)` は active な先頭1件の `body`(無ければ `""`)を返し、monitor の自動リプ/引用が既定として参照する。

#### `PastPost`(`pastpost`)— 学習用に取得した投稿

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `tweet_id` | `str` | `index=True` | ツイートID(UNIQUE 制約はなくインデックスのみ) |
| `text` | `str` | `""` | 本文 |
| `created_at` | `datetime?` | `None` | 元ツイートの投稿時刻 |
| `like_count` / `retweet_count` | `int` | `0` | |
| `fetched_at` | `datetime` | `_utcnow` | 取得時刻 |
| `author_user_id` | `str?` | `index=True` | どのアカウントの投稿か(他人学習で追加) |
| `author_handle` | `str?` | `None` | |
| `is_own` | `bool` | `True` | 自分の投稿か(既定 True=後方互換) |

#### `AccountProfile`(`accountprofile`)— アカウント単位の抽出プロファイル

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `handle` | `str` | `index=True` | ハンドル(一意キーとして扱う) |
| `user_id` | `str?` | `index=True` | X user id |
| `is_self` | `bool` | `False` | 自分のアカウントか |
| `display_name` | `str?` | `None` | 表示名 |
| `posts_fetched` | `int` | `0` | 取得済み投稿数 |
| `avg_likes` / `avg_retweets` | `float` | `0.0` | 平均いいね/RT |
| `active_hours_json` | `str` | `"[]"` | 投稿時間帯(JST hour→count)。`created_at` から純計算 |
| `profile_json` | `str` | `"{}"` | 構造化プロファイル(tone/themes/post_style/hooks/hashtags/cadence/summary) |
| `profile_text` | `str` | `""` | AI の散文サマリ(整形プロンプトに乗せる。`summary` のみ入る) |
| `extracted_at` / `updated_at` | `datetime` | `_utcnow` | |

設計コメント: 常時適用の口調は `StyleProfile.guide_text`、`AccountProfile` は「誰を真似るか(emulate)」を選択時のみ使う。自分のアカウントも他人と同列に扱い、`is_self` で区別する。

#### `MonitorSettings`(`monitorsettings`)— 監視ソースのオン/オフ【単一行 id=1 運用】

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `mentions_enabled` | `bool` | `False` | メンション監視(自分のポストへの返信)。**既定OFF=手動で返す方針**。トグルは残すのでONにすれば `poll_mentions` で返信案を生成できる |
| `manual_targets_enabled` | `bool` | `True` | 手動リスト対象の監視(MANUAL+LIST の両方を支配) |
| `keyword_search_enabled` | `bool` | `False` | キーワード探索(コスト高ゆえ既定オフ) |
| `following_enabled` | `bool` | `False` | フォロー中の監視(コスト高ゆえ既定オフ) |
| `auto_monitor_enabled` | `bool` | `False` | 絡み案の自動生成(monitor_tick)を回すか。UI(Settings)のトグルで制御。乱造防止のため既定OFF。OFFなら即returnしAPI消費なし |
| `auto_post_enabled` | `bool` | `True` | 「現在は未使用」。予約投稿は常時実行方針で、緊急停止は `config.posting_enabled` が担う。互換のため列のみ残存 |
| `max_drafts_per_run` | `int` | `10` | 1監視サイクルで作る下書き総数上限(全ソース横断で共有) |
| `min_impressions` | `int` | `10000` | 絡み候補の最低インプレッション(view数)。未満は候補から除外(伸びていない投稿へのリプは露出が取れない)。view数が取れない投稿(公式APIフォールバック経路)は判定不能として通す。**有名人ウォッチは対象外** |
| `celeb_watch_enabled` | `bool` | `False` | 有名人ウォッチ(celeb_tick)を回すか。UI(Settings)のトグルで制御 |
| `celeb_list_id` | `str?` | `None` | 有名人ウォッチ対象のXリストID(メンバーのAI言及投稿を検索で検出) |
| `updated_at` | `datetime` | `_utcnow` | (`set_monitor_settings` 内で明示更新しない) |

#### `NewsSettings`(`newssettings`)— ニュース速報生成の設定【単一行運用】

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `auto_news_enabled` | `bool` | `False` | ニュース速報の自動生成(news_tick)を回すか。UI(News)のトグルで制御。乱造防止のため既定OFF |
| `genres_json` | `str` | `'["AI", "テクノロジー"]'` | 対象ジャンル(XNewsBot の `genredigest.genre` と突合)の JSON 配列 |
| `max_posts_per_run` | `int` | `3` | 1回の生成で作る下書き数上限(バッチAI選定の `max_n`) |
| `last_digest_id` | `int` | `0` | 処理済みダイジェストのカーソル(`genredigest.id`)。同じニュースを二度生成しない |
| `updated_at` | `datetime` | `_utcnow` | |

#### `BlackoutSettings`(`blackoutsettings`)— 投稿禁止時間帯【単一行 id=1 運用】

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `enabled` | `bool` | `True` | 制限帯を有効にするか |
| `weekdays_json` | `str` | `"[0, 1, 2, 3, 4]"`(月〜金) | 制限曜日(月=0..日=6)の JSON 配列 |
| `windows_json` | `str` | `'[["09:00", "12:00"], ["13:00", "19:00"]]'` | `[["HH:MM","HH:MM"], ...]` の制限時間帯(JST) |
| `updated_at` | `datetime` | `_utcnow` | |

曜日番号は月=0..日=6(Python の `datetime.weekday()` 互換)。土日は既定で対象外。

#### `ApiCostLog`(`apicostlog`)— X API / Claude API 従量課金の記録

| フィールド | 型 | 既定 | 意味 |
|---|---|---|---|
| `kind` | `CostKind` | (既定なし=必須) | コスト種別 |
| `units` | `int` | `1` | 件数(X API)またはトークン総数(LLM) |
| `cost_usd` | `float` | `0.0` | 実額(LLM はここに実額を入れる) |
| `note` | `str?` | `None` | メモ |
| `ts` | `datetime` | `_utcnow` | 記録時刻 |

### 単一行(シングルトン)テーブルの運用

`StyleProfile`(name=default)/`MonitorSettings`(id=1)/`BlackoutSettings`(id=1)/`NewsSettings` はいずれも「単一行運用」だが、DB 制約ではなく利用側コードが保証する。`get_monitor_settings` / `get_blackout_settings` / `get_news_settings` は先頭行を返し、無ければ既定値で作成・commit する(`StyleProfile` は `set_style_guide` が name=default で upsert)。

### マイグレーション(`db._migrate` / `_ADDED_COLUMNS`)

`init_db()` は ① `get_engine()` → ② `SQLModel.metadata.create_all`(未作成テーブルを作る。**ALTER はしない**)→ ③ `_migrate()`(冪等列追加)→ ④ `seed_builtin_templates`(型の冪等投入)の順で実行する。

`create_all` が既存テーブルに列追加しない欠点を補うのが `_migrate()`。`_ADDED_COLUMNS` は `{テーブル名: [(列名, SQLite型, デフォルト句), ...]}` で、`PRAGMA table_info` で既存列を調べ、無い列だけ `ALTER TABLE ... ADD COLUMN` する(前方追加専用。型変更・列削除・データ移行はしない)。

`_ADDED_COLUMNS` の全列: `pastpost`(`author_user_id`/`author_handle`/`is_own DEFAULT 1`)、`draft`(`blackout_override DEFAULT 0`/`target_text DEFAULT ''`/`target_created_at`/`schedule_missed DEFAULT 0`)、`monitorsettings`(`max_drafts_per_run DEFAULT 10`/`auto_monitor_enabled DEFAULT 1`/`auto_post_enabled DEFAULT 1`/`min_impressions DEFAULT 10000`/`celeb_watch_enabled DEFAULT 0`/`celeb_list_id`)、`engagetarget`(`list_id`)。

ハマりどころ: **モデルに新フィールドを足したら、既存DBへ反映するには `_ADDED_COLUMNS` への追記も必要**(`create_all` だけでは既存テーブルに列が増えない)。

### シード(`templates.seed_builtin_templates`)

`_BUILTIN` の4型を投入: `("バズの型 (buzz-playbook)", POST, POST_PLAYBOOK)`、`("絡みリプの型 (R1〜R6)", REPLY, REPLY_PLAYBOOK)`、`("引用RTの型", QUOTE, QUOTE_PLAYBOOK)`、`("ニュース速報の型 (N1〜N5)", NEWS, NEWS_PLAYBOOK)`。本文は `xagent/prompts.py` の定数。**冪等性は `name` の完全一致で判定**(同名が既存ならスキップ)、`builtin=True` で作成。投入後、各 kind に active が無ければ `created_at` 昇順先頭を `set_active`。戻り値は新規投入件数。

エッジケース: **ユーザーがビルトインの型を改名すると、再 `init_db` で同名扱いされず重複投入される**(`builtin` フラグはスキップ判定に使われない)。

---

## 5. 下書きライフサイクル(状態機械)

### 状態遷移表(`guards.ALLOWED_TRANSITIONS`)

遷移の唯一の真実。`can_transition(old, new)` は `new in ALLOWED_TRANSITIONS.get(old, set())` を返す。

| 遷移元 | 許可される遷移先 |
|---|---|
| `DRAFT` | `APPROVED`, `REJECTED`, `CANCELED` |
| `APPROVED` | `QUEUED`, `REJECTED`, `CANCELED` |
| `QUEUED` | `POSTED`, `QUEUED`(再予約=予約時刻変更の自己ループ), `APPROVED`, `REJECTED`, `CANCELED` |
| `POSTED` | (空集合。投稿済みからはどこへも遷移不可) |
| `REJECTED` | `CANCELED` |
| `CANCELED` | `DRAFT`(ゴミ箱からの復元のみ) |

不変条件:
- `CANCELED`(取消/ゴミ箱)は未投稿のどの状態からでも入れる。**`POSTED` からは取消不可**(履歴改ざん防止)。
- `QUEUED → QUEUED` の自己ループが許可され、予約時刻の変更(再予約)を通せる。
- `CANCELED` から戻れるのは `DRAFT` のみ。
- `REJECTED` からは `CANCELED` にしか行けない(却下→直接 DRAFT 不可、`CANCELED` 経由で復元する)。

### 遷移の門 `_set_status`

`approve_draft` / `reject_draft` / `restore_draft` / `queue_draft` / `cancel_draft` は全て `_set_status` を経由する。`can_transition` で弾かれると `PolicyViolation("不正な状態遷移: ...")`。例外として **`post_draft` 内の `POSTED` への遷移と `reconcile_missed_schedules` の `QUEUED → APPROVED` は `_set_status` を通さず直接代入する**(整合性は `ensure_post_authorized` が事前に状態を絞ることで担保)。

### 各遷移を起こす操作

- `approve_draft`: `DRAFT/QUEUED → APPROVED`
- `reject_draft`: `→ REJECTED`
- `cancel_draft`: `QUEUED` の時は先に `scheduled_at=None`(自動投稿対象から外す)、その後 `→ CANCELED`。行はDBに残る
- `restore_draft`: `CANCELED → DRAFT`
- `queue_draft`: `scheduled_at` を `to_naive_utc` 正規化、`blackout_override` 指定時は列保存、**常に `schedule_missed=False` にリセット**してから `→ QUEUED`
- `reconcile_missed_schedules`: 失効した予約を `QUEUED → APPROVED` に戻す(後述 §10)

### `post_draft` のガード順序(最重要・厳格)

`post_draft(session, x_client, draft, settings=None, now=None, trigger=PostTrigger.MANUAL, override=False) -> list[str]` が実際に X へ投稿する唯一の経路。ガードは以下の順序で必ず通す(順序が安全性の要)。

| # | ガード | 内容 | 失敗時 |
|---|---|---|---|
| 1 | 緊急停止 | `settings.posting_enabled` が False なら即拒否(最優先) | `PolicyViolation` |
| 2 | 認証/予約 | `ensure_post_authorized(status, scheduled_at, trigger, now)` | `PolicyViolation` |
| 3 | ブラックアウト | `is_blackout(...)` → `ensure_not_blackout(in_blackout, override or draft.blackout_override, reason)` | `BlackoutViolation` |
| 4 | 頻度 | `_rate_limiter(settings).check(recent_posted_times(session), now)` | `PolicyViolation` |

`ensure_post_authorized` の trigger 別:
- `MANUAL`(人の明示操作=認証): `status ∈ {APPROVED, QUEUED}` のみ(`ensure_postable`)。
- `SCHEDULED`(スケジューラ発火=予約): `status==QUEUED` かつ `scheduled_at != None` かつ `scheduled_at <= now` をすべて満たす場合のみ(誤爆防止)。

### 投稿実行(ガード通過後、種別ごとの分岐)

| `kind` | 動作 | コスト記録 | `posted_tweet_id` |
|---|---|---|---|
| `REPOST` | `target_tweet_id` 必須。`x_client.retweet(...)`。本文チェックの前に分岐・return | `WRITE` units=1 | `target_tweet_id`(RTには新規IDが無いため対象IDを記録) |
| `REPLY` | **送信不可**: 頻度ガード直後に `PolicyViolation` で弾く。X API の制限(自分が言及/絡みを受けていない他人の会話への返信は403)で公式API送信できないため。下書き生成までにとどめ、送信は手動UIで行う(Inboxの「Xで返信（手動）」)。自分宛メンション返信は手動で送信。詳細は memory `xagent-api-reply-restriction` | — | — |
| `QUOTE` | `_post_quote(...)`: まず `x_client.post(seg[0], quote_tweet_id=...)`。X が「引用不可」エラー(`quoting this post is not allowed`)を返したら本文末尾に対象URL(`_quote_tweet_url`)を付けて通常投稿に**フォールバック**。それ以外のエラーは握り潰さず再送出 | 同上 | `ids[0]` |
| `POST` | `x_client.post_thread(segments, media_ids_first=...)`(スレッド対応) | 同上 | `ids[0]` |

- `REPOST` 以外で `segments` が空なら `PolicyViolation("本文が空の...")`。
- メディア: `media_paths` 非空なら `media_ids = [upload_media(p) ...]`。`REPLY`/`QUOTE` は単発投稿に、`POST` は先頭ツイートにのみ(`media_ids_first`)付与。`REPOST` はメディア処理なし。
- 成功後はいずれも `posted_at=now`、`status=POSTED`(直接代入)、`updated_at=now`、戻り値は tweet id のリスト。
- **引用URLフォールバック**(`service._post_quote`): X API v2 の `quote_tweet_id` は公開・引用可(reply_settings=everyone)なツイートでも 403「Quoting this post is not allowed...」を返すことがある(手動UIでは引用できる挙動差)。この特定エラー時のみ、引用を諦めず本文に `https://x.com/<handle>/status/<id>` を埋め込んだ通常投稿として送る。`target_tweet_id` が無い／別種の拒否なら従来どおり例外を上げる。

### `RateLimiter`(頻度ガード、純粋関数)

DB に依存させず純粋関数的に判定。`RateLimitConfig`(`max_per_day=10` / `hard_cap_per_day=100` / `min_interval_seconds=300`)を `settings` から構築。`check(recent_post_times, now)` の判定順:
1. 直近24時間(`> now - 1day`、厳密に大なり)の件数が `hard_cap_per_day` 以上 → 不許可
2. 同件数が `max_per_day` 以上 → 不許可
3. 最終投稿からの経過が `min_interval_seconds` 未満 → 不許可(`retry_after_seconds` を返す)。なお最小間隔判定は全件 `recent_post_times` の `max` を基準にする

---

## 6. 機能カタログ

各機能の実体・挙動・委任系("AIに任せる")を細粒度で列挙する。

### コンテンツ生成

- **整形(format_post)**: 雑メモを「本人のノウハウ口調の X 投稿」へ変換。`allow_long=False`(既定)は140字厳守でスレッド化しない。`allow_long=True` は長文フック可。型(playbook)・口調ガイド・真似る相手の特徴をシステムプロンプトの追記ブロックとして注入。出力は本文のみ(前置き・引用符・コードブロック禁止)。
- **案を複数(format_variations)**: 1メモから言い回し違いを n 案(`n=max(1,min(n,5))` で1〜5にクランプ)。`---` だけの行で区切らせ、各片を整形。`raw=True` では言い回し違いを作れないため1件のみ。
- **真似る(emulate)**: 学習済み `AccountProfile` の `profile_text` と代表投稿を整形プロンプトに乗せる。**選択時のみ**有効で、未指定なら学習データは自動注入されない。常時適用は手入力のスタイルガイドのみ。
- **型ライブラリ(PromptTemplate)**: POST/REPLY/QUOTE/NEWS の4カテゴリ。`POST_PLAYBOOK` の型 A〜P(J/O は欠番)、`REPLY_PLAYBOOK` の R1〜R6、`QUOTE_PLAYBOOK`、`NEWS_PLAYBOOK` の N1〜N5(大手ニュース系/個人解説系の実投稿から抽出した速報の型＋Xアルゴリズム対応の注意書き)。各 kind で active は最大1件(monitor / news の自動生成が既定として使う)。
- **AI 自動選択(auto_template / choose_template)**: 「AIに任せる」。候補が0件→None、1件→そのID(LLM 呼ばず)、2件以上のときだけ LLM に id を選ばせる。候補集合に含まれる id のときだけ採用、解析失敗は None。
- **指令解析(commands.parse_command)**: 自由文(例「このURLをリポストして、以下の文で投稿: …」)を `{action, target_url/tweet_id/handle, body, raw, note}` に構造化。ツイート URL 抽出は Python の正規表現で確定(LLM 誤りに依存しない)。「リポスト」は引用RT(quote)として扱う。**URL が無ければ quote→post に降格**、URL あり×quote では body から対象 URL を除去。

### 文体・学習

- **文体学習(style)**: 常時適用の口調ガイド(`StyleProfile.guide_text`)。`learn_past_posts` で自分の過去投稿を `PastPost` に保存し参考に。`example_texts` は like 降順の自分投稿(表示・参考用、整形へは自動注入しない)。
- **プロフィール学習(profiles.learn_account)**: 任意ユーザー(自分/他人)の投稿を取得(上限 `PROFILE_MAX_POSTS=200`)→投稿時間帯ヒストグラム算出(JST、LLM 不使用)→AI 抽出(先頭 `_SAMPLE_FOR_LLM=60` 件)→`AccountProfile` を handle キーで upsert。JSON 化失敗時は `("{}", raw)` で全文を `profile_text` に温存。

### テキスト計量(text.py、LLM 不使用)

- 2系統の計量がある。**加重(weighted)**: Latin=1・CJK/絵文字=2(`weighted_length`)。**字数(コードポイント数)**: `char_length = len(text)`。
- 定数: `POST_LIMIT_WEIGHTED=280` / `FOLD_THRESHOLD_WEIGHTED=280` / `POST_LIMIT_CHARS=140` / `FOLD_THRESHOLD_CHARS=140`。
- `exceeds_fold(text)`: **字数(140)基準**で折りたたみ判定(`weighted_length` ではない点に注意)。
- `split_into_thread(text, limit=POST_LIMIT_CHARS, add_numbering=False)`: 字数基準で分割。短文は単一セグメント、超過時は文単位(句点等の lookbehind)で貪欲連結、1文が limit 超なら固定長で強制分割。連結すると原文に一致(情報欠落なし)。`add_numbering=True` かつ2セグメント以上で末尾に ` (i/n)`(番号分の字数は別途要考慮)。

### 予約・キュー・投稿

- **最適時間予約(schedule_optimal)**: 固定の高エンゲージ時間帯(JST、`DEFAULT_SLOTS_HOUR=(8,12,16,19,21,22)`)から、`now` 以降・既予約と90分以上離れた直近スロットを `next_optimal_slot` で選びキュー投入。
- **時間指定予約**: `queue_draft(scheduled_at=...)`。`to_naive_utc` で正規化。
- **ブラックアウト override 予約**: `queue_draft(blackout_override=True)` で制限帯でも発火を許可する印を列に焼き付ける。
- **予約失効(reconcile_missed_schedules)**: `MISSED_SCHEDULE_GRACE=30分` を超えて未投稿の予約を `QUEUED → APPROVED` に戻し `scheduled_at=None`、`schedule_missed=True`。遅れた投稿は X で逆効果のため遅延投稿せず再予約を促す。猶予内(30分未満)の遅延は通常投稿。
- **キュー処理(process_due_queue)**: 先に `reconcile_missed_schedules` → `due_drafts`(QUEUED かつ scheduled_at != None かつ scheduled_at <= now)を順に `post_draft(trigger=SCHEDULED, override=draft.blackout_override)`。戻り `{posted, skipped, errors, missed}`。`PolicyViolation`/`BlackoutViolation` は `skipped`(据え置き)、その他例外は `errors`。
- **即時投稿(post_draft, MANUAL)**: 承認済み/キュー済みを人の操作で投稿。
- **通常リポスト(create_repost_draft / retweet)**: コメント無しの自分の過去投稿の再拡散。LLM 整形なし。引用RT(コメント付き)は quote 側。

### 監視・絡み(§10 に動作詳細)

- **監視ソース**: メンション(reply案、**既定オフ=手動返信方針**)/手動MANUAL対象/Xリスト/ジャンルkeyword(既定オフ)/フォロー中(既定オフ)。絡み対象は**本人オリジナル投稿のみ**(RT・引用RT・**24時間超**は除外)。さらに**最低インプレッション(`MonitorSettings.min_impressions`、既定10000)未満の投稿は候補から除外**(twitterapi.io の `viewCount` を `view_count` として参照。取れない投稿=None は判定不能として通す。公式APIフォールバック経路は view が無いため実質このフィルタを通らない)。
- **有名人ウォッチ(`run_celeb_once` / celeb_tick)**: 専用Xリスト(`celeb_list_id`)の有名人が **AIに言及した投稿だけ**を即検出して reply 案を作る軽量ストリーム。タイムライン巡回はせず、リストメンバーを `from:a OR from:b ...`(`CELEB_SEARCH_CHUNK=12` 人ずつに分割)× AIキーワードOR(`AI_TOPIC_KEYWORDS`)× `within_time:24h` の**検索1〜2クエリ/回**で検出(API節約)。ヒット0ならLLMを呼ばない。`MonitorCursor(stream="celeb")` を raw 全ヒットの最大IDまで前進させ、選定落ちした投稿も二度LLMにかけない。Draft既存除外・RT除外・24h窓は通常監視と同じだが、**min_impressions は適用しない**(投稿直後の素早い反応を優先=閾値免除)。候補には `note`(即時反応の狙い)を付けて `select_engagements` に渡し、1回の生成上限は `CELEB_MAX_PER_TICK=3`。自動実行は `celeb_watch_enabled`(既定OFF)でゲート、手動は Settings「今すぐチェック」/ `POST /monitor/celeb-run-once`(ジョブ化)。
- **AIバッチ選定(`collect_engage_candidates` → `formatter.select_engagements`)**: 全有効ソースの直近24時間の候補を**1回のバッチAI判断**にまとめて渡し、「どの投稿に絡むか(分散)」「reply / quote のどちらか」「本文」までを一度に生成する(従来の直列スキャン＋投稿ごと判断は先頭アカウントがバジェットを使い切り同一アカウント偏りを生んだため廃止)。**同一アカウントは原則1件・最大2件**(プロンプト指示＋コード側でも3件目以降を破棄)。選定結果は `service.create_engage_draft_from_text` で LLM を呼ばずそのまま Draft 化(選定理由を `source_text` に `[AI選定理由]` として保持)。`run_once` の総生成数バジェット(`max_drafts_per_run`)が選定の `max_n` になる。メンション(自分宛)は従来通り個別生成・引用RTせず常に reply。
- **絡み対象(EngageTarget)**: MANUAL(user_id 直接)/LIST(list_id を毎回メンバー展開)/GENRE(keyword 検索)/FOLLOWING(Enum はあるが poll は EngageTarget を読まず自分のフォローを直接巡回)。
- **返信/引用生成(generate_reply / generate_quote)**: 相手投稿から AI が本文生成。返信は `_REPLY_LENGTH_BANDS` から `random.choice` で長さ帯を選び、毎回同じ長さに寄るのを防ぐ。常に1セグメント(分割しない)、140字厳守。
- **型の事後切替(`service.recast_engage_draft` / `POST /drafts/{id}/recast`)**: AI が選んだ型を人が上書きする導線。未承認(DRAFT)の REPLY⇄QUOTE 間でのみ、新しい型で本文を再生成し `kind` を差し替える(元ポスト情報は維持)。Inbox の「引用RTで送る」「手動で返信に切替」ボタンから呼ぶ。DRAFT 以外/POST 種別は `PolicyViolation`(API は 409)。

### ニュース速報(news.py)

- **ソース**: 別プロジェクト XNewsBot が朝夕に収集するニュースDB(`xnewsbot.db`)を `sqlite3` の `file:...?mode=ro` URI で**読み取り専用**接続(`xnewsbot_db_path`、`.env` の `XNEWSBOT_DB_PATH` で変更可)。XNewsBot 側のコード・DBは一切変更しない。DB不在は `NewsSourceUnavailable`(API は 500 にせず `available=false` で案内)。
- **新着判定(`fetch_new_items`)**: `NewsSettings.last_digest_id` カーソルより新しい `genredigest` のうち対象ジャンル(`genres_json`、既定 AI/テクノロジー)の `newsitem` を返す。**初回(カーソル0)は全履歴でなく最新の収集日だけ**に絞る(過去分の大量生成防止)。処理後カーソルは対象外ジャンル含む全体 `MAX(id)` まで進める。
- **生成(`run_news_once` → `formatter.generate_news_posts`)**: 新着記事一覧を**1回のバッチAI判断**(JSONスキーマ強制、NEWS型 playbook 注入)に渡し、「どの記事を選ぶか(最大 `max_posts_per_run`)」「通常投稿(post)か一次情報の引用RT(quote)か」「本文」「選定理由」までまとめて決める。下書きを作るだけで投稿しない(人間承認必須)。
- **Draft化(`_create_news_draft`)**: post は `source_url` があれば**2ツイート目(リプ欄)**に置く(外部リンクのリーチ減点対策)。quote は `source_tweets[0].url` から tweet_id を抽出して `kind=QUOTE`(元ポストの画像・動画が引用で見える)。URL が取れなければ post へフォールバック。選定理由は `source_text` に `[AI選定理由]` として保持。
- **自動実行(news_tick)**: `auto_news_enabled`(既定OFF)でゲート。新ダイジェスト検知で自動生成(ダイジェスト連動)。手動は News 画面「今すぐ生成」/ `POST /news/run-once`(ジョブ化)。

### X ネイティブ「リスト」

- **リスト作成(lists.create_list_from_handles)**: ハンドル一覧を正規化(@除去・大小無視重複排除・順序保持)→各 handle を解決→`create_list`→一括 `add_list_member`。1件の失敗で全体を止めず `skipped:[{handle, reason}]` に理由付き計上して継続。書き込みは公式 X API のみ。

### 分析・メディア・その他

- **分析(コスト)**: `ApiCostLog` を X API(read/write/tl)と Claude(llm)に分けて集計、合計も返す(`GET /analytics/cost`)。単価: READ $0.005/件、WRITE $0.01/件、TL $0.01/件、LLM はトークン換算(`LLM_PRICE_PER_MTOK={"input":5.0,"output":25.0}` USD/Mtok、claude-opus-4-8 価格)。実課金はサブスクリプション(Claude Code CLI)だが消費の可視化のため名目額を記録し続ける。
- **分析(サマリ)**: 全 `DraftStatus` のステータス別下書き件数(`GET /analytics/summary`)。
- **メディア(media)**: 画像(jpg/jpeg/png/webp/gif)最大4枚・動画(mp4/mov/m4v)1個・25MB上限。混在不可。保存は `<uuid4hex><ext>`、DB には相対パス。実際の X アップロードは投稿直前(`media_id` 失効回避)。
- **おすすめ時間(recommended_times)**: UI 初期値用に重ならない直近 count 件のスロットを返す(`GET /schedule/recommended`)。tier は best(21時)/great(19・22時)/good(8・12・16時)。
- **デーモントグル(auto_monitor_enabled・既定OFF)**: 絡み案の自動生成のみを UI(Settings)でオンオフ。OFF(既定)でもプロセスは止めず `monitor_tick` が即returnするだけ(API消費なし)。乱造防止のため既定OFF＝必要な時だけオンにし、終わったらオフに戻す運用。手動で1回だけ生成するなら Inbox「監視を1回実行」/ CLI `xagent monitor-once`。予約投稿の発火はこのトグルと無関係に常時動く。ニュース速報の自動生成も同じ思想で `NewsSettings.auto_news_enabled`(既定OFF・News 画面のトグル)、有名人ウォッチも `MonitorSettings.celeb_watch_enabled`(既定OFF・Settings のトグル)。
- **DB容量管理(maintenance.enforce_db_capacity)**: `max_db_bytes`(既定2GB)超過時に端末状態(POSTED/REJECTED/CANCELED)のみ `created_at` 昇順で `_PURGE_BATCH=200` 件ずつ削除し VACUUM。生きた下書きは削除しない。
- **コスト記録(cost)**: `commit` は常に呼び出し側に委ねる(下書き作成や学習と同一トランザクションで永続化)。`bill_formatter_usage` は整形器のトークン使用量をフラッシュしカウンタをリセット(二重課金防止)。両方0なら None。
- **通知(notify)**: 承認待ちが出ると macOS 通知(`osascript`)。macOS 以外・通知不可環境では黙って no-op(例外を投げない)。

---

## 7. APIエンドポイント一覧

全ルータは `dependencies=[Depends(require_api_token)]` を APIRouter レベルで付与。`config.api_token` 設定時のみ `X-API-Token` ヘッダ必須(未設定なら認証なしで開放=既定)。`/me`・`/status` もリモートアクセス対応で同じ保護下(アカウント名・運用状態を晒さない。`/status` はフロントのログイン時トークン検証も兼ねる)。常に開放なのは `/health`(launchd 死活確認・ログイン前の接続確認)と `/media/files` 静的配信(`<img>/<video>` タグはカスタムヘッダを送れないため。tailnet 内限定の露出として許容)のみ。

- `GET /health` — `{status, version}`。
- `GET /me` — 現在の投稿先アカウント(`get_me`、失敗時 `{id:null, username:null}`)。
- `GET /status` — **予約スケジューラの稼働状況**。`{scheduler_enabled, scheduler_running, healthy, last_queue_tick_at, seconds_since_queue_tick, queue_interval_seconds, next_queue_run_at, posting_enabled, auto_monitor_enabled}`。`healthy` はハートビート(直近の `queue_tick` 発火時刻、`daemon._last_queue_tick_at`)が間隔の3倍以内＝スレッド生存かで判定。起動直後で未発火なら猶予で稼働扱い(lifespan で queue ジョブを `next_run_time=now` にして即発火させハートビートを立てる)。フロントは App のサイドバーで「予約スケジューラ 稼働中/停止?」バッジに使う(`api.status()` を10秒間隔ポーリング)。

### 共通基盤(deps.py)

| 依存 | 供給物 | 失敗時 |
|---|---|---|
| `require_api_token` | 認証ガード | トークン設定時の不一致で **401** |
| `db_session` | `Session` | — |
| `get_formatter` | `Formatter` | — |
| `get_x_client` | `XClient` | `XClientError` で **503**(投稿・refresh・対象解決) |
| `get_x_client_optional` | `XClient \| None` | 失敗時 None を返す(compose /command の本文取得) |

### compose(`/compose`)

| メソッド | パス | 用途 | LLM | エラー |
|---|---|---|---|---|
| POST | `/compose/preview` | 140字基準の折りたたみ/分割プレビュー | 不使用 | — |
| POST | `/compose` | 整形して未承認下書き作成 | 使用 | 400(PolicyViolation) |
| POST | `/compose/variations` | 言い回し違いN案。`raw=true` なら1件のみ | 使用 | 400 |
| POST | `/compose/interpret` | 自由文の指令解析(下書きは作らないがコスト計上) | 使用 | — |
| POST | `/compose/command` | 確認済み指令から下書き作成(quote/reply/post)。quote/reply は `target_tweet_id` 必須 | 使用 | 400 |
| POST | `/compose/quote-from-url` | ツイートURLから引用案(引用RT)をAI生成(`create_quote_draft`)。Inboxの手動ボタン。**ジョブ化: `{"job_id"}` を即返し結果は `GET /jobs/{id}`**(URL不正のみ同期400) | 使用 | 400 |

### jobs(`/jobs`) — 長時間AI生成の非同期実行(2026-06-11〜)

生成系(quote-from-url / monitor run-once / recast)は Claude CLI 待ちで数十秒〜15分かかり、同期のままだと**ブラウザ fetch の上限(Chrome 約300秒)・CLIタイムアウト(240秒)・worker 再起動(--reload/kickstart)**のどれかに当たって「TypeError: Failed to fetch」になっていた。これらは `{"job_id"}` を即返し、フロント(`api.ts` の `runJob`)が `GET /jobs/{job_id}` を2秒間隔でポーリングする。

| メソッド | パス | 用途 | エラー |
|---|---|---|---|
| GET | `/jobs` | **実行中**ジョブの一覧 `[{job_id, label, progress, elapsed_seconds}]`。label はジョブ種別(`monitor-run-once`/`quote-from-url`/`recast`/`news-run-once`/`celeb-run-once`)。画面を開き直した時の再接続用 | - |
| GET | `/jobs/{job_id}` | ジョブ状態 `{job_id, status: running\|done\|error, progress, elapsed_seconds, result, error}` | 404(再起動でジョブ消滅) |

ジョブは**プロセス内メモリ保持**(`xagent/api/jobs.py`、完了10分後に掃除)。worker 再起動で消えるが、結果の実体(下書き)は DB に入るので永続化しない。フロントは 404 を「サーバが再起動したため生成が中断されました」と表示し、一時的な接続断はポーリング継続で吸収する。あわせて未捕捉例外は `main.py` のミドルウェアが **CORS ヘッダ付き JSON 500** に変換する(Starlette 既定のプレーン500は CORSMiddleware を通らず、vite dev :5180 のクロスオリジンでは一律 Failed to fetch に化けるため)。

**進捗表示(2026-06-12〜)**: `start_job(fn)` の `fn` は `set_progress("メッセージ")` を受け取り、節目で「何をしているか」を更新する(無表示の長い待ちはユーザーにはハング/エラーと区別がつかないため)。`GET /jobs/{id}` は `progress`(文字列) と `elapsed_seconds`(サーバ計測の経過秒) を返し、フロント(`runJob` の `onProgress`)が「候補収集中: リスト「絡み」 12/37人 (@handle)（経過 3分20秒）」のように表示する。run-once は収集アカウントごと・AIバッチ選定開始・下書き作成1件ごとに更新し、Inbox は「下書き作成中」進捗を受けたら一覧を即リロードして生成済みの案から順次表示する。ジョブ失敗は `status=error` に加えて `logger.exception` でサーバログ(`~/Library/Logs/xagent.err.log`)にも残す(画面遷移でポーリングが切れると失敗がどこにも記録されないため)。run-once の result には `candidates`(選定に渡した候補数) が含まれ、0 のとき UI は「新しい絡み候補なし」と区別して表示する。

**実行中ジョブへの再接続**: Inbox はマウント時に `GET /jobs` で `label=="monitor-run-once"` の実行中ジョブを探し、あれば `pollJob` で進捗表示と結果反映を再開する(`trackMonitorJob`)。リロード・画面遷移・別端末/CLI からの開始でも、Inbox を開けば進行中の生成が見える。あわせて現在の画面はURLハッシュ(`#inbox` 等)に保持し、リロードや新ビルド検知の自動リロードで Compose に戻らない(`App.tsx` の `viewFromHash`)。

### drafts(`/drafts`)

| メソッド | パス | 用途 | エラー |
|---|---|---|---|
| GET | `/drafts` | 一覧(`status`/`kind` で絞込) | — |
| POST | `/drafts/reconcile-schedules` | 失効予約を承認済みへ戻す。`{"missed":[id...]}` | — |
| GET | `/drafts/{id}` | 単一取得 | 404 |
| PATCH | `/drafts/{id}` | segments/scheduled_at の独立部分更新 | 404 |
| POST | `/drafts/{id}/approve` | 承認 | 404/409 |
| POST | `/drafts/{id}/reject` | 却下 | 404/409 |
| POST | `/drafts/{id}/cancel` | 取消(予約は自動投稿もキャンセル、投稿済みは不可) | 404/409 |
| POST | `/drafts/{id}/restore` | ゴミ箱から復元 | 404/409 |
| POST | `/drafts/{id}/recast` | 絡みの型切替: リプライ⇄引用RTで本文再生成し `kind` 差替(`{"to":"reply"\|"quote"}`)。未承認のREPLY/QUOTEのみ。**ジョブ化: `{"job_id"}` を即返す**(404のみ同期。PolicyViolation はジョブの error として返る) | 404 |
| POST | `/drafts/{id}/queue` | キューへ(最適/指定時刻) | 404/409 |
| POST | `/drafts/{id}/post` | 即時投稿 | 404/409/423/502 |

`post` の例外マッピング(順序重要): `BlackoutViolation`→**423 Locked**、`PolicyViolation`→**409**、`XClientError`→**502**(X側拒否、下書きは未投稿で残す)。

### posts(`/posts`)

| メソッド | パス | 用途 | エラー |
|---|---|---|---|
| GET | `/posts/recent?days=` | DBキャッシュ済み自分投稿(既定7日、1..30) | — |
| POST | `/posts/refresh?days=&max_total=` | X から取得し `PastPost(is_own)` に upsert | 503 |
| POST | `/posts/{tweet_id}/repost` | 自分の投稿を通常リポスト(now/time)。**自動承認** | 423/409(XClientError 未捕捉→500) |

注意: posts repost(`repost`)は `create_repost_draft`→`approve_draft`→(time なら `queue_draft`、now なら `post_draft`)を一気通貫で実行し、捕捉するのは `BlackoutViolation`(→423)と `PolicyViolation`(→409。QUEUE 失敗時の time モードのポリシー違反も同経路で 409)のみ。`XClientError` の明示捕捉が無いため、即時投稿時の X 側拒否は drafts post の 502 と非対称に **500** になる。

### targets(`/targets`)

| メソッド | パス | 用途 | エラー |
|---|---|---|---|
| GET | `/targets` | 全 `EngageTarget` 一覧 | — |
| POST | `/targets` | 対象追加(kind=LIST は `list_id` 必須、user_id 解決は best-effort) | 400 |
| DELETE | `/targets/{id}` | 削除 | 404 |

### monitor(`/monitor`)

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/monitor/run-once` | 監視を1サイクル実行。`?limit=N` でその回だけ生成数を N 件までに絞る(乱造防止)。**ジョブ化: `{"job_id"}` を即返し `run_once` の戻りはジョブの result**(候補収集+バッチ選定で実測数分〜15分) |
| POST | `/monitor/celeb-run-once` | 有名人ウォッチを1回実行(`run_celeb_once`)。`?limit=N` でその回の生成上限を絞る。**ジョブ化**(label=`celeb-run-once`)。result は `{candidates, reply_suggestions, quote_suggestions}`、リスト未設定なら result 内 `error` で案内 |
| GET | `/monitor/settings` | 監視設定取得 |
| PUT | `/monitor/settings` | 監視設定更新(None 以外のフラグのみ部分更新。`min_impressions`/`celeb_watch_enabled`/`celeb_list_id` 含む) |

### news(`/news`)

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/news/run-once` | ニュース速報の生成を1回実行。`?limit=N` でその回だけ生成数を絞る。**ジョブ化: `{"job_id"}` を即返し**(label=`news-run-once`)、結果 `{news_items, created, draft_ids}` はジョブの result。新着が無ければ何も作らない |
| GET | `/news/settings` | ニュース速報設定取得(無ければ既定で作成) |
| PUT | `/news/settings` | 設定更新(`auto_news_enabled`/`genres_json`/`max_posts_per_run`、None 以外のみ部分更新) |
| GET | `/news/recent` | 直近2日の対象ジャンルのニュース一覧(UI表示用)。XNewsBot の DB が読めなくても 500 にせず `{available: false, error}` を返す |

### schedule(`/schedule`)

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/schedule/recommended` | おすすめ投稿時間(`slots`/`note`/`sources`/`next_slots`) |
| GET | `/schedule/blackout` | 制限帯設定取得 |
| PUT | `/schedule/blackout` | 制限帯設定更新(部分更新) |
| GET | `/schedule/blackout/status?at=` | 指定時刻(未指定=現在)の制限帯判定 |

### lists(`/lists`)

| メソッド | パス | 用途 | エラー |
|---|---|---|---|
| GET | `/lists?max_total=` | 所有リスト一覧(`Query(100, ge=1, le=1000)`) | 503 |
| GET | `/lists/{id}/members?max_total=` | メンバーをプロフィール付きで(`Query(100, ge=1, le=1000)`) | 503 |
| POST | `/lists` | ハンドル一覧から作成+一括追加 | 503 |
| PATCH | `/lists/{id}` | 名前/説明/公開設定更新(204) | 503 |
| DELETE | `/lists/{id}` | 削除(204) | 503 |
| POST | `/lists/{id}/members` | メンバー追加(handle or user_id、両欠落で 422、解決不可で 404) | 422/404/503 |
| DELETE | `/lists/{id}/members/{user_id}` | メンバー削除(204) | 503 |

lists ルータの GET/POST/PATCH/DELETE/members 系はすべて `except XClientError` を捕捉して **503** を返す。members 追加は handle→user_id 解決失敗時も **503**(`get_user_by_username` 失敗)を返す。

### media / analytics / profiles / style / templates

| メソッド | パス | 用途 | エラー |
|---|---|---|---|
| POST | `/media/upload` | multipart アップロード→相対パス返却 | 400(不許可拡張子)/413(25MB超) |
| GET | `/analytics/cost` | コスト集計(x_api/claude_api/total_usd/by_kind) | — |
| GET | `/analytics/summary` | ステータス別下書き件数 | — |
| GET | `/profiles` | 学習済みプロファイル一覧(handle昇順) | — |
| POST | `/profiles/learn` | アカウント学習(READ+LLM コスト計上) | 404(ユーザー未存在) |
| GET | `/style` | `{guide_text, examples}` | — |
| PUT | `/style` | スタイルガイド更新(常に active=True) | — |
| POST | `/style/learn` | 自分の過去投稿学習(READ コスト) | 503(get_x_client 依存のみ。実行時 XClientError は未捕捉→500、§12) |
| GET | `/templates?kind=` | 型一覧(kind フィルタ) | — |
| POST | `/templates` | 型作成(active=true で既定化) | — |
| PATCH | `/templates/{id}` | 更新(`active=true` のときだけ既定化、`false` では解除しない) | 404 |
| POST | `/templates/{id}/activate` | 既定化 | 404 |
| DELETE | `/templates/{id}` | 削除(204) | 404 |

メディア配信は本ルータではなく `main.py` の `app.mount("/media/files", StaticFiles(directory=media_dir()))`。

### HTTPエラーコードの意味(横断)

| コード | 意味 | 主な発生箇所 |
|---|---|---|
| 400 | 入力不正/ポリシー違反 | compose 各(`PolicyViolation`)、targets POST(list_id欠如)、media(不許可拡張子) |
| 401 | `X-API-Token` 不正 | `require_api_token`(トークン設定時のみ) |
| 404 | 下書き/対象/型が見つからない | drafts/targets/profiles/templates |
| 409 | 承認/頻度等のポリシー違反 | drafts approve/.../post、posts repost |
| 413 | ファイルが大きすぎる(25MB超) | media upload |
| 422 | 必須パラメータ欠落 | lists add_member(handle/user_id 両欠落) |
| 423 | 制限時間帯ブロック(`BlackoutViolation`) | drafts post、posts repost |
| 502 | X側拒否(`XClientError`、引用不可/権限不足/レート) | drafts post のみ |
| 503 | Xクライアント生成失敗/接続失敗 | `get_x_client` 依存全般、posts refresh(実行時 XClientError も捕捉)、lists 全エンドポイント(実行時 XClientError 捕捉)。style/learn は `get_x_client` 生成失敗のみ(実行時 XClientError は未捕捉→500) |

注意: 同一例外でもコード差がある。`PolicyViolation` は compose では **400**、drafts/posts では **409**。`XClientError` は drafts post で **502**、それ以外で **503**。

### 部分更新パターン

drafts PATCH(segments/scheduled_at 独立)、monitor PUT(None 以外のフラグのみ)、blackout PUT、Template/Monitor 更新系はいずれも `None` が「変更しない」を意味し、明示的な空(`[]`/`""`)とは区別される。

---

## 8. フロントエンド画面一覧

Vite + React + TS + Tailwind の SPA。`App.tsx` がシェル、`api.ts` が唯一のバックエンド通信層、`types.ts` が共有型。ビュー切替は URL ルータを使わずローカル state `view` の条件レンダリング(URLハッシュ `#inbox` 等と双方向同期する `viewFromHash`、初期値はハッシュ→無ければ `"compose"`。リロードしても画面が保持される)。サイドバー下部に API 接続バッジ(10秒ごと ping)・「投稿先: @username」・ビルド時刻(`__BUILD_TIME__`、`npm run build` 忘れ検知用)を表示。**lg 未満(スマホ)ではサイドバーがハンバーガー付きドロワーになる**(fixed+translate、backdrop タップで閉じる)。

### 共通規約

- **時刻**: バックエンドは naive UTC ISO を返す。表示時は `+ "Z"` を付けて UTC 解釈し `toLocaleString("ja-JP")` 等で日本時間に変換。予約送信時は JST 壁時計値 `"YYYY-MM-DDTHH:MM"` に `:00+09:00` を付ける(`toScheduledAtISO`)。この naive UTC ⇔ JST 壁時計の変換規約がフロント全体の不変条件。
- **エラー**: `api.req<T>()` は `!res.ok` で `body.detail` を含む `Error("ステータス: detail")` を throw。各画面が文字列化して表示。
- **API base と認証**: `BASE` は `VITE_API_BASE` 明示指定 > dev(`npm run dev`)なら `http://localhost:8000` > 本番ビルド(同一オリジン配信)なら相対パス `""`。localStorage のトークン(`xagent_api_token`)を全リクエストに `X-API-Token` ヘッダで自動付与し、**401 を受けると `xagent:unauthorized` イベント → App がログイン画面(`views/Login.tsx`、`/status` でトークン検証)を表示**。サーバの `API_TOKEN` 未設定時は 401 が発生しないためログイン画面は一切出ない(ローカル運用の挙動不変)。
- **二段階確認**: `useBlackoutGate` フックが制限帯の二段階確認ゲートを提供。`gate(onProceed, at?)` が `blackoutStatus` を引き、非ブロックなら即 `onProceed(false)`、ブロックなら stage1「警告を無視」→ stage2「最終確認」→ `onProceed(true)`。判定取得失敗時はそのまま実行(サーバ側ガードが最後の砦)。

### 各画面

| 画面 | 用途 | 主操作 |
|---|---|---|
| **Compose** | 思いつきを整形して下書き化 | ライブプレビュー(400msデバウンス)、メディア添付(画像4/動画1/混在不可)、emulate選択、案の数(1〜4)、型選択(既定`auto`)、長文許可/raw。指令マーカー(URL or「そのまま」等)があれば「指令を解析」→ confirm → command |
| **Inbox** | メンション返信案・絡み案の承認/編集/送信 | `kind !== "post"` のみ表示し、性質が違うので**返信案(sky)と引用案/引用RT(violet)に分けて表示**(`kind==="reply"`/`"quote"`)。「監視を1回実行」(横の件数入力でその回の生成上限を指定・既定10、`monitorRunOnce(limit)`)。**「URLから引用案を作る」手動ボタン**(URL入力＋ボタン→`POST /compose/quote-from-url`→`service.create_quote_draft` で相手投稿からAIが引用コメントを生成し引用案セクションに追加。自分のコメントを添える引用は Compose の指令フロー)。**返信案はAPI送信不可のため「コピーして元ポストを開く」(返信文をクリップボードにコピー＋相手ポストを別タブで開く=ポップアップブロック回避でwindow.open先行)＋「却下」のみ**(注記を上部に表示)。承認導線は無く DRAFT のまま手動送信。引用案は「承認して送信」(BlackoutGate経由、`approve→postNow` 順)/「承認のみ」/「却下」 |
| **News** | XNewsBot のニュースから速報下書きを生成 | 「今すぐ生成」(`newsRunOnceStart`→`pollJob` で進捗表示、結果は「新着なし/速報価値なし/N件作成」を区別して表示)、自動生成トグル(`auto_news_enabled`・既定OFF)+1回の上限(onBlurで保存)、直近2日のニュース一覧(処理済みカーソルより新しいものに「未生成」バッジ)。マウント時 `GET /jobs` で `label=="news-run-once"` の実行中ジョブへ再接続。DB不在時は `available=false` の案内を表示 |
| **Queue** | 自分投稿の下書き・予約・投稿・取消をタブ管理 | タブ: 下書き(post限定)/承認済み/予約/投稿済み/取消。**全タブで返信(reply)は除外**(API投稿不可のためInboxで手動送信)。queued/approved 表示時に `reconcileSchedules` を1回実行(失効復旧)。最適時間予約/時間指定予約(SchedulePicker)/今すぐ投稿/取消。BlackoutGate は「今すぐ投稿」「時間指定予約」のみ(最適予約・取消は通さない) |
| **Posts** | 直近1週間の自分投稿を通常リポストで再拡散 | recentPosts/refreshPosts、リポスト(now/time)。Inbox/Queue と並んで `useBlackoutGate` を直接使う画面(投稿・予約を伴う3画面)。Compose は下書き作成専用のため BlackoutGate を使わない |
| **Targets** | 絡む対象の管理 | リストを丸ごと対象化(推奨、`addTargetList`)、個別ハンドル追加、削除。kind=list は「リスト連携」バッジ、個別は user_id 解決済み/未解決バッジ |
| **Lists** | X ネイティブ「リスト」管理 | 一覧/作成(改行・カンマ区切り)/設定保存/削除/メンバー追加・除外。`added`/`skipped` をトースト。最も状態が多い画面 |
| **Templates** | 投稿/リプの「型」管理 | 種別ごと一覧、作成/編集/既定化(`activate`、1kind1つ)/削除。内蔵型も編集・削除可能 |
| **Style** | 文体管理 | スタイルガイド(常時適用)保存、自分の過去投稿学習(`learn`)、アカウント学習(`learnProfile`、50/100/200件・is_self)、学習済みプロファイル一覧 |
| **Analytics** | コスト・集計の閲覧専用 | `cost`(X API/Claude API 2カラム+内訳)、`summary`(ステータス別件数)。操作要素なし |
| **Settings** | 監視・制限帯 | 監視ソーストグル(楽観的更新)、`max_drafts_per_run`、`min_impressions`(絡み候補の最低インプレッション・onBlur保存)、`auto_monitor_enabled`(絡み案の自動生成オンオフ・既定OFF)、**有名人ウォッチカード**(`celeb_watch_enabled` トグル・既定OFF、`celeb_list_id` の設定状況表示、「今すぐチェック」=`celebRunOnceStart`→`pollJob` 進捗表示でエラー/候補0/生成N件をトースト分岐)、ブラックアウト編集(保存ボタンを押すまで反映されない) |
| **Agent** | 静的な説明画面 | Claude Code への話しかけ方(言う言葉→やること→等価CLI)を3カテゴリで表示。API 呼び出しなし |

### 主要共通コンポーネント

- **DraftCard**: 下書き1件の中核カード。種別/ステータスバッジ、`schedule_missed` の「予約失効」バッジ、元ポスト表示(reply/quote/repost)、メディアプレビュー。編集可否 `canEdit = onUpdated && kind !== "repost" && status ∈ {draft,approved,queued}`。字数表示は **コードポイント数**(`[...text].length`)、140字超で赤。
- **SchedulePicker**: `datetime-local` 代替。JST 壁時計の `"YYYY-MM-DDTHH:MM"`。クイック(1h後/3h後/今夜21時/明日8時)+おすすめ時間チップ+日付/時刻ドロップダウン(15分刻み、2週間先まで)。
- **BlackoutGate / useBlackoutGate**: 上記の二段階確認ゲート。`element` を画面に一度描画しておく必要がある。
- **ConfirmDialog**: 誤投稿防止モーダル。**既定フォーカスはキャンセルボタン**(Enter連打誤送信防止)。背景クリック・Escape はキャンセル扱い。
- **Toast**: success/info は8秒で自動消去、**error は手動で閉じるまで残る**。
- **ui / AgentHint**: 基本部品集(Button/Switch/Card/Badge/Textarea/Input/Spinner)とエージェント説明用折りたたみヒント。

---

## 9. CLIコマンド / MCPツール一覧

CLI(`xagent/cli.py`、エントリ `main()`)と MCP サーバ(`xagent/mcp_server.py`、エントリ `main()`、サーバ名 `"xagent"`)は同一サービス層の上の2つの操作面。DB に触れる全コマンド/ツールは実行前に `init_db()` を通す。`raw=True`=「整形せず最終文をそのまま下書き化」、`emulate=@handle`=「学習済みアカウントの口調に寄せる」。

### CLI コマンド(`xagent <command>`)

| コマンド | 主な引数 | 用途 |
|---|---|---|
| `version` / `init-db` | — | バージョン表示 / テーブル作成 |
| `preview` | `text`, `--long` | LLM不使用の整形構造確認(字数・折りたたみ・分割) |
| `compose` | `text`, `--long`, `--raw`, `--emulate`, `--variations N`(最大5) | 整形して下書き作成。`variations>1` かつ非raw なら複数案 |
| `reply` | `target`, `text`, `--raw`, `--emulate` | 指定ツイートへの返信下書き |
| `quote` | `target`, `text`, `--raw`, `--emulate` | 指定ツイートの引用RT下書き |
| `learn-account` | `handle`, `--max`(200), `--self` | アカウント学習・特徴抽出 |
| `profiles-list` | — | 学習済みプロファイル一覧 |
| `monitor-config` | `--mentions/--manual/--keyword/--following`(各 no- あり), `--max N` | 監視トグルと生成数上限の表示/変更 |
| `list` | `--status`, `--json` | 下書き一覧 |
| `show` | `draft_id`, `--json` | 下書き詳細(未発見は BadParameter) |
| `approve` / `reject` / `cancel` / `restore` | `draft_id` | 承認/却下/取消/復元 |
| `queue` | `draft_id`, `--at <ISO>` | キュー投入(`--at` なしで最適時間) |
| `post` | `draft_id` | 承認済みを即時投稿(要X資格情報) |
| `targets-add` / `targets-list` | `handle` / — | 絡み対象 追加/一覧 |
| `x-list-create` | `name`, `accounts`, `--file`, `--description`, `--private/--public` | X リスト作成+一括メンバー追加 |
| `style-set` / `style-show` | `text` / — | スタイルガイド 設定/表示 |
| `learn` | — | 自分の過去投稿を学習 |
| `monitor-once` | — | 受信監視を1サイクル実行 |
| `daemon` | `--poll-seconds`(180), `--queue-seconds`(60) | 監視+投稿キューの常駐(`BlockingScheduler`) |
| `serve` | `--host`(127.0.0.1), `--port`(8000) | FastAPI 起動 |

### MCP ツール

CLI とほぼ対応する。差分・専用ツール:

- **CLI と同等**: `preview` / `compose`(ただし `variations` 無し=常に1件)/ `reply` / `quote` / `list_drafts` / `get_draft` / `approve` / `reject` / `cancel` / `restore` / `queue` / `post`(戻りに `posted_tweet_ids`)/ `monitor_once` / `monitor_settings`。
- **MCP 専用**(CLI に無し):
  - `update_segments(draft_id, segments)`: 承認前の本文(セグメント)手修正。
  - `recent_posts(days=7)`: DB キャッシュ済みの自分の直近投稿(読むだけ。取得更新は API/デーモン側)。
  - `repost(tweet_id, at=None)`: 自分の過去投稿を通常リポスト。**作成・承認・投稿を一気通貫**(対象は自分の過去投稿に限定)。
  - `create_x_list` / `list_x_lists` / `get_x_list_members`: X リスト操作。

導入: `pip install -e ".[mcp]"`、起動は `xagent-mcp` または `python -m xagent.mcp_server`(stdio トランスポート)。

### CLI/MCP の差分・ハマりどころ

- `compose --variations` は **CLI 限定**(MCP は1件のみ)。
- `repost` は**自分の過去投稿専用**(コメント無し)。引用RT は `quote`。
- CLI の `reject` は対象未発見でも例外を出さず無言で終わる(他コマンドは BadParameter を投げるのと非対称)。MCP 未発見は `{"error": "not found"}`。
- `_fetch_target_text` は資格情報が無くても通る(reply/quote の下書き作成自体は X クレデンシャル無しで成功し、元ポスト本文が空になるだけ。実際の `post` で初めて資格情報必須)。

---

## 10. 監視・絡み生成・スケジューラ・デーモンの動作詳細

### 受信監視・絡み案生成(`monitor.run_once`)

`run_once(session, x_client, formatter, me_user_id, max_drafts=None) -> {"reply_suggestions": replies, "quote_suggestions": quotes}` が1監視サイクル。流れは **「メンション個別処理 → 候補収集 → 1回のバッチAI選定 → Draft化」**:

1. **メンション(`poll_mentions`、トグル `mentions_enabled`・既定OFF=手動で返す方針)**: 従来通り `MonitorCursor`(stream=`"mentions"`)の `since_id` で重複を防ぎ、1件ずつ `create_reply_draft` で返信案を作る(自分宛は引用RTしないので `quote_suggestions` には乗らない)。`_cap_oldest(tweets, limit)` は超過時に **id 昇順(古い順)**で先頭 `limit` 件だけ残し、カーソルを最新まで飛ばさないため新着は次サイクルで再取得される。
2. **候補収集(`collect_engage_candidates`)**: 全有効ソース(手動MANUAL/リストLIST=トグル `manual_targets_enabled` が両方を支配、ジャンルGENRE=`keyword_search_enabled`、フォロー中=`following_enabled`)から**直近24時間(`TARGET_MAX_AGE_HOURS=24`)の本人オリジナル投稿**を集める。**since_id カーソルは使わない**(24時間窓で毎回取り直す)。除外は3段: (a)**リポスト除外(`_drop_reposts`)**=正規化辞書の `is_repost`(twitterapi.io は `retweeted_tweet`/`quoted_tweet` の有無、公式API は `referenced_tweets` の type=retweeted/quoted)＋後方互換の本文 `RT @` 始まり、(b)**既に同じ tweet を対象にした Draft があれば状態問わず除外**(REJECTED 含む=却下済みへの再生成をしない・乱造防止と重複防止を兼ねる)、(c)ソース横断の tweet_id 重複排除、(d)**最低インプレッション(`min_impressions`、既定10000)未満を除外**(`view_count` が None=判定不能の投稿は `_within_age` と同じ思想で通す)。候補は新しい順(id降順)に `SELECT_MAX_CANDIDATES=120` 件まで(LLM入力の安全弁)。候補には `like_count`/`retweet_count`(twitterapi.io の `_norm` が保持)も載せ、伸び始め判断の材料にする。
3. **バッチAI選定(`formatter.select_engagements`)**: 候補一覧を1回のLLM呼び出し(JSONスキーマ強制)に渡し、「どの投稿に絡むか(**同一アカウントは原則1件・最大2件**=分散)」「reply / quote のどちらか(迷えば reply)」「本文(140字厳守)」「選定理由」までまとめて生成する。コード側でも検証(候補に無いID破棄・140字超破棄・kind正規化・同一アカウント3件目以降破棄・`max_n`=残バジェットで打ち切り)。
4. **Draft化(`service.create_engage_draft_from_text`)**: 本文は生成済みなので**LLMを呼ばずに** `status=DRAFT` の下書きを作る。選定理由は `source_text` に `[AI選定理由] ...` として保持(Inboxの承認判断材料)。型を変えたいときは従来通り Inbox の切替ボタン=`recast_engage_draft`。

重要な不変条件:
- 投稿はせず未承認下書き(`status=DRAFT`)を生成するだけ(自動投稿しない)。
- **`kind==FOLLOWING` の `EngageTarget` 行はどのソースでも読まれない**(フォロー中は `get_following` で自分のフォローを直接巡回。Enum とテーブルの不一致=ハマりどころ)。
- LIST は収集ごとに `get_list_members(max_total=LIST_MEMBERS_MAX=500)` でメンバーを**毎回取り直す**ため、X リスト側の増減が次サイクルで自動反映される。
- 定数: `FOLLOWING_MAX_ACCOUNTS=20`、`LIST_MEMBERS_MAX=500`、`TARGET_MAX_AGE_HOURS=24`、`SELECT_MAX_CANDIDATES=120`。
- `auto_monitor_enabled` / `auto_post_enabled` は `monitor.run_once` 内では参照されない(デーモン側のティックゲート)。
- 対象ルール(本人オリジナルのみ等)は memory `xagent-monitor-targeting-rules` に恒久記録。
- 生成数バジェット(`max_drafts_per_run`、手動1回実行は `max_drafts` でその回限りの上限)はメンションとバッチ選定で共有する総生成数上限。

### 有名人ウォッチ(`monitor.run_celeb_once`)

`run_celeb_once(session, x_client, formatter, max_drafts=None, progress=None) -> {"candidates", "reply_suggestions", "quote_suggestions"}`。専用Xリスト(`celeb_list_id`)の有名人の **AI言及投稿だけ**を検索で即検出し、reply 案を作る軽量ストリーム(機能の狙いと検索式は§6)。`run_once` との差分:

- **検出は検索ベース**: `get_list_members` でメンバーを取り、`_celeb_queries`(`CELEB_SEARCH_CHUNK=12` 人ずつ `from:` OR連結 × `AI_TOPIC_KEYWORDS` OR × `within_time:24h`)を `search_recent(since_id=カーソル)` で実行。タイムライン巡回をしないため **9〜12人なら検索1クエリ/回**で済む。
- **since_id カーソルを使う**(`MonitorCursor(stream="celeb")`)。raw 全ヒットの最大IDまで前進させるため、**選定落ちした投稿も二度LLMにかけない**(`collect_engage_candidates` の24時間窓・毎回取り直しとは逆の設計)。
- **min_impressions を適用しない**(投稿直後の素早い反応を優先=閾値免除)。RT除外・24h窓・Draft既存除外は同じ。
- 候補に `note`(即時反応の狙い)を付けて `select_engagements` へ。1回の生成上限は `max_drafts or CELEB_MAX_PER_TICK=3`。
- `celeb_list_id` 未設定なら例外でなく result 内 `error` で案内(ジョブを 500 にしない)。

### 最適時間スロット(`scheduler.py`)

- `DEFAULT_SLOTS_HOUR=(8,12,16,19,21,22)`(JST)。`next_optimal_slot(now_utc, taken_utc, ...)` は `now` 以降・既予約と絶対差90分以上離れた直近スロットを naive UTC で返す純関数。見つからなければ `now_utc + horizon_days(14)`。
- `recommended_times(now_utc, count=3, ...)`: UI 初期値用。slots/note/sources + 重ならない直近 count 件を `next_slots`(ISO 配列)で返す。
- `schedule_optimal(session, draft, ...)`: 承認済みを最適スロットへ割り当て `service.queue_draft` でキュー投入。

### 予約キュー消化(`process_due_queue`)

`due_drafts(session, now)` は `status==QUEUED` かつ `scheduled_at != None` を取得し Python 側で `scheduled_at <= now` のみ返す(予約時刻のないキューは自動投稿対象にしない=誤爆防止)。`process_due_queue` は先に `reconcile_missed_schedules` → `due_drafts` を `post_draft(trigger=SCHEDULED, override=draft.blackout_override)` で順に投稿。戻り `{posted, skipped, errors, missed}`。

失効と due の重なり: `reconcile` が先に走るため、`scheduled_at <= now - 30分` の予約は `due_drafts` 取得前に `APPROVED` へ外れ、同ティック内で投稿と失効が競合しない。

### ブラックアウト(`blackout.py`)

`is_blackout(now_utc, settings_row, tz_name="Asia/Tokyo") -> (bool, reason)`:
1. `enabled=False` → `(False, "")`
2. `now_utc` を JST に変換、`local.weekday()` が制限曜日集合に無ければ `(False, "")`
3. 各 window で `start <= cur < end`(start 含む・end 含まない)なら `(True, "<曜>曜 HH:MM–HH:MM は制限時間帯です。")`

境界エッジ: 12:00 ちょうどは `["09:00","12:00"]` に該当しない(end 排他)。曜日は **JST のローカル曜日**で判定(UTC 日付ではない)。`set_blackout_settings` は不正な `"HH:MM"` を黙って除外、JSON 破損時は空集合/空リストへフォールバック(例外を投げない)。

override: `ensure_not_blackout(in_blackout, override, reason)` は `in_blackout and not override` のときだけ `BlackoutViolation`(`PolicyViolation` のサブクラス)。`post_draft` は `override or draft.blackout_override` を渡す。

### 常駐デーモン(`daemon.py` + API 内蔵スケジューラ)

3つのティック関数。いずれも例外を内部で握ってデーモンを止めない。

| 関数 | ゲート | 動作 |
|---|---|---|
| `monitor_tick()` | `auto_monitor_enabled` が OFF(既定) なら**即 return**(`XClient.from_settings` すら呼ばず API 消費ゼロ)。API常駐スケジューラに登録され、UIトグルONの時だけ実処理 | `get_me()` → `monitor.run_once(...)`。提案が1件以上なら `notify` で承認待ち通知。下書きのみ生成・自動投稿しない |
| `news_tick()` | `auto_news_enabled` が OFF(既定) なら**即 return**(XNewsBot の DB も LLM も触らない) | `news.run_news_once(...)`。新ダイジェスト検知で速報下書きを生成(ダイジェスト連動)、1件以上作れば `notify`。`NewsSourceUnavailable` は warning ログのみ(デーモンを止めない) |
| `celeb_tick()` | `celeb_watch_enabled` が OFF(既定) **または `celeb_list_id` 未設定**なら**即 return**(API消費ゼロ) | `monitor.run_celeb_once(...)`。有名人のAI言及を検索検出して reply 案を生成、1件以上作れば `notify`。`XClientError` は warning ログのみ |
| `queue_tick()` | ゲートなし(**常時実行**。「予約投稿は止めない」方針) | 発火のたびモジュール変数 `_last_queue_tick_at`(naive UTC)を更新(X資格情報の有無に関わらず先に。`/status` のハートビート)→ `process_due_queue(...)` |

緊急停止の責務分担: 全投稿停止は `config.posting_enabled`、個別の制限帯/頻度ガードは `process_due_queue`→`post_draft`。`auto_post_enabled` は `queue_tick` のゲートに**使われていない**(未使用)。

スケジューラ稼働の可視化: `lifespan` は `app.state.scheduler` に `BackgroundScheduler` を保持し、`queue` ジョブを `next_run_time=now` で登録して起動直後に1回発火させる(ハートビート即時化)。`GET /status` がこのハートビートとジョブの `next_run_time` から `healthy` を判定し、フロント App のサイドバーで「予約スケジューラ 稼働中/停止?」バッジに表示する(起動し忘れ・ハングをUIで検知)。

実運用構成: API プロセスの `lifespan` 内で `BackgroundScheduler(timezone="UTC")` が `queue`(60秒)・`monitor`(180秒、`max_instances=1`, `coalesce=True`)・`news`(600秒、`max_instances=1`, `coalesce=True`)・`celeb`(600秒=`celeb_interval_seconds`、`max_instances=1`, `coalesce=True`)を登録。monitor は `auto_monitor_enabled`、news は `auto_news_enabled`、celeb は `celeb_watch_enabled`(いずれも既定OFF)で内部ゲートされ、UIトグルONの時だけ実処理する。`daemon.run`(`BlockingScheduler` 版、CLI `xagent daemon`)は別経路で、launchd 常駐構成では実際に回るのは内蔵スケジューラ。

---

## 11. 運用Runbook

### 再起動 / 自動反映

launchd 常駐(`com.tomato.xagent`、`KeepAlive` 有効)。plist は `~/Library/LaunchAgents/com.tomato.xagent.plist`。

**コード変更は自動反映される(2026-06-09〜)。** plist の uvicorn 引数に `--reload --reload-dir /Users/tomato/Project/XAgent/xagent` を追加済みで、`xagent/` 配下の `.py` を**保存した瞬間(約1秒)に worker が再起動して新コードを読み直す**(手動 kickstart 不要)。`--reload-dir` を Python パッケージ(`xagent/`)に限定しているため、ルート直下の `xagent.db`・`media/`・ログ(`~/Library/Logs`)の書き込みでは再起動ループにならない(`watchfiles` を venv に導入済み)。さらに保険として **git `post-commit` フック**(`githooks/post-commit`、`core.hooksPath=githooks`)が**コミット毎に** `launchctl kickstart -k` で確実に作り直す(構文エラーで reloader 親ごと落ちた場合の復帰も兼ねる)。

手動再起動が必要なのは **plist 自体の変更・依存追加・マイグレーション反映**のときだけ:

```
# plist定義の変更を反映するとき(引数変更など。kickstartでは反映されない)
launchctl bootout gui/$(id -u)/com.tomato.xagent
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tomato.xagent.plist
# 通常の手動再起動(plist定義は不変・プロセスだけ作り直す)
launchctl kickstart -k gui/$(id -u)/com.tomato.xagent
```

`--reload` は reloader(親, PPID=launchd)+ worker(子)の2プロセス構成になる。`kill` は使わない(launchd が即再生成)。予約投稿は DB 永続(`status==QUEUED`+`scheduled_at`)で `queue_tick`(60秒)・`reconcile_missed_schedules`(猶予30分)が再検索するため、reload の数秒再起動で取りこぼし・二重投稿は起きない。

### テスト

```
pip install -e ".[dev]"     # pytest>=8
pytest                       # tests/ 配下(現状 約225テスト関数 / 20ファイル。READMEの「約35」は旧値)
```

テスト基盤: インメモリ SQLite + `BlackoutSettings(enabled=False)` シードで実時刻非依存。API テストは `StaticPool` で単一コネクション共有、`app.dependency_overrides` で `db_session`/`get_formatter`/`get_x_client` をフェイクに差し替え。FakeFormatter(LLM 呼ばない)・FakeXClient(書込記録・読取注入)で挙動を検証。

### ビルド(フロントエンド)

```
cd frontend
npm install
npm run dev        # vite。ポート 5180 固定(strictPort)
npm run build      # vite build
npm run typecheck  # tsc --noEmit
```

注意: README 本文には `# http://localhost:5173` とあるが**記述ミス**。実際の起動ポートは **5180**(`vite.config.ts` が `strictPort: true` で自動フォールバックしない)。

**フロントのコードを変えたら `npm run build` を忘れない**: 本番UI(`:8000` および ts.net 経由)は `frontend/dist` を配信するため、build しないと古いUIが配信され続ける(API 側の再起動は不要。StaticFiles はリクエスト毎にディスクを読む)。サイドバー下部の `build: M/D HH:mm` 表示が古ければ build 忘れ。

### リモートアクセス(Tailscale)

```
# 初回のみ: tailnet 内に HTTPS プロキシ(再起動後も永続)
tailscale serve --bg 8000
tailscale serve status   # 確認(https://<mac名>.<tailnet>.ts.net → 127.0.0.1:8000)
tailscale serve reset    # 解除
```

- uvicorn は `127.0.0.1` バインドのまま(インターネット非公開。tailnet 内のみ到達可)。
- 認証: `.env` に `API_TOKEN=<ランダム値>` を設定(`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` で生成)→ `.env` は `--reload-dir` の対象外なので `launchctl kickstart -k` で反映。スマホ初回アクセス時にログイン画面でこの値を入力(localStorage 保存)。
- 共同運営者のアクセス: 自分の tailnet にユーザーとして招待(無料プランは3ユーザーまで)が最も確実。代替はノード共有(共有先からの serve 到達性は実機検証が必要)。
- HTTPS が必要な理由: iOS の clipboard API(Inbox の「コピーして元ポストを開く」)が secure context 必須。
- 注意: Mac がスリープすると外から使えない。システム設定 →「電源アダプタ接続時はスリープさせない」を推奨。`/media/files` と `/health` は無認証(tailnet 内限定の露出として許容)。

### ログ

| 種別 | パス |
|---|---|
| stdout | `~/Library/Logs/xagent.out.log` |
| stderr | `~/Library/Logs/xagent.err.log` |

### ポート

- API+本番UI: `127.0.0.1:8000`(ローカルバインド。リモートは tailscale serve の `https://<mac名>.<tailnet>.ts.net` 経由)
- フロント開発サーバ: `127.0.0.1:5180`

### DB

SQLite `xagent.db`(`DB_PATH` 既定)。容量上限 `MAX_DB_BYTES`(既定2GB)超過で端末状態の古い順物理削除+VACUUM。

---

## 12. 既知の注意点・ハマりどころ

### 時刻まわり

- **内部は naive UTC で統一**。aware と naive を混ぜて比較すると `TypeError`。外部(API/CLI)から来る datetime は必ず `service.to_naive_utc` を経由させる(`queue_draft`、API `update_draft`、CLI `queue --at`)。修正済み項目 M1。
- フロントは naive UTC ISO に `+ "Z"` を付けて表示、予約送信は JST 壁時計値に `:00+09:00`。二重サフィックスや混在で不正値になる。

### X 側拒否(403/502)

- 権限不足・レート超過などで X が拒否すると、drafts post では `XClientError` → **502** を返し、下書きは未投稿(`approved`)のまま残る。500 ではなく理由を返す設計。
- ただし「引用不可」エラーだけは 502 にする前に `service._post_quote` がURL埋め込み投稿へフォールバックするため、引用案が引用不可でも(通常投稿として)送信が成功しうる(上記§投稿実行を参照)。
- 一方、`XClientError` を捕捉せず **500** になりうる経路が2つある(drafts post の 502 と非対称):
  - **posts repost**(`posts.py` の `repost`)は `BlackoutViolation`(→423)/`PolicyViolation`(→409)のみ捕捉。即時投稿で X が拒否すると 500。
  - **POST /style/learn**(`style.py` の `learn`)は `try/except XClientError` を持たず、503 化されるのは `get_x_client` 依存(資格情報未設定で `XClient.from_settings` 失敗)のみ。実行時に `get_me()` / `learn_past_posts` 内の取得が `XClientError` を投げると 500 になる(posts repost と同じ穴)。

### 計量の二系統

- `exceeds_fold` / `split_into_thread` は **字数(コードポイント数)** 基準(140)、`FormatResult.weighted_total` は **加重** 基準。判定系統(字数)と表示系統(加重)が異なる。`FOLD_THRESHOLD_WEIGHTED=280` 定数は `exceeds_fold` では未使用。
- DraftCard の `charCount` もコードポイント数(`[...text].length`)であり weighted ではない。
- 折りたたみ閾値280は docstring 上**【要実測】**で安全側の既定値。

### フロントエンドのビルド/HMR

- README は「shadcn/ui」と書くが `package.json` 依存に shadcn/Radix 系が見当たらない(調査範囲では未導入)。

### Enum とテーブルの不一致

- `TargetKind.FOLLOWING` の `EngageTarget` 行はどの poll でも読まれない(§10)。フォロー監視は `following_enabled` トグル + 自分のフォロー直接巡回。

### 設定反映

- `get_settings()` は `lru_cache` のため**プロセス起動後の `.env` 変更は無反映**。`config.reload_settings()` で部分的に再生成できるが、`db.py` の `_engine` は再生成されないため `DB_PATH` 等の DB 接続系変更は**プロセス再起動が必須**(修正項目 M4)。

### プロセス競合

- `serve`(FastAPI)と `daemon` を**別プロセスで同時起動すると、同一 SQLite への書き込み競合で `database is locked`** が出うる(M6)。launchd 常駐とは別に手動で立てると競合する。単一プロセス運用が安全。

### 型シードの冪等性

- `seed_builtin_templates` は `name` の完全一致でのみ冪等判定するため、ビルトイン型を改名すると再 `init_db` で重複投入される。

### メディアの GIF 扱い

- `media.py` は GIF を `IMAGE_EXTS` に含めるため**画像扱い**(最大4枚・他画像と混在可)。docstring の「GIF も1個まで」「画像と動画(GIF)混在不可」という記述は実装と一致しない(docstring と実装の差異)。
- `save_bytes` は `is_allowed_filename` を内部で呼ばない(API 層 `/media/upload` が許可チェックを担う)。枚数/混在の検証は投稿時の `validate_media_set` 担当で、アップロード単発では行わない。

### XClient のフォールバック特例

- 読取は基本「`TwitterApiIoError` のときだけ公式フォールバック」だが、`get_list_members` だけは**「空配列(falsy)でも公式へフォールバック」**する(非公開リストは twitterapi.io が空を返すため)。
- `retweet` だけが書込系で唯一 `_guard` を通さず、tweepy 例外が `XClientError` に変換されずそのまま伝播する(意図的か否か**未確認**)。
- `get_owned_lists` / `get_list_members` の公式呼び出しは `user_auth=True` 必須(Bearer/app-only だと X が 503 を返す)。

### UX 表示(修正項目 M3)

- `poll_mentions`/`poll_genre` は `target_handle` に `author_id`(数値ID)を渡すため、リプライ案の表示が `@123456...` になる。ただし実投稿の宛先は `target_tweet_id` で行うため**送信は正しい**(表示のみの問題)。

### 未修正・記録のみ

- M5(security): `compose` の `media_paths` はローカル任意パス無検証。外部公開時に任意ファイルアップロードの危険(トークン認証+ローカルバインドで緩和)。
- L1(perf): `analytics/summary` がステータス毎に全件取得+`len()`(現規模では実害なし)。
- L4(仕様): 監視1ティックの下書き生成に上限なし(「下書きは自由生成・投稿だけ厳重」の方針で意図的)。コストは Analytics で可視化、コスト高ソースは既定オフ。

---

## 付録A. 環境変数一覧

`cp env.example .env` で作成。`.env` はコミットしない(`.gitignore` 済み)。値はコードに置かず環境変数で与える。全 API キーは Optional(未設定でも import 時にクラッシュしない)。

| 変数名 | 既定 | 意味 |
|---|---|---|
| `ANTHROPIC_API_KEY` | None | **未参照**(旧API直叩き時代の名残。CLI実行時の env からは除去される) |
| `CLAUDE_MODEL` | `claude-opus-4-8` | 整形に使うモデル(Claude Code CLI に `--model` で渡す) |
| `CLAUDE_CLI_PATH` | None | `claude` コマンドのパス。空なら PATH → `~/.local/bin/claude` の順で解決 |
| `CLAUDE_CLI_TIMEOUT_SECONDS` | 240 | CLI 1回の実行タイムアウト(秒)。フロントの fetch 既定(約300秒)内に収める |
| `X_API_KEY` / `X_API_SECRET` | None | X API OAuth1.0a(書込用) |
| `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | None | OAuth1.0a アクセストークン(メディアアップロード用 v1.1 にも必須) |
| `X_BEARER_TOKEN` | None | X API Bearer(読取用) |
| `TWITTERAPI_IO_KEY` | None | twitterapi.io キー(読取専用)。未設定なら読取も公式X(オプトイン) |
| `DB_PATH` | `xagent.db` | SQLite ファイルパス |
| `MAX_DB_BYTES` | 2GB(`2147483648`) | DB容量上限。超過で古い端末状態を物理削除 |
| `MEDIA_DIR` | `media` | 画像/動画保存先 |
| `MAX_POSTS_PER_DAY` | 10 | 自然な1日上限 |
| `HARD_CAP_POSTS_PER_DAY` | 100 | ハード上限 |
| `MIN_POST_INTERVAL_SECONDS` | 300 | 連投の最小間隔(秒) |
| `POSTING_ENABLED` | true | 緊急停止スイッチ。false で全投稿停止 |
| `SCHEDULER_ENABLED` | true | false で予約キューの自動処理を止める(手動投稿は可) |
| `SCHEDULER_INTERVAL_SECONDS` | 60 | 予約キュー点検間隔(秒) |
| `MONITOR_INTERVAL_SECONDS` | 180 | 絡み案自動生成の実行間隔(秒) |
| `API_TOKEN` | None | 設定時に全ルータ+`/me` `/status` で `X-API-Token` 必須(開放は `/health` と `/media/files` のみ)。空ならローカル開放 |
| `TIMEZONE` | `Asia/Tokyo` | 運用タイムゾーン |

設定の二重制御: 投稿の緊急停止は `POSTING_ENABLED`、絡み案の自動生成のオンオフは `MonitorSettings.auto_monitor_enabled`、ニュース速報の自動生成は `NewsSettings.auto_news_enabled`、有名人ウォッチは `MonitorSettings.celeb_watch_enabled`(いずれも既定OFF・UIトグル)。1日上限は `MAX_POSTS_PER_DAY`(自然)と `HARD_CAP_POSTS_PER_DAY`(ハード)の二段。

## 付録B. パッケージング・依存関係

`pyproject.toml`(パッケージ名 `xagent` / version `0.1.0` / `requires-python>=3.11`)。正は `pyproject.toml`、`requirements.txt` はミラー。

主要依存: `fastapi>=0.115`、`uvicorn>=0.30`(**`[standard]` を意図的に付けない**=Python 3.14 での uvloop/httptools ビルド回避)、`pydantic>=2.7`、`pydantic-settings>=2.3`、`sqlmodel>=0.0.21`、`tweepy>=4.14`、`APScheduler>=3.10`、`httpx>=0.27`、`python-multipart>=0.0.9`、`typer>=0.12`。`anthropic` SDK は不使用(LLM は Claude Code CLI のヘッドレス使い捨てセッション=`xagent/claude_cli.py` 経由・subscription課金。`--bare` は OAuth が読めなくなるため使わない)。

extras: `dev`=`pytest>=8`、`mcp`=`mcp>=1.2`。エントリポイント: `xagent`→`xagent.cli:main`、`xagent-mcp`→`xagent.mcp_server:main`。

フロント: React 19、Tailwind 4、Vite 6、TypeScript 5.6。

## 付録C. Claude Code連携資産

- **スキル `xpost`**(`.claude/skills/xpost/SKILL.md`): 「これ投稿して」等の依頼を受け、調査→バズ型選定→本文生成→承認待ち下書き投入まで自走。承認なしに `post` しない。自分で書いた最終文は `--raw`、雑メモは整形に任せる。
- **ワークフロー `x-viral-compose`**(`.claude/workflows/x-viral-compose.js`): 「調査→型別に複数案→採点→最良案」の3フェーズ(Research/Draft/Score)。`FORMATS=['リスト型(N選)', '逆張り型', 'ハウツー型']`。返り値に `createCommand`(`winner` があれば `xagent compose --raw <text>`、なければ null)。下書き投入は呼び出し側が `winner.text` を確認して実行。

両資産はコード本体(`service.post_draft`)の承認ゲートと完全に整合し、AI の自走範囲は下書き作成・予約まで、`post`(送信)は人間承認後のみ。
