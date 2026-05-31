# XAgent コードレビュー（セキュリティ＋バグ）

対象: バックエンド(`xagent/`)・FastAPI(`xagent/api/`)・フロント(`frontend/`)・CLI・MCP。
分類: `security` / `bug` / `perf` / `ux` / `ops`。重大度: 高 / 中 / 低。
本レビューで **高・中は可能な限り本タスク内で修正**し、残りは推奨として記録する。

最終更新: 2026-05-31

---

## サマリ

| ID | 重大度 | 分類 | 箇所 | 状態 |
|----|--------|------|------|------|
| H1 | 高 | security | API 書き込み系に認証なし | **修正済**（任意の `X-API-Token`） |
| R1 | 高 | bug | 予約なしキューの自動投稿（誤爆） | **修正済**（Phase 1: `ensure_post_authorized` / `due_drafts` 限定） |
| R2 | 高 | ops | 緊急停止スイッチ不在 | **修正済**（Phase 1: `posting_enabled`） |
| M1 | 中 | bug | `scheduled_at` の aware/naive 混在 | **修正済**（naive UTC へ正規化） |
| M2 | 中 | bug | `targets.py` の広すぎる `except` と `info["id"]` | **修正済**（限定 + `.get`） |
| M3 | 中 | ux | メンション/ジャンル絡み案の `target_handle` が数値ID | 記録（要追加 lookup・コスト増のため保留） |
| M4 | 中 | ops | `get_settings()` の `lru_cache` で `.env` 変更が無反映 | 記録 + `reload_settings()` 追加 |
| M5 | 中 | security | `media_paths` がローカル任意パス（検証なし） | 記録（トークン認証 + ローカルバインドで緩和） |
| M6 | 中 | ops | SQLite を serve と daemon の2プロセスで共有 | 記録（運用注意） |
| L1 | 低 | perf | `analytics/summary` がステータス毎に全件走査 | 記録 |
| L2 | 低 | bug | `extract_profile` のコードフェンス除去が脆い | 記録（フォールバックで吸収） |
| L3 | 低 | bug | 自分の旧学習 `PastPost` に `author_handle` 無し | 記録（自分の真似は `learn-account --self`） |
| L4 | 低 | robustness | 監視1ティックの下書き生成に上限なし | 仕様（下書きは自由生成の方針。コストは Analytics で可視化） |

---

## 修正した項目

### H1 — API 書き込み系に認証がない（高・security）
`xagent/api/` の各ルータは無防備で、ローカルバインド(127.0.0.1)前提だった。クラウド公開前の保険として
**任意のトークン認証**を追加。`config.api_token` を設定した場合のみ、書き込み系ルータ
(`compose`/`drafts`/`targets`/`style`/`monitor`/`profiles`/`analytics`)で `X-API-Token` ヘッダを必須にする。
未設定（既定）ではローカル開放のまま（挙動不変）。`/health`・`/me` は常に開放。
- 追加: `xagent/api/deps.py` の `require_api_token`。各ルータに `dependencies=[Depends(require_api_token)]`。
- 注意: トークンを設定したらフロント側も `X-API-Token` を送る必要がある（クラウド移行時に対応）。

### R1 — 予約なしキューの自動投稿（高・bug, Phase 1 で修正済）
旧 `scheduler.due_drafts` は `scheduled_at is None` のキューを「即時」とみなし、`daemon.queue_tick`(60秒毎)が
**認証も予約も無いのに投稿**しうる穴があった。`PostTrigger` と `ensure_post_authorized` を導入し、
SCHEDULED 経路は「QUEUED かつ `scheduled_at` 設定済み かつ 到来済み」のみに限定。

### R2 — 緊急停止スイッチ（高・ops, Phase 1 で追加）
`config.posting_enabled=False` で手動/予約とも全投稿を停止できるキルスイッチを追加。

