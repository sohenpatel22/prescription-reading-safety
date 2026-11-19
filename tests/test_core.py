import json
import os

import pytest

from rxproto import din, dpd, evaluate, generate, llm, readers
from rxproto.sig import ismp_flags, parse_sig, same_meaning


def test_sig_parser_short_and_plain():
    a = parse_sig("1 tab PO BID")
    b = parse_sig("Take 1 tablet by mouth twice daily")
    assert same_meaning(a, b) and a["per_day"] == 2
    assert parse_sig("Take 2 puffs by inhalation four times daily as needed")["per_day"] == 4
    assert parse_sig("ii tabs PO TID")["dose"] == 2
    assert parse_sig("1/2 tab PO QHS")["dose"] == 0.5


def test_ismp_flags():
    assert ismp_flags("5.0 mg") and ismp_flags(".5 mg") and ismp_flags("1 tab PO QD")
    assert not ismp_flags("0.5 mg") and not ismp_flags("1 tab PO daily")


def test_din_matcher_uses_health_canada_list():
    m = din.match("Metformin", "300 mg")
    assert m.get("strength_not_marketed") and m["din"] == ""
    assert din.match("Metformin", "500 mg")["group"]  # product found; maker left open without history
    er = din.match("Metformin ER", "500 mg")
    ir = din.match("Metformin", "500 mg")
    assert json.loads(er["group"])[3] == "ER" and json.loads(ir["group"])[3] == "IR"
    assert din.match("Metformn", "500 mg")["base"] == "METFORMIN"  # spelling slip
    assert dpd.resolve_name("Lipitor")[0] == {"ATORVASTATIN"}


def test_maker_choice_order():
    base = din.match("Atorvastatin", "20 mg")
    gid, dins = base["group"], base["candidates"]
    assert base["din"] == "" and base["din_basis"] == "needs_choice"        # no history: no guess
    usual, last = dins[0], dins[1]
    m = din.match("Atorvastatin", "20 mg", history={gid: usual})
    assert m["din"] == usual and m["din_basis"] == "pharmacy_usual"
    m = din.match("Atorvastatin", "20 mg", history={gid: usual}, patient_dins={gid: last})
    assert m["din"] == last and m["din_basis"] == "patient_last_fill"       # patient beats pharmacy habit
    m = din.match("Lipitor", "20 mg", history={gid: usual}, patient_dins={gid: last}, no_sub=True)
    assert m["din_basis"] == "no_substitution" and m["din"] == "02230713"    # Lipitor's own DIN
    m = din.match("Atorvastatin", "20 mg", history={gid: usual}, no_sub=True)
    assert m["din"] == "" and m["din_basis"] == "needs_choice"              # generic + no sub: ask


def test_scorer_rules():
    rec = {"label": {"strength": "0.5 mg", "patient_dob": "1980-04-05", "drug_name": "Lipitor", "prescriber_license": "",
                     "sig": "1 tab PO BID"},
           "sig_struct": {"dose": 1.0, "unit": "tablet", "route": "PO", "per_day": 2, "prn": False}}
    assert evaluate.outcome("strength", ".5 mg", rec) == "correct"
    assert evaluate.outcome("strength", "5 mg", rec) == "wrong"
    assert evaluate.outcome("patient_dob", "05/04/1980", rec) == "correct"   # dd/mm
    assert evaluate.outcome("drug_name", "atorvastatin", rec) == "correct"
    assert evaluate.outcome("prescriber_license", "12345", rec) == "invented"
    assert evaluate.outcome("sig", "Take 1 tablet by mouth twice daily", rec) == "correct"
    assert evaluate.outcome("sig", "", rec) == "miss"


def test_end_to_end_dry_run(tmp_path, monkeypatch):
    """Whole pipeline on 6 synthetic prescriptions with the fake reader (tests code paths only)."""
    monkeypatch.setenv("RX_FAKE", "1")
    monkeypatch.setattr(llm, "CACHE", tmp_path / "cache")
    out = tmp_path / "synth"
    recs = generate.generate(n=6, out_dir=out, seed=7)
    hist = generate.load_history(out)
    from rxproto.fake import fake
    res = {"A": [], "B": []}
    for r in recs:
        img = out / "images" / f"{r['id']}.png"
        res["A"].append(readers.run_a(r, img, fake))
        res["B"].append(readers.run_b(r, img, hist, fake))
    rows = evaluate.score(recs, res)
    assert len(rows["A"]) == len(rows["B"]) == 6 * len(generate.FIELDS)
    assert all(x["color"] in ("green", "amber", "red") for x in rows["B"])
    monkeypatch.setattr(evaluate, "RES", tmp_path)
    p = evaluate.report(recs, res, rows, path=tmp_path / "report.md")
    assert "Dry run with the fake reader" in p.read_text(encoding="utf-8")


def test_triage_bands_and_cleanup(tmp_path):
    from PIL import Image
    from rxproto import triage
    recs = generate.generate(n=6, out_dir=tmp_path, seed=11)
    for r in recs:
        img = Image.open(tmp_path / "images" / f"{r['id']}.png")
        ch = triage.image_checks(img)
        assert ch["black_white"] == (r["style"] == "fax")
        tri = triage.decide(ch, {"written": "typed", "legibility_drug": 5, "problems": []})
        assert tri["band"] == ("Medium" if r["style"] == "fax" else "Easy")
        cleaned, steps = triage.clean(img, tri)
        assert cleaned.size == img.size and "contrast" in steps
    worse = triage.decide(ch, {"written": "handwritten", "legibility_drug": 1, "problems": ["smudge"]})
    assert worse["band"] == "Hard" and "smudge" in worse["reasons"]


def test_lanes_and_escalation(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_FAKE", "1")
    monkeypatch.setattr(llm, "CACHE", tmp_path / "cache")
    out = tmp_path / "synth"
    recs = generate.generate(n=9, out_dir=out, seed=3)
    hist = generate.load_history(out)
    from rxproto.fake import fake
    bands = set()
    for r in recs:
        res = readers.run_c(r, out / "images" / f"{r['id']}.png", hist, fake)
        assert res["setup"] == "C" and res["band"] in ("Easy", "Medium", "Hard")
        assert res["lane"] in ("easy", "medium", "hard")
        if res["escalated"]:
            assert res["lane"] == "medium"
        bands.add(res["band"])
    assert len(bands) >= 2


def test_rulebook_catches_impossible_strength():
    from rxproto import coverage
    assert 300.0 in coverage.strength_variants("500", swaps_only=True)
    assert 500.0 not in coverage.strength_variants("500")
    ok = coverage.marketed_mg("METFORMIN", "tablet", "IR")
    assert 500.0 in ok and 300.0 not in ok
