"""Fake reader for dry runs: copies the label and injects reading errors."""
import random
import re

from .generate import FIELDS

ERR = {"printed": 0.01, "handwritten": 0.06, "fax": 0.05}


def _mangle(v, rng):
    if not v:
        return v
    digits = [i for i, c in enumerate(v) if c.isdigit()]
    if digits and rng.random() < 0.7:
        i = rng.choice(digits)
        return v[:i] + rng.choice("0123456789".replace(v[i], "")) + v[i + 1:]
    letters = [i for i, c in enumerate(v) if c.isalpha()]
    if letters:
        i = rng.choice(letters)
        return v[:i] + rng.choice("aeiourstn") + v[i + 1:]
    return v


def fake(kind, rec, fields=None):
    rng = random.Random(f"{rec['id']}|{kind}")
    lab, pg = rec["label"], rec["page"]
    p = ERR[rec["style"]] * (0.5 if kind == "B3" else 1.0)

    def read(field, value):
        q = p * (8 if (field == "strength" and "smudge_strength" in rec["traps"]) else 1)
        return _mangle(value, rng) if rng.random() < q else value

    raw = dict(drug_name=pg["drug_text"], strength=pg["strength_text"], qty=str(lab["qty"]), refills=str(rec["refills"]),
               sig=pg["sig_text"], patient_dob_as_written=pg["dob_text"], prescriber_license=lab["prescriber_license"])
    if kind == "T":
        smudged = "smudge_strength" in rec["traps"]
        return dict(written="typed" if rec["style"] == "printed" else "mixed",
                    legibility_drug=2 if smudged else 4, problems=["smudge"] if smudged else [])
    if kind == "A":
        out = {f: read(f, lab[f]) for f in FIELDS}
        if not rec["ds_written"] and rng.random() < 0.5:
            out["ds"] = ""
        if rec["refills"] and rng.random() < 0.3:
            out["qty_auth"] = out["qty"]
        if "ambiguous_dob" in rec["traps"] and rng.random() < 0.5:
            y, m, d = lab["patient_dob"].split("-")
            out["patient_dob"] = f"{y}-{d}-{m}"
        out["strength"] = re.sub(r"^\.", "0.", out["strength"])
        return out
    if kind == "B1":
        out = {f: read(f, lab[f]) for f in FIELDS}
        out.update({k: read(k, v) for k, v in raw.items()})
        out.update(doc_type=rec["style"], form="", no_substitution="yes" if pg.get("nosub_text") else "no", ds_written=str(lab["ds"]) if rec["ds_written"] else "",
                   uncertain_fields=["strength"] if "smudge_strength" in rec["traps"] and rng.random() < 0.6 else [])
        return out
    if kind == "B2":
        return {k: read(k, v) for k, v in raw.items()}
    if kind == "B3":
        return {f: read(f, raw.get(f, lab.get(f, ""))) for f in fields}
    raise ValueError(kind)
