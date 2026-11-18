"""Drug-to-DIN matching.

simple_lookup: what a quick first version often does (name + strength string match, first hit).
match: the proposed matcher (name/brand resolution with fuzzy fallback, release type, single-ingredient
products only, strength in mg, form family, then the pharmacy's usual DIN from past selections).
"""
import json
import re

from rapidfuzz import fuzz, process

from . import dpd


def parse_strength(text):
    """'500 mg' / '.5 mg' / '5.0mg' / '0.1 mg' / '100 mcg' -> (value, unit) or None."""
    if not text:
        return None
    m = re.search(r"(\d*\.?\d+)\s*(mg|mcg|µg|ug|g)\b", text.lower())
    if not m:
        return None
    unit = {"µg": "mcg", "ug": "mcg"}.get(m.group(2), m.group(2))
    return float(m.group(1)), unit


def strength_mg(text):
    p = parse_strength(text)
    return None if p is None else dpd.to_mg(p[0], p[1])


def simple_lookup(drug_text, strength_text):
    """Baseline: any marketed product whose ingredient or brand contains the name and whose strength
    number matches; first by DIN. No release-type, combination or pharmacy-preference handling."""
    name = (drug_text or "").upper().strip()
    ps = parse_strength(strength_text)
    if not name or not ps:
        return ""
    for p in dpd.products():
        hit_name = name in p["brand"].upper() or any(name in i["name"].upper() for i in p["ingredients"])
        if hit_name and any(abs(i["strength"] - ps[0]) < 1e-9 for i in p["ingredients"]):
            return p["din"]
    return ""


_CHOICES = None


def _choices():
    global _CHOICES
    if _CHOICES is None:
        names = {b: {b} for b in dpd.single_ingredient_index()}
        for k, v in dpd.brand_index().items():
            names.setdefault(k, set()).update(v)
        _CHOICES = names
    return _CHOICES


def resolve_fuzzy(drug_text):
    """-> (bases, release, score, runner_up) ; runner_up = a different drug that is almost as close."""
    bases, release = dpd.resolve_name(drug_text)
    n = dpd.norm_brand(drug_text)
    if not n:
        return set(), release, 0, None
    ch = _choices()
    top = process.extract(n, list(ch), scorer=fuzz.ratio, limit=4)
    if bases:
        runner = next((c for c, s, _ in top if not (ch[c] & bases) and s >= 86), None)
        return bases, release, 100, runner
    if top and top[0][1] >= 85:
        best = ch[top[0][0]]
        runner = next((c for c, s, _ in top[1:] if not (ch[c] & best) and s >= top[0][1] - 5), None)
        return set(best), release, top[0][1], runner
    return set(), release, top[0][1] if top else 0, None


def match(drug_text, strength_text, form_hint="", history=None, patient_dins=None, no_sub=False):
    """Step 1 finds the product group (ingredient, strength, form, release): that is the safety part.
    Step 2 picks the maker inside the group, in this order:
      1. "No substitution" + a brand written -> that brand's DIN (if the brand is not in the group: red)
      2. the patient's last fill of this product -> same maker again
      3. the pharmacy's usual DIN for this product (past "DIN selected")
      4. otherwise pre-select nothing: the pharmacist picks from the short list (no guessing)
    history: {group: din}; patient_dins: {group: din} for this patient."""
    out = dict(din="", group="", base="", candidates=[], reasons=[], name_score=0, runner_up=None, marketed=[],
               din_basis="no_match")
    bases, release, score, runner = resolve_fuzzy(drug_text)
    out.update(name_score=score, runner_up=runner)
    if not bases:
        out["reasons"].append("drug name not found in Health Canada list")
        return out
    if score < 100:
        out["reasons"].append(f"name matched approximately ({score:.0f}/100)")
    if runner:
        out["reasons"].append(f"looks like another drug: {runner.title()}")
    mg = strength_mg(strength_text)
    idx = dpd.single_ingredient_index()
    prods = [p for b in sorted(bases) for p in idx.get(b, []) if p["release"] == release]
    if form_hint in ("tablet", "capsule", "inhaler"):
        prods = [p for p in prods if p["form_family"] == form_hint] or prods
    out["base"] = sorted(bases)[0]
    out["marketed"] = sorted({dpd.group_key(p)[1] for p in prods if isinstance(dpd.group_key(p)[1], float)})
    if mg is None:
        out["reasons"].append("strength not readable")
        return out
    hits = [p for p in prods if abs(dpd.group_key(p)[1] - mg) < 1e-6] if prods else []
    if not hits:
        shown = ", ".join(f"{m:g} mg" for m in out["marketed"][:8])
        out["reasons"].append(f"{mg:g} mg is not a marketed strength for this drug ({'ER' if release == 'ER' else 'regular'}); marketed: {shown}")
        out["strength_not_marketed"] = True
        return out
    groups = {}
    for p in hits:
        groups.setdefault(json.dumps(dpd.group_key(p)), []).append(p)
    gid = sorted(groups, key=lambda g: -len(groups[g]))[0]
    prods_g = sorted(groups[gid], key=lambda p: p["din"])
    dins = [p["din"] for p in prods_g]
    out.update(group=gid, candidates=dins[:8], controlled=any(p["controlled"] for p in prods_g))
    written = dpd.norm_brand(drug_text)
    is_brand = bool(written) and dpd.ingredient_base(written) not in dpd.single_ingredient_index()
    if no_sub:
        if is_brand:
            hit = [p["din"] for p in prods_g if dpd.norm_brand(p["brand"]) == written
                   or dpd.norm_brand(p["brand"]).split()[:1] == written.split()[:1]]
            if hit:
                out.update(din=hit[0], din_basis="no_substitution", candidates=hit)
                out["note"] = f"'No substitution': brand {drug_text} only"
            else:
                out["reasons"].append(f"'No substitution' written, but brand {drug_text} not found for this strength")
                out["din_basis"] = "needs_choice"
            return out
        out["din_basis"] = "needs_choice"
        out["note"] = "'No substitution' written with a generic name: confirm the exact product with the prescriber"
        return out
    if patient_dins and patient_dins.get(gid) in dins:
        out.update(din=patient_dins[gid], din_basis="patient_last_fill", note="same maker as this patient's last fill")
    elif history and history.get(gid) in dins:
        out.update(din=history[gid], din_basis="pharmacy_usual", note="pharmacy's usual product")
    else:
        out.update(din="", din_basis="needs_choice",
                   note=f"no past selection: pharmacist picks one of {len(dins)} interchangeable products")
    return out
