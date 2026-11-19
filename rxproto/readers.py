"""Readers for set-ups A, B and C."""
import io
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import List

from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel

from . import din, llm, triage
from .generate import FIELDS
from .sig import ismp_flags, parse_sig

ROOT = Path(__file__).resolve().parents[1]


# ---------------- schemas ----------------
class RxA(BaseModel):
    drug_name: str
    strength: str
    form: str
    qty: str
    qty_auth: str
    ds: str
    sig: str
    patient_first: str
    patient_last: str
    patient_dob: str
    prescriber_first: str
    prescriber_last: str
    prescriber_license: str


class RxB1(BaseModel):
    doc_type: str
    drug_name: str
    strength: str
    form: str
    qty: str
    refills: str
    ds_written: str
    sig: str
    patient_first: str
    patient_last: str
    patient_dob_as_written: str
    patient_dob: str
    prescriber_first: str
    prescriber_last: str
    prescriber_license: str
    no_substitution: str
    uncertain_fields: List[str]


class RxB2(BaseModel):
    drug_name: str
    strength: str
    qty: str
    refills: str
    sig: str
    patient_dob_as_written: str
    prescriber_license: str


PROMPT_A = """Read this pharmacy prescription and return the fields as JSON:
drug_name, strength, form, qty (quantity to dispense), qty_auth (total quantity authorized including refills),
ds (days supply), sig (directions), patient_first, patient_last, patient_dob (YYYY-MM-DD), prescriber_first,
prescriber_last, prescriber_license. Use an empty string if a field is not present."""

PROMPT_B1 = """You are transcribing a Canadian pharmacy prescription (it may be printed, handwritten or faxed).
Copy what is written. Do not correct, complete or guess. If a field is not on the page, return "".
- doc_type: "printed", "handwritten" or "fax".
- drug_name: exactly as written, including words like ER, XR, CR (brand or generic).
- strength: exactly as written, with its unit (for example ".5 mg" stays ".5 mg", "5.0 mg" stays "5.0 mg").
- form: tablet, capsule, inhaler, etc. only if written; else "".
- qty: the quantity to dispense as a number. It may be labelled Mitte, M, Disp, Qty or #. Quantities may also be in words.
- refills: number of refills/repeats (Rpt, Repeats, Refills, "x2"). "NR" or "No refills" means 0. Refills are NOT the quantity.
- ds_written: days supply ONLY if it is explicitly written on the page; otherwise "". Do not calculate it.
- sig: the directions exactly as written, keeping abbreviations (for example "1 tab PO BID").
- patient_dob_as_written: the date of birth exactly as written. patient_dob: the same date as YYYY-MM-DD; if
  it is written like 05/04/1980, read it as day/month/year (Canadian usage).
- prescriber_license: digits only.
- no_substitution: "yes" if the prescriber wrote "No substitution", "No sub", "Do not substitute" or similar; else "no".
- uncertain_fields: names of any fields above that are hard to read, smudged, ambiguous or that you are not sure about."""

PROMPT_B2 = """Independent check of a prescription. Read each item slowly, character by character and digit by digit.
Return exactly what is written (keep abbreviations, decimals and units as written). Use "" if absent.
drug_name, strength (with unit), qty (quantity to dispense, digits), refills (digits; NR/No refills = 0),
sig (directions as written), patient_dob_as_written, prescriber_license (digits)."""


def prompt_strong(fields):
    return ("A prescription needs a careful second look at these fields only: " + ", ".join(fields) + ".\n"
            "Zoom in mentally and read them exactly as written (keep decimals, units and abbreviations). "
            "qty is the quantity to dispense; refills are separate. Use \"\" if a field is absent or truly unreadable.")


def strong_schema(fields):
    from pydantic import create_model
    return create_model("Strong_" + "_".join(sorted(fields)), **{f: (str, ...) for f in fields})


# ---------------- helpers ----------------
def png_bytes(img):
    b = io.BytesIO()
    img.save(b, format="PNG", optimize=True)
    return b.getvalue()


