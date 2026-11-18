"""Command line.

python -m rxproto build-dpd            # turn data/dpd_raw/allfiles.zip into data/dpd_products.json
python -m rxproto generate --n 150     # make the synthetic test set
python -m rxproto models               # list Gemini models your key can use
python -m rxproto run --setup both     # run set-up A and/or B (cached; safe to stop and rerun)
python -m rxproto score                # write results/report.md
python -m rxproto review --n 6         # write results/review.html (what the pharmacist would see)
Set RX_FAKE=1 to dry-run everything without a key (the scores only test the code).
"""
import argparse
import base64
import html
import json
import sys
from pathlib import Path

from . import dpd, evaluate, generate, llm, readers

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"


def _load_results(setup):
    p = RES / f"{setup}.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def cmd_run(args):
    import os
    recs = generate.load_labels()[: args.limit or None]
    hist = generate.load_history()
    fake = None
    if os.getenv("RX_FAKE") == "1":
        from .fake import fake
    RES.mkdir(exist_ok=True)
    for setup in (["A", "B"] if args.setup == "both" else [args.setup]):
        out = []
        for k, rec in enumerate(recs, 1):
            img = generate.OUT_DIR / "images" / f"{rec['id']}.png"
            before = (llm.USAGE.calls + llm.USAGE.cached, {m: list(v) for m, v in llm.USAGE.tokens.items()}, llm.USAGE.seconds)
            try:
                r = readers.run_a(rec, img, fake) if setup == "A" else readers.run_b(rec, img, hist, fake)
            except llm.CallLimitReached as e:
                print(f"\nStopped: {e}")
                break
            r["usage"] = dict(calls=llm.USAGE.calls + llm.USAGE.cached - before[0], seconds=llm.USAGE.seconds - before[2],
                              tokens={m: [a - b for a, b in zip(v, before[1].get(m, [0, 0, 0]))] for m, v in llm.USAGE.tokens.items()})
            out.append(r)
            if k % 10 == 0 or k == len(recs):
                print(f"{setup}: {k}/{len(recs)} done (new calls {llm.USAGE.calls}, from cache {llm.USAGE.cached})", flush=True)
        with open(RES / f"{setup}.jsonl", "w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")


def cmd_score(args):
    recs = generate.load_labels()
    results = {s: _load_results(s) for s in ("A", "B")}
    results = {s: v for s, v in results.items() if v}
    if not results:
        sys.exit("No results yet. Run: python -m rxproto run --setup both")
    rows = evaluate.score(recs, results)
    p = evaluate.report(recs, results, rows)
    print(p.read_text(encoding="utf-8"))


def cmd_review(args):
    recs = {r["id"]: r for r in generate.load_labels()}
    rows = [json.loads(l) for l in (RES / "field_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    rowsB = {}
    for x in rows:
        if x["setup"] == "B":
            rowsB.setdefault(x["id"], {})[x["field"]] = x
    resB = {r["id"]: r for r in _load_results("B")}
    pick = sorted(rowsB, key=lambda i: -sum(x["color"] != "green" for x in rowsB[i].values()))[: args.n]
    col = {"green": "#e7f6ec", "amber": "#fff1d6", "red": "#fde2e1"}
    edge = {"green": "#2e7d32", "amber": "#b26a00", "red": "#c62828"}
    parts = ["<!doctype html><meta charset=utf-8><title>Review screen mock-up</title><style>body{font-family:Segoe UI,Arial;margin:16px;background:#f6f7f9}"
             ".rx{display:flex;gap:16px;background:#fff;padding:12px;margin-bottom:24px;border-radius:8px}img{width:520px;border:1px solid #ccc}"
             "table{border-collapse:collapse;font-size:14px}td{padding:4px 8px;border-bottom:1px solid #eee;vertical-align:top}.why{color:#555;font-size:12px}</style>",
             "<h2>Review screen mock-up (set-up B, synthetic prescriptions)</h2><p>Green = quick glance, amber = please check, red = most likely wrong. "
             "The label column is the true answer, shown here only for this demo.</p>"]
    for i in pick:
        b64 = base64.b64encode((generate.OUT_DIR / "images" / f"{i}.png").read_bytes()).decode()
        trs = []
        fl = resB[i]["flags"]
        for f in generate.FIELDS:
            x = rowsB[i][f]
            why = "; ".join(fl[f]["red"] + fl[f]["amber"]) or ""
            if not why and x["color"] != "green":
                why = f"learned score: {x['p_wrong']:.0%} chance of error"
            trs.append(f"<tr><td>{f}</td><td style='background:{col[x['color']]};border-left:4px solid {edge[x['color']]}'><b>{html.escape(str(x['pred']))}</b>"
                       f"<div class=why>{html.escape(why)}</div></td><td>{html.escape(recs[i]['label'][f])}{' &#10007;' if x['wrong'] else ''}</td></tr>")
        trs.append(f"<tr><td>DIN</td><td>{html.escape(resB[i]['din'] or '(none)')}<div class=why>{html.escape(resB[i].get('din_note', ''))}</div></td><td>{html.escape((recs[i]['pharmacy_din'] or '(none)') + ' - ' + recs[i]['din_basis'])}</td></tr>")
        parts.append(f"<div class=rx><img src='data:image/png;base64,{b64}'><div><h3>{i} ({recs[i]['style']})</h3>"
                     f"<table><tr><td></td><td><b>Read by AI</b></td><td><b>True label</b></td></tr>{''.join(trs)}</table></div></div>")
    (RES / "review.html").write_text("".join(parts), encoding="utf-8")
    print(RES / "review.html")


def main():
    ap = argparse.ArgumentParser(prog="rxproto")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-dpd")
    g = sub.add_parser("generate"); g.add_argument("--n", type=int, default=150)
    sub.add_parser("models")
    r = sub.add_parser("run"); r.add_argument("--setup", choices=["A", "B", "both"], default="both"); r.add_argument("--limit", type=int, default=0)
    sub.add_parser("score")
    v = sub.add_parser("review"); v.add_argument("--n", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "build-dpd":
        print(dpd.build_products(), "marketed human products")
    elif a.cmd == "generate":
        print(len(generate.generate(n=a.n)), "prescriptions in", generate.OUT_DIR)
    elif a.cmd == "models":
        print("\n".join(llm.list_models()))
    elif a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "score":
        cmd_score(a)
    elif a.cmd == "review":
        cmd_review(a)


if __name__ == "__main__":
    main()
