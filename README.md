# XAgent

テキストを投げるだけで、AIが自分のノウハウ口調に整形 → 承認 → Xへ半自動で投稿・エンゲージするエージェント。管理用のWeb UIダッシュボードを備える。

> 設計の背骨: X規約上、自動いいね/フォロー/RT/一斉リプライは禁止。**AIが下書き→人間が承認→送信** の半自動(human-in-the-loop)のみを行う。投稿頻度ガードを内蔵。

## 構成
- `xagent/` … コアライブラリ(整形・Xクライアント・DB・スケジューラ・ガード)。CLI/Web/監視デーモンが共有。
  - `text.py` … X加重文字数・「さらに表示」折りたたみ判定・スレッド分割(LLM不使用・テスト済み)
  - `guards.py` … 投稿頻度リミッタ・承認ゲート(テスト済み)
  - `config.py` / `models.py` / `db.py` … 設定・DBモデル・SQLite
  - `api/main.py` … FastAPIバックエンド(`/health`, `/compose/preview`)
  - `cli.py` … Typer CLI
- `tests/` … pytest
- `frontend/` … Web UIダッシュボード(React + Vite + TS + Tailwind + shadcn/ui)※後続フェーズ

## セットアップ
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp env.example .env   # 値を埋める(APIキー等)
```

## 動作確認・起動
```bash
# バックエンド
pytest                                   # 35テスト
xagent preview "整形したい長文…"          # LLM不使用の構造確認
xagent compose "今日のノウハウ" && xagent list   # 整形→下書き(要ANTHROPIC_API_KEY)
xagent serve                             # FastAPI: http://127.0.0.1:8000
xagent daemon                            # 監視＋投稿キューの常駐(Macローカル)

# フロント(別ターミナル)
cd frontend && npm install && npm run dev   # http://localhost:5173

# CLI主要コマンド
xagent approve <id> / post <id> / queue <id> [--at ISO]
xagent targets-add @handle / monitor-once / style-set "口調" / learn
```

## ステータス(構築順)
1. ✅ 基盤(コア・DB・ガード・FastAPI・テスト)
2. ✅ 整形(Claude)＋承認＋投稿(`formatter`/`service`/`x_client`、承認ゲート・頻度ガード)
3. ✅ Web UI(Compose/Queue/Inbox/Targets/Style/Analytics、React+Vite+Tailwind)
4. ✅ スケジューラ(最適時間分散/指定時刻予約、`scheduler`)
5. ✅ 監視デーモン＋Inbox(メンション返信案/絡み案、`monitor`/`daemon`)
6. ✅ 絡み対象リスト(`EngageTarget`、CLI/UIで管理)
7. ✅ Analytics(コスト集計)・スタイル学習(過去投稿API取得)・Claude Code連携(MCP/skill)

残: ライブ検証(APIキー設定後)、画像添付UI、クラウド24h常駐、「さらに表示」閾値の実測。

詳細プラン: `~/.claude/plans/x-x-ai-ai-x-x-snappy-meadow.md`
