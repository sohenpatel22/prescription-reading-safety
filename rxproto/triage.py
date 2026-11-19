"""Triage: sort a prescription into Easy, Medium or Hard and clean the image."""
import io
import os
from typing import List

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel

from . import llm

BANDS = ["Easy", "Medium", "Hard"]
GRID = {  # how it was written -> how it arrived -> band
    "typed":       {"digital_pdf": "Easy",   "scan": "Easy",   "photo": "Medium", "fax": "Medium"},
    "mixed":       {"digital_pdf": "Medium", "scan": "Medium", "photo": "Hard",   "fax": "Hard"},
    "handwritten": {"digital_pdf": "Medium", "scan": "Hard",   "photo": "Hard",   "fax": "Hard"},
}


class TriageAI(BaseModel):
    written: str            # typed | handwritten | mixed
    legibility_drug: int    # 1 (unreadable) .. 5 (perfectly clear)
    problems: List[str]     # e.g. smudge, crossed out, cut off, stamp over text, shadow, blurry


PROMPT_T = """Look at this prescription image (low resolution on purpose) and judge only how hard it is to read.
- written: "typed" if the prescription details are printed, "handwritten" if everything is by hand,
  "mixed" if a printed form or pad has handwritten entries.
- legibility_drug: 1-5, how clearly the drug name, strength and directions can be read (5 = perfectly clear).
- problems: short words for anything that makes reading harder: smudge, crossed out, cut off, stamp,
  shadow, blurry, faint, tilted, specks. Empty list if none."""


def image_checks(img):
    """Measurements from the image itself. Values are on a 1000-px-wide grey copy."""
    g = img.convert("L")
    full = np.asarray(g, dtype=float)
    grey_share = float(((full > 40) & (full < 215)).mean() * 1000)   # mid-grey pixels per 1000
    ink = full[full < 200]
    small = g.resize((1000, int(g.height * 1000 / g.width)), Image.BILINEAR)
    a = np.asarray(small, dtype=float)
    lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    dark = a < 128
    inner = dark[1:-1, 1:-1]
    lonely = inner & ~dark[:-2, 1:-1] & ~dark[2:, 1:-1] & ~dark[1:-1, :-2] & ~dark[1:-1, 2:]
    bg = np.asarray(small.filter(ImageFilter.GaussianBlur(25)), dtype=float)
    return dict(
        black_white=grey_share < 2.0,                # fax pages are almost pure black and white
        grey_share=grey_share,
        blur=float(lap.var()),                       # low = blurry
        ink_darkness=float(np.percentile(ink, 10)) if ink.size else 255.0,   # high = faint ink
        specks=float(lonely.mean() * 1000),          # isolated dark dots per 1000 px
        uneven_light=float(np.percentile(bg, 95) - np.percentile(bg, 5)),  # high = shadow
        tilt=_tilt(small),
    )


def _tilt(small):
    """Angle (degrees) that makes text lines most horizontal, searched in -3..3."""
    thumb = ImageOps.invert(small.resize((400, int(small.height * 400 / small.width))))
    best, best_score = 0.0, -1.0
    for ang in np.arange(-3, 3.01, 0.5):
        rows = np.asarray(thumb.rotate(ang, fillcolor=0), dtype=float).sum(axis=1)
        sc = rows.var()
        if sc > best_score:
            best, best_score = float(ang), sc
    return best


def arrived_from_checks(ch, channel=None):
    """In production the intake system knows the channel. Here it is inferred from the image."""
    if channel:
        return channel
    if ch["black_white"]:
        return "fax"
    if ch["uneven_light"] > 40 or abs(ch["tilt"]) >= 1.5:
        return "photo"
    return "scan"


def decide(ch, ai, channel=None):
    written = (ai.get("written") or "mixed").lower()
    written = written if written in GRID else "mixed"
    arrived = arrived_from_checks(ch, channel)
    band = GRID[written][arrived]
    reasons = [written, arrived.replace("_", " ")]
    bump = []
    if int(ai.get("legibility_drug") or 3) <= 2:
        bump.append("drug area hard to read")
    probs = [p.lower() for p in (ai.get("problems") or [])]
    for p in ("smudge", "crossed", "cut off", "stamp"):
        if any(p in q for q in probs):
            bump.append(p)
    if ch["blur"] < 150:
        bump.append("blurry")
    if bump:
        band = BANDS[min(2, BANDS.index(band) + 1)]
        reasons += bump
    problems = probs + (["tilted"] if abs(ch["tilt"]) >= 0.5 else []) + (["shadow"] if ch["uneven_light"] > 40 else []) \
        + (["specks"] if ch["specks"] > 0.05 else []) + (["faint"] if ch["ink_darkness"] > 100 else [])
    return dict(band=band, reasons=reasons, written=written, arrived=arrived, problems=sorted(set(problems)))


def clean(img, tri):
    """Image clean-up chosen from the triage findings. Returns (image, steps applied)."""
    g = img.convert("L")
    steps = []
    probs = set(tri["problems"])
    if tri["arrived"] == "fax" or "specks" in probs:
        g = g.filter(ImageFilter.MedianFilter(3))
        steps.append("removed specks")
    if "shadow" in probs:
        bg = g.filter(ImageFilter.GaussianBlur(40))
        a = np.asarray(g, dtype=float) / np.maximum(np.asarray(bg, dtype=float), 1) * 235
        g = Image.fromarray(np.clip(a, 0, 255).astype("uint8"))
        steps.append("evened the light")
    if "tilted" in probs:
        ang = _tilt(g.resize((1000, int(g.height * 1000 / g.width))))
        if abs(ang) >= 0.5:
            g = g.rotate(ang, fillcolor=255, resample=Image.BICUBIC)
            steps.append(f"straightened {ang:+.1f} deg")
    cutoff = 2 if "faint" in probs else 1
    g = ImageOps.autocontrast(g, cutoff=cutoff)
    steps.append("contrast")
    return g, steps


def triage(rec, img, fake=None, channel=None):
    ch = image_checks(img)
    small = img.convert("L").resize((640, int(img.height * 640 / img.width)), Image.BILINEAR)
    b = io.BytesIO()
    small.save(b, format="PNG")
    model = os.getenv("RX_MODEL_TRIAGE", "gemini-flash-lite-latest")
    ai = llm.ask_image(model, b.getvalue(), PROMPT_T, TriageAI, "T", fake_answer=fake and (lambda: fake("T", rec)))
    out = decide(ch, ai, channel)
    out["checks"] = {k: round(v, 2) if isinstance(v, float) else v for k, v in ch.items()}
    out["ai"] = ai
    return out
