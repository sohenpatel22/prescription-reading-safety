# Prescription reading: set-ups A, B and C

Synthetic test set: 30 prescriptions (printed: 10, handwritten: 8, fax: 12), 13 fields each. Drugs and strengths from Health Canada's Drug Product Database; people and clinics invented.

A: a typical single-pass design (one model call, simple product lookup). B: every prescription fully checked. C: triage into Easy / Medium / Hard, then a lane per band.
Synthetic images are easier than real faxes, so treat these numbers as a comparison, not an accuracy estimate.

## 1. Reading accuracy (95% CI by bootstrap over prescriptions)

| Measure | Set-up A | Set-up B | Set-up C |
|---|---|---|---|
| All fields | 97.7% (96.4% to 99.0%) | 97.7% (96.2% to 99.0%) | 97.9% (96.4% to 99.2%) |
| Critical fields (drug, strength, qty, days supply, sig) | 99.3% (98.0% to 100.0%) | 99.3% (98.0% to 100.0%) | 99.3% (98.0% to 100.0%) |
| All fields, printed | 98.5% (96.2% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |
| All fields, handwritten | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |
| All fields, fax | 95.5% (93.3% to 97.7%) | 94.2% (91.5% to 96.8%) | 94.9% (92.3% to 97.6%) |
| Prescriptions with every field right | 70.0% (53.3% to 86.7%) | 73.3% (56.7% to 90.0%) | 76.7% (60.0% to 90.0%) |

Paired difference B minus A, all fields: **+0.0 points** (95% CI -1.0 to +1.0); critical fields: **+0.0 points** (-2.0 to +2.0).  
Paired difference C minus A, all fields: **+0.3 points** (95% CI -1.0 to +1.5); critical fields: **+0.0 points** (-2.0 to +2.0).  
Paired difference C minus B, all fields: **+0.3 points** (95% CI -0.5 to +1.3); critical fields: **+0.0 points** (+0.0 to +0.0).  

## 2. Accuracy by field

| Field | A | B | C |
|---|---|---|---|
| drug_name | 100.0% | 100.0% | 100.0% |
| strength | 100.0% | 96.7% | 96.7% |
| form | 100.0% | 100.0% | 100.0% |
| qty | 100.0% | 100.0% | 100.0% |
| qty_auth | 100.0% | 100.0% | 100.0% |
| ds | 96.7% | 100.0% | 100.0% |
| sig | 100.0% | 100.0% | 100.0% |
| patient_first | 100.0% | 100.0% | 100.0% |
| patient_last | 100.0% | 100.0% | 96.7% |
| patient_dob | 86.7% | 93.3% | 93.3% |
| prescriber_first | 100.0% | 100.0% | 100.0% |
| prescriber_last | 100.0% | 100.0% | 100.0% |
| prescriber_license | 86.7% | 80.0% | 86.7% |

## 3. Kinds of error (counts over all fields)

| Outcome | A | B | C |
|---|---|---|---|
| Wrong value | 9 | 9 | 8 |
| Left blank, but on the page | 0 | 0 | 0 |
| Filled in, but not on the page | 0 | 0 | 0 |
| Wrong or invented drug / strength (most dangerous) | 0 | 1 | 1 |

## 4. Product (DIN) pre-selection

| Measure | A | B | C |
|---|---|---|---|
| **Safety:** pre-selected DIN is the right product (ingredient, strength, form, release) | 92.9% (82.1% to 100.0%) of 28 picks | 96.2% (88.5% to 100.0%) of 26 picks | 96.2% (88.5% to 100.0%) of 26 picks |
| 'No substitution' on a brand: that brand pre-selected | 4/4 | 4/4 | 4/4 |
| Patient had it before: same maker pre-selected | 0/7 | 5/7 | 5/7 |
| Pharmacy's usual product pre-selected | 2/15 | 15/15 | 15/15 |
| No history at all: nothing pre-selected (pharmacist picks) | 0/3 | 3/3 | 3/3 |
| Strength not marketed: nothing pre-selected | 1/1 | 1/1 | 1/1 |
| ...of which the patient's last maker differs from the pharmacy's usual | 0/6 | 4/6 | 4/6 |

Set-up A always takes the first product its lookup finds, so it cannot follow 'No substitution', a patient's last fill or the pharmacy's habit, and it guesses when there is no history.

## 5. Risk highlights (set-up A has none, like today)

A field **should be flagged** if it was read wrong **or** carries a planted safety issue (ISMP abbreviation, strength not marketed, days-supply mismatch, ambiguous date, controlled drug). Confidence scores are out-of-fold (5-fold, by prescription). Thresholds: amber at 15%, red at 50% chance of error.

| Measure | Set-up B | Set-up C |
|---|---|---|
| Share of fields flagged (amber or red) | 6.9% | 6.9% |
| Should-flag fields caught | 78.9% of 19 | 66.7% of 18 |
| ...with the rules alone (no learned score) | 57.9% | 61.1% |
| Reading errors caught | 55.6% | 25.0% |
| Planted safety issues caught | 100.0% | 100.0% |
| Fine fields flagged anyway (false alarms) | 3.2% | 4.0% |
| **Error rate among green fields** (green means green) | 1.1% (4 of 363) | 1.7% (6 of 363) |
| Confidence score AUROC / calibration error | 0.95 / 0.018 | 0.92 / 0.006 |

## 5b. Lanes (set-up C)

| Band | Prescriptions | Moved up a lane | Model calls per Rx | Field accuracy | Critical-field accuracy | Error rate among green fields |
|---|---|---|---|---|---|---|
| Easy | 10 | 1 | 2.30 | 100.0% | 100.0% | 0.0% |
| Medium | 11 | 0 | 3.36 | 95.8% | 98.2% | 3.0% |
| Hard | 9 | 0 | 4.00 | 98.3% | 100.0% | 1.9% |

Set-up B gives every prescription the full checks; set-up C spends them by band. Compare the accuracy rows in section 1 with the calls and tokens in section 6.

## 6. Cost and time per prescription

| Measure | A | B | C |
|---|---|---|---|
| Model calls | 1.00 | 2.17 | 3.20 |
| Model time (seconds, sum of calls) | 6.4 | 12.1 | 14.3 |
| Tokens in / out / thinking | gemini-3.5-flash: 1,534 / 140 / 957 | gemini-3.5-flash: 3,362 / 269 / 1,777<br>gemini-3.1-pro-preview: 203 / 2 / 26 | gemini-3.5-flash: 3,009 / 254 / 1,486<br>gemini-3.1-pro-preview: 597 / 15 / 161<br>gemini-3.5-flash-lite: 1,350 / 35 / 0 |
| Estimated US$ (needs RX_PRICE_* in .env) | set prices | set prices | set prices |

## 7. How to read this

- The test set is synthetic: printed, handwritten-font and fax-degraded images with planted traps. Real faxes and real handwriting are harder.
- Set-up A stands for a typical first version of a prescription reader.
- The scorer, flags and DIN matcher are meant to run unchanged on a real labelled set.
