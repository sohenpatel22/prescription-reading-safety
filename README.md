# prescription-reading-safety

Reading prescriptions with a vision LLM is easy to demo and hard to trust. This repo compares three ways of doing it
on the same labelled test set:

- **A (baseline):** one Gemini call returns every field; the DIN comes from a plain name + strength lookup.
- **B (checked):** the same model, wrapped in checks. Critical fields are read twice, arithmetic is done in code,
  the product is matched against Health Canada's Drug Product Database, rule checks run, and only disputed or
  failing fields go to a stronger model. Every field ends up green, amber or red, with a reason.
- **C (triaged):** first sort the prescription into Easy, Medium or Hard (image checks + a quick look by a small
  model), clean the image based on what was found, then use a lane per band: Easy gets one read, Medium a second
  read of critical fields, Hard two reads plus the stronger model on every critical field. An Easy prescription that
  fails a critical check moves up a lane.

All three are scored field by field with the same rules, and the report shows where each one goes wrong.

## First run (30 prescriptions)

Gemini 3.5 Flash for the reads, Gemini 3.1 Pro as the stronger model, 3.5 Flash-Lite for triage. Full report:
[docs/first_run_30rx.md](docs/first_run_30rx.md).

| | A | B | C |
|---|---|---|---|
| Fields read right | 97.7% | 97.7% | 97.9% |
| Critical fields read right | 99.3% | 99.3% | 99.3% |
| Right product pre-selected | 92.9% | 96.2% | 96.2% |
| Patient's usual maker kept | 0 / 7 | 5 / 7 | 5 / 7 |
| Planted safety issues flagged | no flags | 100% | 100% |
| Errors among green fields | - | 1.1% | 1.7% |
| Cost per prescription | ~1.2¢ | ~2.4¢ | ~2.4¢ |

On clean synthetic images the three read about equally well; the gains are in product matching and flagging.
B and C each got one strength wrong that A got right, which is what the colours and the review step are for.

## Test set

150 synthetic Canadian prescriptions (50 printed, 50 handwritten, 50 faxed) with exact labels for 13 fields:
drug, strength, form, quantity, total authorised, days supply, directions, patient name and date of birth,
prescriber name and licence. Drugs and strengths come from the DPD; patients, prescribers and clinics are invented.

Some prescriptions carry traps that a careful pharmacist would catch: brand names, look-alike drugs, smudged
strengths, `.5 mg`, `5.0 mg`, `QD`, strengths that are not sold in Canada, days supply that does not add up,
dates that can be read two ways, missing fields, "No substitution", and refills from a different maker.

No real patient data is used anywhere.

## How B picks the product (DIN)

1. **Product group** (the safety part): ingredient (generic or brand), strength in mg, form and release type
   (regular vs ER). Combination products are excluded. If nothing matches, the field turns red.
2. **Maker within the group:** "No substitution" with a brand wins; then the patient's last fill; then the
   pharmacy's usual product; otherwise nothing is pre-selected and the pharmacist picks.

Patient and pharmacy histories are simulated in `data/synth/`.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env        # add GEMINI_API_KEY, or OPENROUTER_API_KEY
python -m rxproto models    # check which model names your key can use
python -m rxproto run --setup all --limit 10    # small trial first
python -m rxproto run --setup all
python -m rxproto score     # writes results/report.md
python -m rxproto review    # writes results/review.html
python -m rxproto coverage  # what the rule book catches with no model at all (free)
```

Model answers are cached in `cache/`, so a run can be stopped and resumed without paying twice. `RX_MAX_USD`
stops a run once it has spent that much. To use Vertex AI
instead of an API key, set the `GOOGLE_*` variables in `.env` and run `gcloud auth application-default login`.

Without a key, `RX_FAKE=1` runs the whole pipeline with a fake reader (useful for testing, not for results).

Rebuild the data from scratch:

```bash
python -m rxproto build-dpd       # downloads the DPD extract if missing
python -m rxproto generate --n 150
```

## What the report contains

- Field accuracy for each set-up with 95% bootstrap intervals, by prescription type, plus paired differences
- Wrong values, blanks and invented values counted separately
- DIN results: whether the pre-selected product is safe, and whether the right maker was chosen in each situation
- For B and C: how many reading errors and planted issues the flags catch, false alarms, the error rate among
  green fields, and the quality of the confidence score (out-of-fold AUROC and calibration)
- For C: results per band (Easy, Medium, Hard), how many moved up a lane, and model calls per prescription
- Model calls, tokens and time per prescription

## Layout

```
rxproto/
  dpd.py        Health Canada Drug Product Database: download, load, look up
  triage.py     Easy / Medium / Hard: image checks, quick model look, band, clean-up
  coverage.py   misreads the rule book catches on its own
  generate.py   synthetic prescriptions, traps and histories
  sig.py        write and parse directions; ISMP "do not use" checks
  din.py        product matching
  llm.py        Gemini / OpenRouter client with cache, retries and a spending cap
  readers.py    set-ups A, B and C
  evaluate.py   scoring, confidence model, report
  fake.py       fake reader for dry runs
tests/
```

## Limits

- Synthetic images are cleaner than real faxes and real handwriting, so absolute accuracy here will be higher
  than in practice. The comparison between the set-ups is the useful part.
- The confidence model is fitted on 150 prescriptions with 5-fold cross-fitting. It shows the method, not a
  validated model.
- Histories for DIN selection are simulated.
- Triage infers how a prescription arrived from the image; in production that comes from the intake system.
  The band grid is a starting point; with labelled data its weights would be learned.

## Data source

Contains information licensed under the Open Government Licence - Canada. Source: Health Canada, Drug Product Database.

## Tests

```bash
python -m pytest -q
```
