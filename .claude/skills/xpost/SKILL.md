---
name: xpost
description: XAgentでX(Twitter)投稿・絡みを自走で準備する。「これ投稿して」「Xに書いて」「整形して」「このポストに絡んで/リプライして/引用して」「〜をバズる形で投稿準備して」等で使う。調査→バズ型選定→本文生成→下書き作成までエージェントが進め、投稿は人間承認で停止する半自動フロー。
---

# xpost — XAgent 投稿・絡みの自走フロー

ユーザーの要望を、調査して本文まで書き、**承認待ちの下書き**として XAgent に投入する。
完全自動はしない（X規約・凍結回避）。投稿は人間が WebUI / `xagent post` で承認・送信する。

CLI は `xagent`（リポジトリの venv で `pip install -e .` 済み前提）。DB はローカル SQLite。
機械可読に読みたいときは `--json` を付ける。

## 自走フロー（例:「Claude活用術5選をバズる形で投稿準備して」）
1. **調査**: 必要なら WebSearch / `x-research` スキルでネタ・事実・今の伸び方を集める。
2. **型を選ぶ**: `references/viral-formats.md` の型ライブラリから、題材に合うバズ型を**自分で選定**する
   （例: 「5選」ならリスト型、逆張りが効くなら逆張り型）。XAgent規範（140字・折りたたみ閾値・
   ハッシュタグ控えめ）を守る。
3. **本文を書く**: 選んだ型で最終本文を書き上げる。
4. **下書き作成**: 自分で書いた最終文は再整形しないため `--raw` で投入する。
   `xagent compose --raw "<本文>"`
   - 雑メモを XAgent の整形に任せる場合は `--raw` なし: `xagent compose "<メモ>"`
   - 言い回し違いを複数出す: `xagent compose "<メモ>" --variations 3`
   - 長文で「さらに表示」を狙う: `--long`
5. **提示**: 出力の `#<id>` と本文をユーザーに見せ、OK か尋ねる。修正指示があれば作り直す。
6. **承認・投稿/予約**（ユーザーがOKしてから）:
   - 今すぐ: `xagent approve <id> && xagent post <id>`
   - 最適/指定時刻に予約: `xagent approve <id> && xagent queue <id> [--at <ISO>]`

## 絡み（リプライ / 引用 / 受信監視）
- **このポストにこう絡んで（自分の文で）**: URL を渡してリプライ/引用の下書きを作る。元ポスト本文も保存される。
  - リプライ: `xagent reply <URL> "<返信文>"`（`--raw` で整形なし）
  - 引用RT: `xagent quote <URL> "<コメント>"`
- **受信監視で絡み案を生成**: `xagent monitor-once`（メンション返信案・対象アカウントへの絡み案を下書き化）
- 一覧 → 確認 → 承認 → 送信: `xagent list --status draft --json` → `approve` → `post`

## 生成数の制御（APIを圧迫しない）
監視 1 回で作る下書きの**総数上限**を設定できる。「絡み案を5件だけ」等の要望はこれで制御する。
- `xagent monitor-config --max 5`（上限を5に）→ `xagent monitor-once`
- 現在値の確認も `xagent monitor-config`（引数なし）。

## 口調・対象
- 真似る口調: `xagent compose "<メモ>" --emulate <handle>`（学習済みのみ）/ 学習: `xagent learn-account <handle>`
- 常時の口調: `xagent style-set "<スタイルガイド>"` / 自分の過去投稿から学習: `xagent learn`
- 絡む相手を追加: `xagent targets-add @handle`

## 重要な原則
- **承認なしに `post` しない**。必ずユーザーの承認を得てから投稿する。下書き作成・予約までが自走の範囲。
- 投稿頻度ガード（1日上限・連投間隔）・制限時間帯に引っかかったら理由をユーザーに伝える。
- X / Anthropic の資格情報が未設定なら、`.env`（`env.example` 参照）の設定を案内する。
  資格情報が無くても下書き作成は可能（元ポスト本文の取得だけスキップされる）。

## 参照
- バズ型の選定根拠: `references/viral-formats.md`
- より大規模な「調査→型別に複数案→採点→最良案を下書き」を自動化したい場合は、
  ワークフロー `x-viral-compose`（`.claude/workflows/x-viral-compose.js`）を使う。
