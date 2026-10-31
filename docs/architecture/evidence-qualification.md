# Evidence qualification gate

Bare prose-to-code matches are qualified, not merely collected. A token
match becomes a `text_mention` graph assertion only with an independent
corroborating signal; anything weaker is recorded as a
`mention_candidate` diagnostic. Candidates stay searchable through the
lexical stage and visible in `review`, but never traverse, never merge,
and never become evidence edges.

## Signals

- `exact_same_document`: the same document already references the entity
  with an exact `path::symbol` key. Identity is established, not guessed.
- `asserted_claim_same_document`: the document asserts a typed claim
  touching the entity, which corroborates identity as strongly as an
  exact reference.
- `dotted_or_call`: `recv.leaf` or `leaf(` syntax denotes the code
  entity, not a prose verb. Qualifies only low-frequency leaves.
- `code_span`: the leaf sits inside backticks. Recorded for review but
  never qualifies alone — prose backticks tool names too
  (e.g. `` `git status` `` is not the `git` helper).

## Qualification rule

Admit the assertion iff `exact_same_document`, or
`asserted_claim_same_document`, or (`document_frequency <= 0.05` and
`dotted_or_call`). Document frequency is corpus-relative over text
sources — never a fixed stopword list. Ambiguous matches that qualify
keep `proposed_ambiguous` status; the candidates stay distinct so
identities never silently merge.

## Refresh equivalence

Qualification inputs (global frequencies, cross-document exact refs,
claim refs) are computed over the full corpus in both full and
incremental paths, and a requalification flip-check reprocesses
documents whose kept mentions change verdicts. Refresh output stays
identical to a full rebuild; the randomized differential suite guards
this. Measured effect on 1,651 real documents: false-positive rate
1.0 → 0.0 on ordinary and rare matches with zero exact references
harmed; evidence assertions 4,852 → 4, candidates preserved.
