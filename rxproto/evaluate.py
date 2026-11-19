"""Score both set-ups against the labels with the same field rules, add confidence scores and colours to
set-up B, and write a comparison report with 95% bootstrap confidence intervals."""
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import numpy as np

from . import din, dpd, llm
from .generate import FIELDS
from .readers import dob_from_written, first_int
from .sig import parse_sig, same_meaning

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
CRITICAL = ["drug_name", "strength", "qty", "ds", "sig"]
EMPTY = {"", "none", "n/a", "na", "unreadable", "-", "null", "not present", "unknown"}
T_AMBER, T_RED = 0.15, 0.5


# ---------------- field scoring ----------------
def _plain(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _dob(s):
    s = (s or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    iso, _ = dob_from_written(s)
    if iso:
        return iso
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s


def outcome(field, pred, rec):
    """-> 'correct' | 'wrong' (wrong value) | 'miss' (left blank) | 'invented' (filled, but not on the page)."""
    lab = rec["label"][field]
    p_empty = str(pred or "").strip().lower() in EMPTY
    if lab == "":
        return "correct" if p_empty else "invented"
    if p_empty:
        return "miss"
    ok = False
    if field == "drug_name":
        bp, rp = dpd.resolve_name(pred)
        bl, rl = dpd.resolve_name(lab)
        ok = bool(bp) and bp == bl and rp == rl
    elif field == "strength":
        a, b = din.strength_mg(pred), din.strength_mg(lab)
        ok = a is not None and b is not None and abs(a - b) < 1e-9
    elif field == "form":
        ok = dpd.form_family(pred) == lab or (lab == "inhaler" and "puff" in str(pred).lower())
    elif field in ("qty", "qty_auth", "ds"):
        ok = first_int(pred) == int(lab)
    elif field == "sig":
        ok = same_meaning(parse_sig(pred), rec["sig_struct"])
    elif field == "patient_dob":
        ok = _dob(pred) == lab
    elif field == "prescriber_license":
        ok = re.sub(r"\D", "", str(pred)) == lab
    else:
        ok = _plain(pred) == _plain(lab)
    return "correct" if ok else "wrong"


# ---------------- confidence model (set-up B) ----------------
GROUPS = {"drug_name": 0, "strength": 1, "qty": 2, "qty_auth": 2, "ds": 2, "sig": 3, "form": 4, "patient_dob": 5,
          "prescriber_license": 6, "patient_first": 7, "patient_last": 7, "prescriber_first": 7, "prescriber_last": 7}


def features(field, fl, pred):
    s = fl["signals"]
    g = np.zeros(8)
    g[GROUPS[field]] = 1
    doc = (s.get("doc_type") or "").lower()
    band = s.get("band", "")
    return np.concatenate([[1.0, s["disputed"], s["strong"], s["unresolved"], s["uncertain"], bool(fl["red"]),
                            bool(fl["amber"]), "hand" in doc, "fax" in doc, str(pred or "").strip() == "",
                            band == "Medium", band == "Hard"], g]).astype(float)


def fit_logreg(X, y, l2=1.0, iters=800, lr=0.5):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        grad = X.T @ (p - y) / len(y) + l2 * np.r_[0, w[1:]] / len(y)
        w -= lr * grad
    return w


def crossfit_p(X, y, groups, k=5, seed=0):
    """Out-of-fold probabilities: each prescription is scored by a model that never saw it."""
    rng = np.random.default_rng(seed)
    ug = np.unique(groups)
    rng.shuffle(ug)
    folds = {g: i % k for i, g in enumerate(ug)}
    f = np.array([folds[g] for g in groups])
    p = np.zeros(len(y))
    for i in range(k):
        tr, te = f != i, f == i
        w = fit_logreg(X[tr], y[tr]) if y[tr].sum() > 0 else np.zeros(X.shape[1])
        p[te] = 1 / (1 + np.exp(-X[te] @ w))
    return p


def auroc(score, y):
    pos, neg = score[y == 1], score[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.r_[pos, neg], kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    allv = np.r_[pos, neg]
    for v in np.unique(allv):  # average ties
        m = allv == v
        ranks[m] = ranks[m].mean()
    return (ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def ece(p, y, bins=10):
    e, edges = 0.0, np.linspace(0, 1, bins + 1)
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p < b if b < 1 else p <= b)
        if m.any():
            e += m.mean() * abs(p[m].mean() - y[m].mean())
    return e


# ---------------- bootstrap ----------------
def boot_ci(per_rx_num, per_rx_den, n=2000, seed=1):
    num, den = np.asarray(per_rx_num, float), np.asarray(per_rx_den, float)
    point = num.sum() / den.sum() if den.sum() else float("nan")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(num), (n, len(num)))
    with np.errstate(invalid="ignore", divide="ignore"):
        s = num[idx].sum(1) / den[idx].sum(1)
    s = s[~np.isnan(s)]
    return point, (np.percentile(s, 2.5) if len(s) else np.nan), (np.percentile(s, 97.5) if len(s) else np.nan)


def boot_diff(a_num, b_num, den, n=2000, seed=2):
    a, b, d = map(lambda x: np.asarray(x, float), (a_num, b_num, den))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), (n, len(a)))
    s = (b[idx].sum(1) - a[idx].sum(1)) / d[idx].sum(1)
    return (b.sum() - a.sum()) / d.sum(), np.percentile(s, 2.5), np.percentile(s, 97.5)