### M1 — `scheduled_at` の aware/naive 混在（中・bug）
コードベースは内部時刻を **naive UTC** に統一している（`models._utcnow`, `scheduler`）。一方 API/CLI は
ISO文字列から **aware datetime** を生成しうる（`+09:00` や `Z` 付き）。これが混ざると
`due_drafts` の `scheduled_at <= now` 比較で `TypeError`、フロントの `new Date(scheduled_at + "Z")` も二重サフィックスで不正値になる。
- 修正: `service.to_naive_utc()` を追加し、`queue_draft` と API `update_draft`、CLI `queue --at` で正規化。

### M2 — `targets.py` の広すぎる `except`（中・bug）
`except (XClientError, HTTPException, Exception)` は全例外を握り潰し、`info["id"]` は `KeyError` の可能性。
- 修正: `except (XClientError, HTTPException)` に限定し、`info.get("id")` に変更。注入済み依存があるが既存実装に合わせ最小修正。

---

## 記録（推奨・未修正）

### M3 — 絡み案の `target_handle` に数値 author_id（中・ux）
`monitor.poll_mentions` / `poll_genre` は `target_handle=t.get("author_id")` を渡すため、
返信/引用案の表示や整形プロンプトで `@123456...`（数値ID）になる。正しい @handle 表示には
追加のユーザー lookup（READ コスト）が要るため保留。実投稿の宛先は `target_tweet_id` で行うため**送信は正しい**（表示のみの問題）。

### M4 — `get_settings()` の `lru_cache` 罠（中・ops）
プロセス起動後に `.env` を編集（キー更新・`posting_enabled` 切替）しても、`lru_cache` のため反映されない。
- 緩和: `config.reload_settings()` を追加（`get_settings.cache_clear()`）。運用上はプロセス再起動が確実。

### M5 — `media_paths` の無検証（中・security）
`compose` の `media_paths` はローカルファイルパスをそのまま `upload_media` に渡す。API が外部公開されると
任意ファイルのアップロードに繋がりうる。トークン認証 + ローカルバインドで緩和。将来はパスのホワイトリスト/アップロードAPIに限定すべき。

### M6 — SQLite の多重プロセス書き込み（中・ops）
`serve`（FastAPI）と `daemon` を別プロセスで同時起動すると、同一 SQLite への書き込み競合で
`database is locked` が出うる。単一プロセス運用か、将来は managed DB（Postgres）へ。

### L1 — `analytics/summary` の全件走査（低・perf）
ステータス毎に `select(Draft).where(...)` の結果を `len()` で数えており全件取得。件数増で非効率。
`func.count` 等に置換余地（現状の規模では実害なし）。

### L2 — `extract_profile` のコードフェンス除去（低・bug）
` ```json ... ``` ` のように後置テキストがあると JSON パースに失敗するが、失敗時は raw を `profile_text` に温存するため致命的でない。

### L3 — 旧 `learn_past_posts` の `PastPost` に `author_handle` 無し（低・bug）
`style.learn_past_posts`（自分の旧学習）は `author_handle` を埋めないため、`example_posts_for_account`（handle 一致）では拾えない。
自分の口調を「真似る相手」に使うときは `learn-account --self` を使う（`author_handle` が入る）。

### L4 — 監視1ティックの下書き生成に上限なし（低・robustness／仕様）
多投稿アカウントを対象にすると1ティックで多数の下書き＋LLM呼び出しが出うる。ただしユーザー方針は
「下書きは自由に出してよい（投稿だけ厳重）」のため**意図的に上限を設けない**。コストは Analytics で可視化、
コスト高のソース（フォロー中/キーワード）は既定オフ。

---

## 確認済みで問題なかった点
- 投稿経路は `service.post_draft` に集約され、全経路（CLI/API/MCP/scheduler）でガードを通過する。
- 生 SQL は使わず SQLModel 経由（`db._migrate` の `PRAGMA`/`ALTER` のみ生 SQL だが固定文字列・外部入力なし）。
- 秘密情報のログ出力は確認範囲で無し。`.gitignore` は `.env`/`.env.*`/`*.db`/`.venv`/`node_modules`/`frontend/dist` を網羅。
- CORS は `allow_credentials` 未設定（既定 False）でローカルポートのみ許可。
- レート制限（1日上限/連投間隔/ハード上限）は純粋関数で単体テスト済み。
