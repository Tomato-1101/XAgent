export const meta = {
  name: 'x-viral-compose',
  description: '題材を調査し複数のバズ型で投稿案を作り採点して最良案を返す(下書き投入は呼び出し側)',
  whenToUse: '「〜をバズる形で投稿準備して」等、調査から本文作成まで自動で進めたいとき',
  phases: [
    { title: 'Research', detail: 'WebSearchで題材のネタ・角度を集める' },
    { title: 'Draft', detail: '型別(リスト/逆張り/ハウツー)に本文を生成' },
    { title: 'Score', detail: '各案を辛口採点して最良案を選ぶ' },
  ],
}

// 使い方: Workflow({ name: 'x-viral-compose', args: 'Claude Code活用術5選' })
// 返り値の winner.text を確認し createCommand(`xagent compose --raw "..."`)を実行すると
// 承認待ちの下書きが作られる。投稿そのものは人間が承認してから(自動投稿はしない)。

const RESEARCH_SCHEMA = {
  type: 'object',
  properties: {
    findings: { type: 'array', items: { type: 'string' }, description: '検証できる事実/Tips' },
    angles: { type: 'array', items: { type: 'string' }, description: '伸ばせる切り口/誤解/逆張り点' },
  },
  required: ['findings'],
  additionalProperties: false,
}

const DRAFT_SCHEMA = {
  type: 'object',
  properties: {
    format: { type: 'string' },
    text: { type: 'string', description: '投稿本文のみ(日本語140字以内)' },
    char_count: { type: 'number' },
  },
  required: ['format', 'text'],
  additionalProperties: false,
}

const SCORE_SCHEMA = {
  type: 'object',
  properties: {
    score: { type: 'number', description: '0-100' },
    reason: { type: 'string' },
  },
  required: ['score'],
  additionalProperties: false,
}

const topic = (typeof args === 'string' && args.trim()) ? args.trim() : '（題材未指定）'

phase('Research')
const research = await parallel([
  () =>
    agent(
      `題材「${topic}」でXでバズる投稿のネタを調べる(角度: 具体的な事実/Tips)。WebSearchを使い、検証できる事実・数字を3〜6個、日本語で集める。`,
      { label: 'research:facts', phase: 'Research', schema: RESEARCH_SCHEMA }
    ),
  () =>
    agent(
      `題材「${topic}」について(角度: 今の伸び方・よくある誤解・逆張りできる点)を調べ、切り口を挙げる。`,
      { label: 'research:angles', phase: 'Research', schema: RESEARCH_SCHEMA }
    ),
])
const notes = research
  .filter(Boolean)
  .flatMap((r) => [...(r.findings || []), ...(r.angles || [])])
log(`調査メモ ${notes.length} 件`)

phase('Draft')
const FORMATS = ['リスト型(N選)', '逆張り型', 'ハウツー型']
const noteBlock = notes.map((n) => '- ' + n).join('\n')
const drafts = await parallel(
  FORMATS.map((fmt) => () =>
    agent(
      `次の調査メモを使い、題材「${topic}」のX投稿本文を「${fmt}」で1つ書く。\n` +
        `規範: 日本語140字以内・一文目で引き・ハッシュタグ控えめ・絵文字控えめ・価値を1つ残す。\n` +
        `調査メモ:\n${noteBlock}\n` +
        `出力 text は投稿本文のみ(前置き・記号枠なし)。char_count に字数。`,
      { label: `draft:${fmt}`, phase: 'Draft', schema: DRAFT_SCHEMA }
    )
  )
)
const cands = drafts.filter(Boolean)

phase('Score')
const scored = await parallel(
  cands.map((d) => () =>
    agent(
      `次のX投稿案を、フック強度・価値・140字遵守・規範適合で100点満点で辛口採点する。\n` +
        `案(${d.format} / ${d.char_count || '?'}字): ${d.text}`,
      { label: `score:${d.format}`, phase: 'Score', schema: SCORE_SCHEMA }
    ).then((s) => ({ ...d, score: s ? s.score : 0, reason: s ? s.reason : '' }))
  )
)
const ranked = scored.filter(Boolean).sort((a, b) => (b.score || 0) - (a.score || 0))
const winner = ranked[0] || null

return {
  topic,
  winner,
  candidates: ranked,
  createCommand: winner ? `xagent compose --raw ${JSON.stringify(winner.text)}` : null,
  note: 'winner.text を確認し createCommand を実行すると承認待ちの下書きが作られる。投稿は人間承認のみ。',
}