def pct(x):
    return "n/a" if x != x else f"{100 * x:.1f}%"


def ci(t):
    return f"{pct(t[0])} ({pct(t[1])} to {pct(t[2])})"


# ---------------- main ----------------
def score(recs, results):
    byid = {r["id"]: r for r in recs}
    rows = {}
    for setup, res in results.items():
        out = []
        for r in res:
            rec = byid[r["id"]]
            for f in FIELDS:
                oc = outcome(f, r["fields"].get(f, ""), rec)
                out.append(dict(id=r["id"], style=rec["style"], field=f, outcome=oc, wrong=oc != "correct",
                                issue=bool(rec["issues"].get(f)), pred=r["fields"].get(f, ""), fl=r["flags"].get(f),
                                band=r.get("band", "")))
        rows[setup] = out
    for setup in ("B", "C"):
        if setup not in rows:
            continue
        B = rows[setup]
        X = np.array([features(x["field"], x["fl"], x["pred"]) for x in B])
        y = np.array([x["wrong"] for x in B], float)
        p = crossfit_p(X, y, np.array([x["id"] for x in B]))
        for x, pi in zip(B, p):
            x["p_wrong"] = float(pi)
            rule_red, rule_amber = bool(x["fl"]["red"]), bool(x["fl"]["amber"])
            x["color_rules"] = "red" if rule_red else ("amber" if rule_amber else "green")
            x["color"] = "red" if (rule_red or pi >= T_RED) else ("amber" if (rule_amber or pi >= T_AMBER) else "green")
    return rows


def _usual(rec):
    from .generate import _pharmacy_pick
    return _pharmacy_pick([{"din": d} for d in rec["group_dins"]]) if rec.get("group_dins") else ""


def _per_rx(rows, ids, pred, base=lambda x: True):
    num = [sum(1 for x in rows if x["id"] == i and base(x) and pred(x)) for i in ids]
    den = [sum(1 for x in rows if x["id"] == i and base(x)) for i in ids]
    return num, den


