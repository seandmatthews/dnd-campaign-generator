# verification

A linting-and-adversarial-review kit for generated D&D campaign prose. It
does not judge quality; it enforces three cheaper things — taste rules you
wrote down, rhythm statistics, and attackers with narrow mandates — and
hands the human a review queue.

Sibling projects: `../agentic-simulator` (the faction sandbox whose
chronicles this can check) and `../dnd-mapping` (worlds, cities,
battlemaps). All three live under the `dnd-campaign-generator` parent
directory.

## The ladder

Cheapest layer first; each sees only what survives the last.

1. **Structural** (`check.py`, zero dependencies): sentence-length spikes,
   uniform rhythm (the slop beat), adverb fog, paragraph-final epigram
   cadence, and near-miss canon names ("Ruravaquan" for "Ruravaqan").
   Table rows and headings are data, not prose — skipped.
2. **Taste** (Vale, optional): the lexicon is the law. `styles/Tabletop/`
   carries the em-dash cap (one per paragraph), semicolon cap (two), the
   fantasy-mean ban list (`Regression.yml` — every caught slop instance
   becomes one line here), and the "not just X but Y" construction.
   Install vale (`winget install Vale.Vale` or from
   <https://vale.sh>); without it the ladder reports the skip and moves on.
3. **Adversarial** (`prompts/`, manual or `--llm`): four attackers, each
   with a narrow mandate and a JSON findings contract —
   - `canon-audit.md` — every claim not derivable from the world state
   - `discriminator.md` — can a model tell this world's prose from generic
     fantasy? If not, it is slop by operational definition (run via
     `--discriminate CANON DECOY1 DECOY2`)
   - `compression.md` — which paragraphs change nothing at the table if
     deleted
   - `specificity.md` — rewrite sentences that could appear in any
     campaign so they belong to this one

   The prompts ask the model only for `{rule, quote, note, severity}`;
   `check.py` decorates each finding into the full schema shape (`file`,
   `line` located from the quote, `level` from severity) before it enters
   the queue.

Findings land in `--out` (default `findings/`) as `findings.json` plus a
human-readable `queue.md`. Nothing blocks; the DM decides.

## Usage

```bash
python tests/test_check.py                                  # the kit tests itself
python check.py ../campaign-data/chronicle/0004-*.md         # structural layer only
python check.py draft.md --state ../campaign-data/engine-home/state.yaml --llm
python check.py --discriminate canon.md generic-one.md generic-two.md         --state ../campaign-data/engine-home/state.yaml
```

## Growing the kit

- Every slop instance you catch by hand becomes one line in
  `styles/Tabletop/Regression.yml` — that is the whole maintenance model.
- New canon terms go in `lexicons/canon-terms.txt` (exact spelling; near
  variants get flagged).
- The prompts are attack contracts: change their mandates, not their
  output shape. Reviewers that return prose judgments regress to median
  praise; reviewers that return findings arrays cannot flatter you.