def clean_image(img):
    g = img.convert("L")
    colors = g.getcolors(256)
    if colors is not None and len(colors) <= 4:  # pure black/white (fax-like): remove speckles
        g = g.filter(ImageFilter.MedianFilter(3))
    return ImageOps.autocontrast(g, cutoff=1)


def first_int(s):
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else None


def norm_text(s):
    return re.sub(r"[^a-z0-9.]", "", str(s or "").lower())


def same_value(field, a, b):
    if field in ("qty", "refills", "prescriber_license"):
        return first_int(a) == first_int(b) and (first_int(a) is not None or norm_text(a) == norm_text(b))
    if field == "strength":
        return din.strength_mg(a) == din.strength_mg(b)
    if field == "sig":
        pa, pb = parse_sig(a), parse_sig(b)
        return (pa == pb) if (pa or pb) else norm_text(a) == norm_text(b)
    if field == "drug_name":
        ra, rb = din.resolve_fuzzy(a)[:2], din.resolve_fuzzy(b)[:2]
        return (ra == rb and bool(ra[0])) or norm_text(a) == norm_text(b)
    return norm_text(a) == norm_text(b)


def dob_from_written(s):
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return date(y, mo, d).isoformat(), (d <= 12 and mo <= 12 and d != mo)
        except ValueError:
            return "", False
    return None, False


# ---------------- set-up A ----------------
def run_a(rec, img_path, fake=None):
    fast, _ = llm.model_names()
    img = Image.open(img_path)
    ans = llm.ask_image(fast, png_bytes(img), PROMPT_A, RxA, "A", fake_answer=fake and (lambda: fake("A", rec)))
    fields = {f: str(ans.get(f, "") or "") for f in FIELDS}
    return dict(id=rec["id"], setup="A", fields=fields, din=din.simple_lookup(fields["drug_name"], fields["strength"]),
                flags={})


# ---------------- set-up B ----------------
CRITICAL_RECHECK = ["drug_name", "strength", "qty", "refills", "sig", "patient_dob_as_written", "prescriber_license"]


CORE = ("drug_name", "strength", "qty", "sig")


