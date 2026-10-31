# Frozen mention-precision benchmark (MochiOS v1)

A tool-neutral scoreboard for prose→code mention precision. The official
Tunic suites (`w1`, `memory`, `agent`) are untouched; this package freezes
its own corpus, labels, and protocol so Tunic's evolution — and any outside
tool run against the same frozen inputs — can be compared number-for-number.

## What is frozen

- `mochios-sample-v1.json` — 48 real MochiOS document occurrences with
  line contexts, strata (`ordinary` × 32, `qualified` × 8, `rare` × 8),
  and first-pass labels (`refers` / `not-a-mention` / `ambiguous` with
  notes). Labels are Muse first-pass; user adjudication pending.
- `mochios-corpus-v1.manifest.json` — sha256 of every corpus file at
  sampling time. Any rerun verifies the corpus against it; drift voids
  comparability and must be recorded, not silently absorbed.

## The task (tool-neutral)

For each sample `(doc, line_no)`, output zero or more entity links or
abstain. A link counts as a hit when it targets the sampled leaf's entity
(or any entity on that line for `qualified` samples with no leaf).

## Scoring

- Ordinary/rare hits on `not-a-mention` labels: false positives.
- Qualified samples: correct abstentions counted; hits on `not-a-mention`: FP.
- `ambiguous` hits: reported, excluded from rates. `refers` hits: true
  positives (this split currently has zero refers labels, so it measures
  false-positive rate and abstention — not recall).
- Mention volume (total assertions, distinct callees, top callees) is
  recorded alongside rates: a tool can score 0 FP by emitting nothing,
  so volume-per-recall matters when refers samples exist.

## Scoreboard policy

`scoreboard.json` holds one row per tool with the measured numbers and the
exact corpus manifest digest. A row is added only for a tool that was
actually executed against the frozen inputs — never by reading docs,
marketing, or analogy. Tools that do not perform prose→entity linking
(code-structure mappers, managed chat APIs without a local path) are
listed under `out_of_scope` with the reason, not scored as zero.

## Reproducing Tunic's row

The scorer fresh-ingests the corpus into a scratch database (never the
repo under test) and maps samples by byte-accurate line offsets:

```
PYTHONPATH=Z:\tunic python <scratch>/frozen_precision.py
```

which rewrites the manifest and `runs/mochios-v1-tunic.json`.

## PyQuifer positive-link split (v1)

`pyquifer-links-v1.json` is the recall counterpart to the MochiOS
negatives: 63 real PyQuifer document occurrences with line contexts,
split into dev (42) and held-out (21). Labels are `refers`
(expected entity in `targets`), `not-a-mention` (a hit is a false
positive), and `ambiguous` (hits reported, excluded from rates).
Labels are Muse first-pass; user adjudication pending. The held-out
21 are never tuned against and never scored during development.

`pyquifer-corpus-v1.manifest.json` freezes the live-checkout digests;
the runner drift-checks it on every run. Matching is by line number,
not byte offset: sampled offsets drifted in the live checkout (P001
25504 → 25731, same line 550) before any manifest existed, so
offsets are recorded for provenance but matching uses `(doc,
line_no)`. Current dev row (`runs/pyquifer-dev-v2-tunic.json`):
`refers` recall 0.80 (8/10), `not-a-mention` FP 0/28.
