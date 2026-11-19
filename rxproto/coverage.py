"""How much can the rule book catch on its own, with no model at all?

For every prescription in the test set:
- strength: every single-character misread of the strength number (one digit changed, dropped or added,
  or the decimal point shifted). A misread is caught if the new strength is not sold in Canada for that
  drug, form and release type.
- drug name: every one-letter misspelling of the generic name. It is caught if the name no longer matches
  a drug, or matches only approximately (flagged amber). It slips through if it is exactly another real drug.
These numbers come from Health Canada's Drug Product Database and plain code. No model is involved.
"""
import json
import string
from collections import Counter

from . import din, dpd


def strength_variants(text, swaps_only=False):
    """'500' -> {400.0, 50.0, 5000.0, ...}: values reachable by one character edit of a number string.
    swaps_only=True keeps only one digit read as another digit (the most realistic misread)."""
    out = set()
    digits = "0123456789"
    for i, ch in enumerate(text):
        if ch.isdigit():
            for d in digits:
                if d != ch:
                    out.add(text[:i] + d + text[i + 1:])
            if not swaps_only:
                out.add(text[:i] + text[i + 1:])
        if swaps_only:
            continue
        for d in digits:
            out.add(text[:i] + d + text[i:])
    if swaps_only:
        out.discard(text)
        return {float(o) for o in out if _num(o) and float(o) > 0 and float(o) != float(text)}
    for d in digits:
        out.add(text + d)
    try:
        v = float(text)
        out |= {repr(v * 10).rstrip("0").rstrip("."), repr(v / 10).rstrip("0").rstrip(".")}
    except ValueError:
        pass
    return {float(o) for o in out if _num(o) and float(o) > 0 and float(o) != float(text)}


def _num(o):
    try:
        float(o)
        return True
    except ValueError:
        return False


def marketed_mg(base, form, release):
    return {dpd.group_key(p)[1] for p in dpd.single_ingredient_index().get(base, [])
            if p["form_family"] == form and p["release"] == release}


def run(recs):
    s_total = s_caught = w_total = w_caught = 0
    slipped = Counter()
    n_total = n_red = n_amber = n_other = 0
    others = Counter()
    for r in recs:
        if not r.get("group"):
            continue
        base, mg, form, release = json.loads(r["group"])
        unit = r["label"]["strength"].split()[-1]
        num = r["label"]["strength"].split()[0]
        ok = marketed_mg(base, form, release)
        for v in strength_variants(num):
            s_total += 1
            if round(dpd.to_mg(v, unit), 6) not in ok:
                s_caught += 1
        for v in strength_variants(num, swaps_only=True):
            w_total += 1
            if round(dpd.to_mg(v, unit), 6) not in ok:
                w_caught += 1
            else:
                slipped[f"{base.title()} {num} -> {v:g} {unit}"] += 1
        name = base.lower()
        for i in range(len(name)):
            for c in string.ascii_lowercase:
                if c == name[i]:
                    continue
                typo = name[:i] + c + name[i + 1:]
                n_total += 1
                bases, rel, score, _ = din.resolve_fuzzy(typo)
                exact, _ = dpd.resolve_name(typo)
                if exact and base not in exact:
                    n_other += 1
                    others[f"{name} -> {typo}"] += 1
                elif not bases:
                    n_red += 1
                else:
                    n_amber += 1
    return dict(strength_total=s_total, strength_caught=s_caught, swap_total=w_total, swap_caught=w_caught, strength_slipped=slipped.most_common(8),
                name_total=n_total, name_red=n_red, name_amber=n_amber, name_other_drug=n_other,
                name_other_examples=others.most_common(8))


def report(res):
    st, sc = res["strength_total"], res["strength_caught"]
    wt, wc = res["swap_total"], res["swap_caught"]
    nt = res["name_total"]
    L = ["# What the rule book catches on its own (no model)", "",
         "Computed from Health Canada's Drug Product Database on the 150 synthetic prescriptions. Plain code; no model involved.", "",
         "| Misread | Cases | Caught by the rule book | Slips through |", "|---|---|---|---|",
         f"| **Strength: one digit read as another** (e.g. 5 read as 3) | {wt:,} | **{wc:,} ({wc / wt:.1%})** | {wt - wc:,} ({(wt - wc) / wt:.1%}) |",
         f"| Strength: any one-character error (also dropped, added, decimal shifted) | {st:,} | {sc:,} ({sc / st:.1%}) | {st - sc:,} ({(st - sc) / st:.1%}) |",
         f"| Drug name: one letter wrong | {nt:,} | {res['name_red'] + res['name_amber']:,} ({(res['name_red'] + res['name_amber']) / nt:.1%}): "
         f"{res['name_red']:,} no match (red), {res['name_amber']:,} close match (amber) | {res['name_other_drug']:,} ({res['name_other_drug'] / nt:.1%}) become another real drug |",
         "", "Note: look-alike drugs (hydroxyzine vs hydralazine) differ by several letters, not one; a separate look-alike check covers them.",
         "", "Strength misreads that slip through (still a real strength):"]
    L += [f"- {k} ({v}x)" for k, v in res["strength_slipped"]]
    L += ["", "One-letter misspellings that are another real drug:"]
    L += [f"- {k}" for k, _ in res["name_other_examples"]] or ["- none"]
    L += ["", "The ones that slip through are why the design still re-reads critical fields and scores confidence."]
    return "\n".join(L) + "\n"