def run_b(rec, img_path, history, fake=None, mode="full", img=None):
    """Set-up B (mode="full") or one lane of set-up C ("easy", "medium", "hard")."""
    fast, strong = llm.model_names()
    img = img if img is not None else clean_image(Image.open(img_path))
    r1 = llm.ask_image(fast, png_bytes(img), PROMPT_B1, RxB1, "B1", fake_answer=fake and (lambda: fake("B1", rec)))
    vals = {k: str(r1.get(k, "") or "") for k in RxB1.model_fields if k != "uncertain_fields"}
    uncertain = set(r1.get("uncertain_fields") or [])
    pre = _rules(vals, history)
    if mode == "easy":
        failing = [f for f in CORE if pre["red"].get(f)] + [f for f in CORE if f in uncertain]
        if failing:
            return {"escalate": sorted(set(failing))}
        r2, disputed = {}, []
    else:
        big = img.resize((int(img.width * 1.5), int(img.height * 1.5)), Image.LANCZOS)
        r2 = llm.ask_image(fast, png_bytes(big), PROMPT_B2, RxB2, "B2", fake_answer=fake and (lambda: fake("B2", rec)))
        disputed = [f for f in CRITICAL_RECHECK if not same_value(f, vals.get(f, ""), r2.get(f, ""))]

    # rule checks on the first read decide which fields also get the stronger model
    need = set(disputed) | {f for f in CORE if pre["red"].get(f)}
    if mode == "hard":
        need |= {"drug_name", "strength", "qty", "refills", "sig"}
    need = sorted(need)
    strong_used = {}
    if need:
        sch = strong_schema(need)
        r3 = llm.ask_image(strong, png_bytes(img), prompt_strong(need), sch, "B3",
                           fake_answer=fake and (lambda: fake("B3", rec, need)))
        for f in need:
            a, b, c = vals.get(f, ""), str(r2.get(f, "") or ""), str(r3.get(f, "") or "")
            if f in disputed:
                if same_value(f, c, a):
                    strong_used[f] = "agreed with read 1"
                elif same_value(f, c, b):
                    vals[f] = b
                    strong_used[f] = "agreed with read 2; value changed"
                else:
                    strong_used[f] = "three reads disagree"
            else:
                strong_used[f] = "confirmed" if same_value(f, c, a) else "stronger model read it differently"
    if "patient_dob_as_written" in disputed and strong_used.get("patient_dob_as_written") == "agreed with read 2; value changed":
        vals["patient_dob"] = dob_from_written(vals["patient_dob_as_written"])[0] or vals["patient_dob"]

    post = _rules(vals, history)
    fields, m = _derive(vals, post, history)
    flags = {}
    for f in FIELDS:
        # disputes travel to fields computed from them; rule messages stay on their own field
        src = {"qty_auth": ["qty", "refills"], "ds": ["ds", "qty", "sig"], "patient_dob": ["patient_dob", "patient_dob_as_written"]}.get(f, [f])
        own = {"patient_dob": ["patient_dob", "patient_dob_as_written"]}.get(f, [f])
        red = [r for s in own for r in post["red"].get(s, [])]
        amber = [r for s in own for r in post["amber"].get(s, [])]
        if f in ("qty_auth", "ds") and post["red"].get("qty"):
            red.append("quantity not found")
        for s in src:
            how = strong_used.get(s)
            if how in ("three reads disagree", "stronger model read it differently"):
                red.append(f"reads disagree ({how})")
            elif how == "agreed with read 2; value changed":
                amber.append("first read was corrected by a second look")
            elif s in disputed:
                amber.append("two reads disagreed; resolved by a third read")
        if any(u in uncertain for u in src):
            amber.append("model marked this field as hard to read")
        flags[f] = dict(red=sorted(set(red)), amber=sorted(set(amber)),
                        signals=dict(disputed=any(s in disputed for s in src), strong=any(s in strong_used for s in src),
                                     unresolved=any(strong_used.get(s) in ("three reads disagree", "stronger model read it differently") for s in src),
                                     uncertain=any(u in uncertain for u in src), doc_type=vals.get("doc_type", "")))
    return dict(id=rec["id"], setup="B", mode=mode, fields=fields, din=post["match"]["din"], din_candidates=post["match"]["candidates"],
                din_basis=post["match"]["din_basis"], din_note=post["match"].get("note", ""),
                flags=flags, raw=dict(read1=r1, read2=r2, strong=strong_used, disputed=disputed))


# ---------------- set-up C: triage + three lanes ----------------
def run_c(rec, img_path, history, fake=None, channel=None):
    raw = Image.open(img_path)
    tri = triage.triage(rec, raw, fake, channel)
    img, steps = triage.clean(raw, tri)
    lane = tri["band"].lower()
    res = run_b(rec, img_path, history, fake, mode=lane, img=img)
    escalated = []
    if "escalate" in res:
        escalated = res["escalate"]
        lane = "medium"
        res = run_b(rec, img_path, history, fake, mode=lane, img=img)
    for f in res["flags"].values():
        f["signals"]["band"] = tri["band"]
    res.update(setup="C", band=tri["band"], lane=lane, escalated=escalated, cleanup=steps,
               triage=dict(reasons=tri["reasons"], checks=tri["checks"], written=tri["written"], arrived=tri["arrived"]))
    if tri["band"] == "Hard":
        res["band_note"] = "Hard: " + ", ".join(tri["reasons"]) + ". Please take the lead."
    return res