def report(recs, results, rows, path=RES / "report.md"):
    ids = [r["id"] for r in recs if all(any(x["id"] == r["id"] for x in results[s]) for s in results)]
    fake = os.getenv("RX_FAKE") == "1"
    L = ["# Prescription reading: set-ups A, B and C", ""]
    if fake:
        L += ["> Dry run with the fake reader (RX_FAKE=1). These numbers only test the code.", ""]
    L += [f"Synthetic test set: {len(ids)} prescriptions ({', '.join(f'{s}: {sum(1 for r in recs if r['id'] in ids and r['style']==s)}' for s in ('printed','handwritten','fax'))}), "
          f"{len(FIELDS)} fields each. Drugs and strengths from Health Canada's Drug Product Database; people and clinics invented.",
          "", "A: a typical single-pass design (one model call, simple product lookup). B: every prescription fully checked. "
          "C: triage into Easy / Medium / Hard, then a lane per band.",
          "Synthetic images are easier than real faxes, so treat these numbers as a comparison, not an accuracy estimate.", ""]
    setups = [s for s in ("A", "B", "C") if s in rows]
    L += ["## 1. Reading accuracy (95% CI by bootstrap over prescriptions)", "", "| Measure | " + " | ".join(f"Set-up {s}" for s in setups) + " |", "|---|" + "---|" * len(setups)]
    def acc_line(name, base):
        cells = []
        for s in setups:
            n, d = _per_rx(rows[s], ids, lambda x: not x["wrong"], base)
            cells.append(ci(boot_ci(n, d)))
        L.append(f"| {name} | " + " | ".join(cells) + " |")
    acc_line("All fields", lambda x: True)
    acc_line("Critical fields (drug, strength, qty, days supply, sig)", lambda x: x["field"] in CRITICAL)
    for st in ("printed", "handwritten", "fax"):
        acc_line(f"All fields, {st}", lambda x, st=st: x["style"] == st)
    cells = []
    for s in setups:
        n = [int(all(not x["wrong"] for x in rows[s] if x["id"] == i)) for i in ids]
        cells.append(ci(boot_ci(n, [1] * len(ids))))
    L.append("| Prescriptions with every field right | " + " | ".join(cells) + " |")
    L.append("")
    for a_, b_ in (("A", "B"), ("A", "C"), ("B", "C")):
        if a_ in rows and b_ in rows:
            an, d = _per_rx(rows[a_], ids, lambda x: not x["wrong"])
            bn, _ = _per_rx(rows[b_], ids, lambda x: not x["wrong"])
            dd = boot_diff(an, bn, d)
            ac, dc = _per_rx(rows[a_], ids, lambda x: not x["wrong"], lambda x: x["field"] in CRITICAL)
            bc, _ = _per_rx(rows[b_], ids, lambda x: not x["wrong"], lambda x: x["field"] in CRITICAL)
            dc2 = boot_diff(ac, bc, dc)
            L.append(f"Paired difference {b_} minus {a_}, all fields: **{100*dd[0]:+.1f} points** (95% CI {100*dd[1]:+.1f} to {100*dd[2]:+.1f}); "
                     f"critical fields: **{100*dc2[0]:+.1f} points** ({100*dc2[1]:+.1f} to {100*dc2[2]:+.1f}).  ")
    L += ["", "## 2. Accuracy by field", "", "| Field | " + " | ".join(setups) + " |", "|---|" + "---|" * len(setups)]
    for f in FIELDS:
        L.append(f"| {f} | " + " | ".join(pct(np.mean([not x['wrong'] for x in rows[s] if x['field'] == f and x['id'] in ids])) for s in setups) + " |")
    L += ["", "## 3. Kinds of error (counts over all fields)", "", "| Outcome | " + " | ".join(setups) + " |", "|---|" + "---|" * len(setups)]
    for oc, desc in (("wrong", "Wrong value"), ("miss", "Left blank, but on the page"), ("invented", "Filled in, but not on the page")):
        L.append(f"| {desc} | " + " | ".join(str(sum(1 for x in rows[s] if x['outcome'] == oc and x['id'] in ids)) for s in setups) + " |")
    crit_wrong = {s: sum(1 for x in rows[s] if x["id"] in ids and x["field"] in ("drug_name", "strength") and x["outcome"] in ("wrong", "invented")) for s in setups}
    L.append("| Wrong or invented drug / strength (most dangerous) | " + " | ".join(str(crit_wrong[s]) for s in setups) + " |")

    L += ["", "## 4. Product (DIN) pre-selection", "", "| Measure | " + " | ".join(setups) + " |", "|---|" + "---|" * len(setups)]
    byid = {r["id"]: r for r in recs}
    dmaps = {s: {x["id"]: x["din"] for x in results[s]} for s in setups}
    marketed = [i for i in ids if byid[i].get("group_dins")]
    # Safety: whatever maker is pre-selected must belong to the right product group (a blank is not wrong, just unfilled)
    cells = []
    for s in setups:
        picked = [i for i in marketed if dmaps[s][i]]
        cells.append(ci(boot_ci([int(dmaps[s][i] in byid[i]["group_dins"]) for i in picked], [1] * len(picked))) + f" of {len(picked)} picks")
    L.append("| **Safety:** pre-selected DIN is the right product (ingredient, strength, form, release) | " + " | ".join(cells) + " |")
    basis_names = {"no_substitution": "'No substitution' on a brand: that brand pre-selected",
                   "patient_last_fill": "Patient had it before: same maker pre-selected",
                   "pharmacy_usual": "Pharmacy's usual product pre-selected",
                   "none": "No history at all: nothing pre-selected (pharmacist picks)",
                   "not_marketed": "Strength not marketed: nothing pre-selected"}
    for basis, name in basis_names.items():
        group = [i for i in ids if byid[i].get("din_basis") == basis]
        if not group:
            continue
        ok = (lambda i, d: d == "") if basis in ("none", "not_marketed") else (lambda i, d: d == byid[i]["pharmacy_din"])
        L.append(f"| {name} | " + " | ".join(f"{sum(ok(i, dmaps[s][i]) for i in group)}/{len(group)}" for s in setups) + " |")
    other = [i for i in ids if byid[i].get("din_basis") == "patient_last_fill" and byid[i]["pharmacy_din"] != _usual(byid[i])]
    if other:
        L.append("| ...of which the patient's last maker differs from the pharmacy's usual | " + " | ".join(
            f"{sum(dmaps[s][i] == byid[i]['pharmacy_din'] for i in other)}/{len(other)}" for s in setups) + " |")
    L.append("")
    L.append("Set-up A always takes the first product its lookup finds, so it cannot follow 'No substitution', a patient's last fill or the pharmacy's habit, and it guesses when there is no history.")

    flagged = [x for x in ("B", "C") if x in rows]
    if flagged:
        L += ["", "## 5. Risk highlights (set-up A has none, like today)", "",
              "A field **should be flagged** if it was read wrong **or** carries a planted safety issue "
              "(ISMP abbreviation, strength not marketed, days-supply mismatch, ambiguous date, controlled drug). "
              f"Confidence scores are out-of-fold (5-fold, by prescription). Thresholds: amber at {T_AMBER:.0%}, red at {T_RED:.0%} chance of error.", "",
              "| Measure | " + " | ".join(f"Set-up {x}" for x in flagged) + " |", "|---|" + "---|" * len(flagged)]

        def m(rs, key="color"):
            y = np.array([x["wrong"] for x in rs])
            fl = np.array([x[key] != "green" for x in rs])
            should = np.array([x["wrong"] or x["issue"] for x in rs])
            issue = np.array([x["issue"] for x in rs])
            green = ~fl
            p = np.array([x["p_wrong"] for x in rs])
            return dict(rate=fl.mean(), catch=(fl & should).sum() / max(should.sum(), 1),
                        catch_err=(fl & y).sum() / max(y.sum(), 1), catch_issue=(fl & issue).sum() / max(issue.sum(), 1),
                        false=(fl & ~should).sum() / max((~should).sum(), 1), green_err=(green & y).sum() / max(green.sum(), 1),
                        green_n=int(green.sum()), green_wrong=int((green & y).sum()),
                        auroc=auroc(p, y.astype(float)), ece=ece(p, y.astype(float)), n_should=int(should.sum()))
        R = {x: m([r for r in rows[x] if r["id"] in ids]) for x in flagged}
        rules_only = {x: m([r for r in rows[x] if r["id"] in ids], "color_rules") for x in flagged}
        L += ["| Share of fields flagged (amber or red) | " + " | ".join(pct(R[x]["rate"]) for x in flagged) + " |",
              "| Should-flag fields caught | " + " | ".join(f"{pct(R[x]['catch'])} of {R[x]['n_should']}" for x in flagged) + " |",
              "| ...with the rules alone (no learned score) | " + " | ".join(pct(rules_only[x]["catch"]) for x in flagged) + " |",
              "| Reading errors caught | " + " | ".join(pct(R[x]["catch_err"]) for x in flagged) + " |",
              "| Planted safety issues caught | " + " | ".join(pct(R[x]["catch_issue"]) for x in flagged) + " |",
              "| Fine fields flagged anyway (false alarms) | " + " | ".join(pct(R[x]["false"]) for x in flagged) + " |",
              "| **Error rate among green fields** (green means green) | " + " | ".join(f"{pct(R[x]['green_err'])} ({R[x]['green_wrong']} of {R[x]['green_n']})" for x in flagged) + " |",
              "| Confidence score AUROC / calibration error | " + " | ".join(f"{R[x]['auroc']:.2f} / {R[x]['ece']:.3f}" for x in flagged) + " |"]

    if "C" in rows:
        Cres = {x["id"]: x for x in results["C"] if x["id"] in ids}
        L += ["", "## 5b. Lanes (set-up C)", "",
              "| Band | Prescriptions | Moved up a lane | Model calls per Rx | Field accuracy | Critical-field accuracy | Error rate among green fields |",
              "|---|---|---|---|---|---|---|"]
        for band in ("Easy", "Medium", "Hard"):
            bid = [i for i, r in Cres.items() if r.get("band") == band]
            if not bid:
                L.append(f"| {band} | 0 | | | | | |")
                continue
            rs = [x for x in rows["C"] if x["id"] in bid]
            crit = [x for x in rs if x["field"] in CRITICAL]
            green = [x for x in rs if x.get("color") == "green"]
            calls = np.mean([Cres[i].get("usage", {}).get("calls", 0) for i in bid])
            L.append(f"| {band} | {len(bid)} | {sum(1 for i in bid if Cres[i].get('escalated'))} | {calls:.2f} | "
                     f"{pct(np.mean([not x['wrong'] for x in rs]))} | {pct(np.mean([not x['wrong'] for x in crit]))} | "
                     f"{pct(sum(x['wrong'] for x in green) / max(len(green), 1))} |")
        if "B" in rows:
            L += ["", "Set-up B gives every prescription the full checks; set-up C spends them by band. Compare the accuracy rows in section 1 with the calls and tokens in section 6."]

    L += ["", "## 6. Cost and time per prescription", "", "| Measure | " + " | ".join(setups) + " |", "|---|" + "---|" * len(setups)]
    agg = {}
    for s in setups:
        us = [x.get("usage", {}) for x in results[s] if x["id"] in ids]
        tok = {}
        for u in us:
            for m_, v in u.get("tokens", {}).items():
                t = tok.setdefault(m_, [0, 0, 0])
                for j in range(3):
                    t[j] += v[j]
        agg[s] = dict(calls=sum(u.get("calls", 0) for u in us) / max(len(us), 1),
                      secs=sum(u.get("seconds", 0) for u in us) / max(len(us), 1), tok=tok, n=len(us))
    L.append("| Model calls | " + " | ".join(f"{agg[s]['calls']:.2f}" for s in setups) + " |")
    L.append("| Model time (seconds, sum of calls) | " + " | ".join(f"{agg[s]['secs']:.1f}" for s in setups) + " |")
    L.append("| Tokens in / out / thinking | " + " | ".join(
        "<br>".join(f"{m_}: {v[0]/agg[s]['n']:,.0f} / {v[1]/agg[s]['n']:,.0f} / {v[2]/agg[s]['n']:,.0f}" for m_, v in agg[s]["tok"].items()) or "-" for s in setups) + " |")
    def usd(tok):
        tot = 0.0
        for m_, (a, b, c) in tok.items():
            kind = "STRONG" if m_ == os.getenv("RX_MODEL_STRONG") else "FAST"
            pin, pout = os.getenv(f"RX_PRICE_IN_{kind}"), os.getenv(f"RX_PRICE_OUT_{kind}")
            if not pin or not pout:
                return None
            tot += a / 1e6 * float(pin) + (b + c) / 1e6 * float(pout)
        return tot
    L.append("| Estimated US$ (needs RX_PRICE_* in .env) | " + " | ".join(
        (f"${usd(agg[s]['tok'])/agg[s]['n']:.4f}" if usd(agg[s]['tok']) is not None else "set prices") for s in setups) + " |")

    L += ["", "## 7. How to read this", "",
          "- The test set is synthetic: printed, handwritten-font and fax-degraded images with planted traps. Real faxes and real handwriting are harder.",
          "- Set-up A stands for a typical first version of a prescription reader.",
          "- The scorer, flags and DIN matcher are meant to run unchanged on a real labelled set."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    with open(RES / "field_rows.jsonl", "w", encoding="utf-8") as f:
        for s in rows:
            for x in rows[s]:
                x2 = {k: v for k, v in x.items() if k != "fl"}
                x2["setup"] = s
                f.write(json.dumps(x2) + "\n")
    return path