def _rules(v, history):
    red, amber = {}, {}
    add = lambda d, f, msg: d.setdefault(f, []).append(msg)
    for f in ("drug_name", "strength", "qty", "sig"):
        if not v.get(f):
            add(red, f, "not found on the page")
    from .generate import patient_key
    pkey = patient_key(v.get("patient_first", ""), v.get("patient_last", ""), v.get("patient_dob", ""))
    m = din.match(v.get("drug_name", ""), v.get("strength", ""), _form_hint(v), history.get("pharmacy"),
                  history.get("patients", {}).get(pkey), no_sub=str(v.get("no_substitution", "")).lower().startswith("y"))
    for r in m["reasons"]:
        if "not found" in r or "not a marketed strength" in r or "strength not readable" in r:
            add(red, "strength" if "strength" in r else "drug_name", r)
        elif "looks like" in r or "approximately" in r:
            add(amber, "drug_name", r)
    if m.get("controlled"):
        add(amber, "drug_name", "controlled substance: check the special rules")
    for msg in ismp_flags(v.get("strength", "")):
        add(red, "strength", msg)
    for msg in ismp_flags(v.get("sig", "")):
        add(amber, "sig", msg)
    parsed = parse_sig(v.get("sig", ""))
    if v.get("sig") and parsed is None:
        add(amber, "sig", "directions could not be fully parsed (dose or frequency)")
    qty, dsw = first_int(v.get("qty")), first_int(v.get("ds_written"))
    calc = _calc_ds(qty, parsed)
    if dsw is not None and calc is not None and abs(dsw - calc) > 1:
        add(amber, "ds", f"days supply written ({dsw}) does not match quantity / daily use ({calc})")
    if dsw is None and calc is None:
        add(amber, "ds", "days supply not written and cannot be calculated (as-needed or non-tablet)")
    dob_iso, ambiguous = dob_from_written(v.get("patient_dob_as_written", ""))
    if ambiguous:
        add(amber, "patient_dob", "date could be day/month or month/day")
    if v.get("patient_dob"):
        try:
            y = int(v["patient_dob"][:4])
            if y < 1905 or y > 2026:
                add(red, "patient_dob", "date of birth out of range")
        except ValueError:
            add(amber, "patient_dob", "date of birth not in YYYY-MM-DD form")
    lic = v.get("prescriber_license", "")
    if lic and not re.fullmatch(r"\d{4,6}", re.sub(r"\D", "", lic) or "x"):
        add(amber, "prescriber_license", "licence number has an unusual length")
    return dict(red=red, amber=amber, match=m)


def _form_hint(v):
    f = (v.get("form") or "").lower()
    if f:
        from .dpd import form_family
        return form_family(f)
    p = parse_sig(v.get("sig", ""))
    return {"tablet": "tablet", "capsule": "capsule", "puff": "inhaler"}.get(p["unit"], "") if p else ""


def _calc_ds(qty, parsed):
    if not qty or not parsed or parsed["prn"] or parsed["unit"] == "puff":
        return None
    daily = parsed["dose"] * parsed["per_day"]
    return int(round(qty / daily)) if daily else None


def _derive(v, rules, history):
    """Arithmetic is done in code, not by the model."""
    qty, refills = first_int(v.get("qty")), first_int(v.get("refills"))
    parsed = parse_sig(v.get("sig", ""))
    dsw = first_int(v.get("ds_written"))
    ds = dsw if dsw is not None else _calc_ds(qty, parsed)
    form = _form_hint(v) or rules["match"].get("form", "")
    fields = {
        "drug_name": v.get("drug_name", ""), "strength": v.get("strength", ""), "form": form,
        "qty": str(qty) if qty is not None else "", "qty_auth": str(qty * ((refills or 0) + 1)) if qty is not None else "",
        "ds": str(ds) if ds is not None else "", "sig": v.get("sig", ""),
        "patient_first": v.get("patient_first", ""), "patient_last": v.get("patient_last", ""),
        "patient_dob": v.get("patient_dob", ""), "prescriber_first": v.get("prescriber_first", ""),
        "prescriber_last": v.get("prescriber_last", ""), "prescriber_license": re.sub(r"\D", "", v.get("prescriber_license", "")),
    }
    return fields, rules
