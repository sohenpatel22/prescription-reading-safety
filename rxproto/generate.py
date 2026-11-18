"""Make a synthetic, fully labelled set of Canadian-style prescriptions.

Every drug and strength comes from Health Canada's Drug Product Database (marketed products).
Patients, prescribers and clinics are invented. Three styles: printed (clinic software),
handwritten (Ink Free font with jitter, blue ink), and fax (either of those, degraded).
Some prescriptions carry planted traps; each trap is recorded in the label.
"""
import hashlib
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import dpd
from .sig import FREQ, write_sig

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "synth"
FONT_DIR = Path("C:/Windows/Fonts")
FIELDS = ["drug_name", "strength", "form", "qty", "qty_auth", "ds", "sig", "patient_first", "patient_last",
          "patient_dob", "prescriber_first", "prescriber_last", "prescriber_license"]

# generic name as written, DPD base, release, brands, frequencies, days-supply options, kind
POOL = [
    ("Metformin", "METFORMIN", "IR", ["Glucophage"], ["BID", "TID"], [30, 90], "chronic"),
    ("Metformin ER", "METFORMIN", "ER", [], ["OD"], [30, 90], "chronic"),
    ("Atorvastatin", "ATORVASTATIN", "IR", ["Lipitor"], ["OD", "QHS"], [30, 90], "chronic"),
    ("Rosuvastatin", "ROSUVASTATIN", "IR", ["Crestor"], ["OD"], [30, 90], "chronic"),
    ("Amlodipine", "AMLODIPINE", "IR", ["Norvasc"], ["OD"], [30, 90], "chronic"),
    ("Ramipril", "RAMIPRIL", "IR", ["Altace"], ["OD", "BID"], [30, 90], "chronic"),
    ("Lisinopril", "LISINOPRIL", "IR", [], ["OD"], [30, 90], "chronic"),
    ("Losartan", "LOSARTAN", "IR", [], ["OD"], [30, 90], "chronic"),
    ("Hydrochlorothiazide", "HYDROCHLOROTHIAZIDE", "IR", [], ["OD"], [30, 90], "chronic"),
    ("Metoprolol", "METOPROLOL", "IR", [], ["BID"], [30, 90], "chronic"),
    ("Bisoprolol", "BISOPROLOL", "IR", [], ["OD"], [30, 90], "chronic"),
    ("Levothyroxine", "LEVOTHYROXINE", "IR", ["Synthroid"], ["OD"], [30, 90], "chronic"),
    ("Pantoprazole", "PANTOPRAZOLE", "IR", [], ["OD", "BID"], [30, 90], "chronic"),
    ("Sertraline", "SERTRALINE", "IR", ["Zoloft"], ["OD"], [30], "chronic"),
    ("Citalopram", "CITALOPRAM", "IR", [], ["OD"], [30], "chronic"),
    ("Escitalopram", "ESCITALOPRAM", "IR", ["Cipralex"], ["OD"], [30], "chronic"),
    ("Venlafaxine XR", "VENLAFAXINE", "ER", ["Effexor XR"], ["OD"], [30], "chronic"),
    ("Trazodone", "TRAZODONE", "IR", [], ["QHS"], [30], "chronic"),
    ("Gabapentin", "GABAPENTIN", "IR", [], ["TID"], [30], "chronic"),
    ("Pregabalin", "PREGABALIN", "IR", ["Lyrica"], ["BID"], [30], "chronic"),
    ("Amoxicillin", "AMOXICILLIN", "IR", [], ["TID"], [7, 10], "antibiotic"),
    ("Cephalexin", "CEPHALEXIN", "IR", [], ["QID"], [7, 10], "antibiotic"),
    ("Ciprofloxacin", "CIPROFLOXACIN", "IR", [], ["BID"], [7], "antibiotic"),
    ("Metronidazole", "METRONIDAZOLE", "IR", [], ["BID", "TID"], [7], "antibiotic"),
    ("Prednisone", "PREDNISONE", "IR", [], ["OD"], [5, 7], "antibiotic"),
    ("Apixaban", "APIXABAN", "IR", ["Eliquis"], ["BID"], [30, 90], "chronic"),
    ("Rivaroxaban", "RIVAROXABAN", "IR", ["Xarelto"], ["OD"], [30, 90], "chronic"),
    ("Warfarin", "WARFARIN", "IR", [], ["OD"], [30], "chronic"),
    ("Furosemide", "FUROSEMIDE", "IR", ["Lasix"], ["OD", "BID"], [30, 90], "chronic"),
    ("Spironolactone", "SPIRONOLACTONE", "IR", [], ["OD"], [30, 90], "chronic"),
    ("Allopurinol", "ALLOPURINOL", "IR", [], ["OD"], [30, 90], "chronic"),
    ("Montelukast", "MONTELUKAST", "IR", ["Singulair"], ["QHS"], [30, 90], "chronic"),
    ("Clopidogrel", "CLOPIDOGREL", "IR", ["Plavix"], ["OD"], [30, 90], "chronic"),
    ("Hydroxyzine", "HYDROXYZINE", "IR", ["Atarax"], ["QHS", "TID"], [30], "prn"),
    ("Hydralazine", "HYDRALAZINE", "IR", [], ["TID"], [30], "chronic"),
    ("Quetiapine", "QUETIAPINE", "IR", ["Seroquel"], ["QHS"], [30], "chronic"),
    ("Risperidone", "RISPERIDONE", "IR", [], ["BID", "QHS"], [30], "chronic"),
    ("Lorazepam", "LORAZEPAM", "IR", [], ["BID", "QHS"], [15, 30], "prn"),
    ("Clonazepam", "CLONAZEPAM", "IR", [], ["BID"], [30], "chronic"),
    ("Oxycodone", "OXYCODONE", "IR", [], ["Q6H"], [5, 7], "prn"),
    ("Salbutamol", "SALBUTAMOL", "IR", ["Ventolin"], ["QID"], [30], "inhaler"),
]
LOOKALIKES = {"HYDROXYZINE", "HYDRALAZINE", "METFORMIN", "METRONIDAZOLE", "CLONAZEPAM", "LORAZEPAM"}

FIRST = ["Olivia", "Liam", "Emma", "Noah", "Charlotte", "William", "Amelia", "Benjamin", "Sophia", "Lucas", "Mia",
         "Ethan", "Harper", "Jacob", "Evelyn", "Aiden", "Chloe", "Daniel", "Aria", "Owen", "Priya", "Arjun", "Mei",
         "Wei", "Fatima", "Omar", "Sofia", "Mateo", "Isabelle", "Gabriel", "Nadia", "Hiroshi", "Ana", "Tariq",
         "Grace", "Samuel", "Leah", "Marc", "Josee", "Kwame"]
LAST = ["Smith", "Brown", "Tremblay", "Martin", "Roy", "Wilson", "MacDonald", "Gagnon", "Johnson", "Taylor",
        "Campbell", "Anderson", "Leblanc", "Lee", "Patel", "Singh", "Nguyen", "Chen", "Wong", "Khan", "Ali",
        "Kowalski", "Rossi", "Silva", "Murphy", "Gill", "Fraser", "Bouchard", "Cote", "Mensah", "Haddad",
        "Kim", "Park", "Ibrahim", "Novak", "Okafor", "Fernandes", "Dubois", "Sharma", "Walsh"]
CLINICS = ["Maple Grove Family Health", "Lakeshore Medical Clinic", "Riverside Health Centre",
           "Northview Family Practice", "Harbourfront Walk-In Clinic", "Cedar Park Medical", "Bayview Health Team"]
STREETS = ["Queen St W", "King St E", "Dundas St W", "Bloor St W", "Yonge St", "Danforth Ave", "College St"]


def _font(name, size):
    return ImageFont.truetype(str(FONT_DIR / name), size)


def _groups_for(base, release):
    groups = {}
    for p in dpd.single_ingredient_index().get(base, []):
        if p["release"] != release or p["form_family"] not in ("tablet", "capsule", "inhaler"):
            continue
        groups.setdefault(dpd.group_key(p), []).append(p)
    return groups


def _strength_text(value, unit, rng):
    v = int(value) if float(value).is_integer() else value
    u = {"MG": "mg", "MCG": "mcg"}.get(unit.upper(), unit.lower())
    return rng.choice([f"{v} {u}", f"{v}{u}", f"{v} {u.upper()}"])


def _num_text(n):
    return str(int(n)) if float(n).is_integer() else str(n)


def _pharmacy_pick(group_products):
    """The DIN this simulated pharmacy usually dispenses for a product group (stable choice)."""
    dins = sorted(p["din"] for p in group_products)
    h = int(hashlib.md5("|".join(dins).encode()).hexdigest(), 16)
    return dins[h % len(dins)]


def make_record(i, rng, style):
    name, base, release, brands, freqs, ds_opts, kind = rng.choice(POOL)
    groups = _groups_for(base, release)
    gkey = rng.choice(sorted(groups, key=str))
    prods = groups[gkey]
    p0 = prods[0]
    ing = p0["ingredients"][0]
    form = p0["form_family"]
    unit = {"tablet": "tablet", "capsule": "capsule", "inhaler": "puff"}[form]
    traps, issues = [], {}

    drug_text = name
    if brands and rng.random() < 0.3:
        drug_text = rng.choice(brands)
        traps.append("brand_name")
    if base in LOOKALIKES:
        traps.append("lookalike_name")

    strength_val, strength_unit = ing["strength"], ing["unit"]
    acceptable = sorted(p["din"] for p in prods)
    pharmacy_din = _pharmacy_pick(prods)
    group_id = json.dumps(gkey)
    if rng.random() < 0.05 and form != "inhaler":
        marketed = {k[1] for k in groups}  # strengths in mg for this drug, form family and release
        for mult in (1.5, 0.6, 3, 0.75):
            cand = strength_val * mult
            cand = round(cand) if cand >= 5 else round(cand, 1)
            if cand > 0 and round(dpd.to_mg(cand, strength_unit), 6) not in marketed:
                strength_val = float(cand)
                traps.append("nonexistent_strength")
                issues.setdefault("strength", []).append("STRENGTH_NOT_MARKETED")
                acceptable, pharmacy_din, group_id = [], "", ""
                break
    strength_text = _strength_text(strength_val, strength_unit, rng)
    if strength_val < 1 and rng.random() < 0.6:
        strength_text = f".{str(strength_val).split('.')[1]} {strength_unit.lower()}"
        traps.append("naked_decimal")
        issues.setdefault("strength", []).append("ISMP_NAKED_DECIMAL")
    elif float(strength_val).is_integer() and strength_val < 100 and rng.random() < 0.07:
        strength_text = f"{int(strength_val)}.0 {strength_unit.lower()}"
        traps.append("trailing_zero")
        issues.setdefault("strength", []).append("ISMP_TRAILING_ZERO")

    # directions, quantity, days supply, refills
    freq = rng.choice(freqs)
    if form == "inhaler":
        dose, freq, prn = 2, "QID", True
    else:
        # two units per dose only where that is common practice
        two_ok = (base, strength_val) in {("METFORMIN", 500.0), ("GABAPENTIN", 100.0), ("GABAPENTIN", 300.0),
                                          ("FUROSEMIDE", 20.0), ("HYDROXYZINE", 10.0), ("HYDROXYZINE", 25.0),
                                          ("LORAZEPAM", 0.5), ("ACETAMINOPHEN", 325.0)}
        dose = 2 if (two_ok and rng.random() < 0.4) else 1
        prn = kind == "prn"
    if rng.random() < 0.07 and freq in ("OD",):
        freq = "QD"
        traps.append("qd_abbreviation")
        issues.setdefault("sig", []).append("ISMP_QD")
    ds_choice = rng.choice(ds_opts)
    duration = ds_choice if kind == "antibiotic" else None
    sig_style = "short" if (style != "printed" or rng.random() < 0.4) else "plain"
    route = "INH" if form == "inhaler" else "PO"
    sig_text = write_sig(dose, unit, freq, prn=prn, duration_days=duration, style=sig_style, route=route)
    per_day = FREQ[freq][0]
    if form == "inhaler":
        qty, ds = 1, 25
    elif prn:
        qty = rng.choice([15, 20, 30, 60])
        ds = ds_choice
    else:
        qty = int(math.ceil(dose * per_day * ds_choice))
        ds = ds_choice
    refills = 0 if kind in ("antibiotic", "prn") or base in ("OXYCODONE",) else rng.choice([0, 1, 2, 3, 5])
    qty_auth = qty * (refills + 1)
    ds_written = prn or form == "inhaler" or rng.random() < (0.45 if style == "printed" else 0.25)
    if not prn and form != "inhaler" and rng.random() < 0.05:
        ds = ds * 3 if ds < 60 else ds // 3
        ds_written = True
        traps.append("ds_mismatch")
        issues.setdefault("ds", []).append("QTY_DS_MISMATCH")
    if p0["controlled"]:
        issues.setdefault("drug_name", []).append("CONTROLLED_SUBSTANCE")

    # people
    pf, pl = rng.choice(FIRST), rng.choice(LAST)
    df, dl = rng.choice(FIRST), rng.choice(LAST)
    dob = date(1935, 1, 1) + timedelta(days=rng.randrange(0, 365 * 75))
    dob_label = dob.isoformat()
    if rng.random() < 0.08:
        dob_text, dob_label = "", ""
        traps.append("dob_absent")
    elif dob.day <= 12 and dob.day != dob.month and rng.random() < 0.35:
        dob_text = f"{dob.day:02d}/{dob.month:02d}/{dob.year}"
        traps.append("ambiguous_dob")
        issues.setdefault("patient_dob", []).append("AMBIGUOUS_DATE")
    else:
        dob_text = rng.choice([dob.isoformat(), dob.strftime("%b %d, %Y").replace(" 0", " "),
                               dob.strftime("%d %b %Y").lstrip("0")] +
                              ([f"{dob.day:02d}/{dob.month:02d}/{dob.year}"] if dob.day > 12 else []))
    license_no = str(rng.randrange(10000, 999999))
    license_label = license_no
    if rng.random() < 0.10:
        license_label = ""
        traps.append("license_absent")

    if style != "printed" and rng.random() < 0.15:
        traps.append("smudge_strength")
    qty_text = rng.choice([f"Mitte: {qty}", f"Qty: {qty}", f"Disp: {qty}", f"#{qty}"])
    if form == "inhaler":
        qty_text = rng.choice(["Mitte: 1 inhaler", "Qty: 1", "Disp: 1 puffer"])
    elif p0["controlled"]:
        qty_text = f"Qty: {qty} ({_words(qty)})"
    if refills == 0:
        ref_text = rng.choice(["Refills: 0", "No refills", "NR"])
    else:
        ref_text = rng.choice([f"Refills: {refills}", f"Rpt x{refills}", f"Repeats: {refills}"])
    form_text = rng.choice(["", "", {"tablet": "tab", "capsule": "cap", "inhaler": "inhaler"}[form],
                            {"tablet": "tablet", "capsule": "capsule", "inhaler": "MDI"}[form]])

    label = {
        "drug_name": drug_text, "strength": f"{_num_text(strength_val)} {strength_unit.lower()}", "form": form,
        "qty": str(qty), "qty_auth": str(qty_auth), "ds": str(ds), "sig": sig_text,
        "patient_first": pf, "patient_last": pl, "patient_dob": dob_label,
        "prescriber_first": df, "prescriber_last": dl, "prescriber_license": license_label,
    }
    page = dict(drug_text=drug_text, strength_text=strength_text, form_text=form_text, sig_text=sig_text,
                qty_text=qty_text, ref_text=ref_text, ds_text=(f"Days supply: {ds}" if ds_written else ""),
                patient=f"{pf} {pl}", dob_text=dob_text, prescriber=f"Dr. {df} {dl}",
                license_text=(rng.choice([f"CPSO #{license_no}", f"Lic. {license_no}", f"Reg. No. {license_no}"])
                              if license_label else ""),
                clinic=rng.choice(CLINICS), street=f"{rng.randrange(10, 999)} {rng.choice(STREETS)}",
                phone=f"416-555-{rng.randrange(1000, 9999)}", fax=f"416-555-{rng.randrange(1000, 9999)}",
                rx_date=(date(2026, 1, 5) + timedelta(days=rng.randrange(0, 300))).strftime("%Y-%m-%d"))
    return {
        "id": f"rx{i:04d}", "style": style, "traps": traps, "issues": issues, "label": label,
        "sig_struct": {"dose": float(dose), "unit": unit, "route": route, "per_day": per_day, "prn": prn},
        "refills": refills, "ds_written": bool(ds_written), "group": group_id,
        "acceptable_dins": acceptable, "pharmacy_din": pharmacy_din, "controlled": p0["controlled"],
        "group_dins": list(acceptable),
        "group_products": [[p["din"], dpd.norm_brand(p["brand"])] for p in prods] if acceptable else [],
        "patient_key": patient_key(pf, pl, dob_label),
        "page": page,
    }


def patient_key(first, last, dob):
    """How the simulated pharmacy finds a patient's profile: name + date of birth."""
    return f"{_plain(first)}|{_plain(last)}|{dob or ''}"


def _plain(s):
    import re
    return re.sub(r"[^a-z]", "", (s or "").lower())


def assign_din_basis(rec, rng, pharmacy_known, patient_hist):
    """Decide which DIN the pharmacist should end up with, and why. The page and both histories follow from it.
    no_substitution: prescriber wrote a brand + "No substitution" -> that brand's DIN only.
    patient_last_fill: this patient had this product before -> same maker again.
    pharmacy_usual: the pharmacy's usual DIN for this product (from its past selections).
    none: no signal -> the pharmacist picks; a confident pre-selection would be a guess.
    not_marketed: the strength is not sold -> nothing should be pre-selected."""
    if not rec["group"]:
        rec.update(din_basis="not_marketed", pharmacy_din="")
        return
    drug_brand = dpd.norm_brand(rec["page"]["drug_text"])
    brand_dins = [d for d, b in rec["group_products"] if b == drug_brand or b.split()[:1] == drug_brand.split()[:1]]
    if "brand_name" in rec["traps"] and brand_dins and rng.random() < 0.8:
        rec["traps"].append("no_substitution")
        rec["page"]["nosub_text"] = rng.choice(["No substitution", "No sub", "Do not substitute"])
        rec.update(din_basis="no_substitution", pharmacy_din=brand_dins[0], acceptable_dins=brand_dins)
        return
    usual = rec["pharmacy_din"]
    if rng.random() < 0.25:
        others = [d for d in rec["group_dins"] if d != usual] or rec["group_dins"]
        last = rng.choice(others)
        patient_hist.setdefault(rec["patient_key"], {})[rec["group"]] = last
        rec["traps"].append("patient_refill_other_maker" if last != usual else "patient_refill")
        rec.update(din_basis="patient_last_fill", pharmacy_din=last)
        return
    if rec["group"] not in pharmacy_known:
        pharmacy_known[rec["group"]] = usual if rng.random() < 0.8 else None
    if pharmacy_known[rec["group"]]:
        rec.update(din_basis="pharmacy_usual", pharmacy_din=usual)
    else:
        rec.update(din_basis="none", pharmacy_din="")


def _words(n):
    ones = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    tens = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + ("" if n % 10 == 0 else "-" + ones[n % 10])
    rest = n % 100
    return ones[n // 100] + " hundred" + ("" if rest == 0 else " " + _words(rest))


# ---------------- rendering ----------------
W, H = 1275, 1650  # 8.5 x 11 in at 150 dpi


def _hand_text(img, xy, text, rng, size=44, color=(20, 30, 110)):
    """Draw text letter by letter with random size, slant and baseline, like quick handwriting. Returns bbox."""
    x, y = xy
    x0, y0, x1, y1 = x, y, x, y
    word_tilt = 0.0
    for ch in text:
        if ch == " ":
            x += int(size * rng.uniform(0.35, 0.6))
            word_tilt = rng.uniform(-6, 6)
            continue
        f = _font("Inkfree.ttf", int(size * rng.uniform(0.85, 1.15)))
        bb = f.getbbox(ch)
        tile = Image.new("RGBA", (max(bb[2], 4) + 10, f.getbbox("Hg")[3] + 14), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((5, 5), ch, font=f, fill=color + (255,))
        tile = tile.rotate(word_tilt + rng.uniform(-7, 7), expand=True, resample=Image.BICUBIC)
        dy = int(rng.uniform(-5, 5))
        img.paste(tile, (x, y + dy), tile)
        x1, y1 = max(x1, x + tile.width), max(y1, y + dy + tile.height)
        x += max(bb[2] - bb[0], 6) + int(rng.uniform(-3, 4))
    return [x0, y0, x1, y1]


def _print_text(draw, xy, text, size=30, bold=False):
    f = _font("arialbd.ttf" if bold else "arial.ttf", size)
    draw.text(xy, text, font=f, fill=(0, 0, 0))
    b = draw.textbbox(xy, text, font=f)
    return list(b)


def render(rec, rng):
    pg, style = rec["page"], rec["style"]
    base_style = style if style != "fax" else rng.choice(["printed", "handwritten"])
    bg = (255, 255, 255) if base_style == "printed" else (250, 248, 240)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    boxes = {}
    # printed header (always printed, like a clinic pad or software header)
    _print_text(d, (80, 60), pg["clinic"], 40, bold=True)
    _print_text(d, (80, 115), f"{pg['street']}, Toronto, ON", 24)
    _print_text(d, (80, 148), f"Tel {pg['phone']}   Fax {pg['fax']}", 24)
    boxes["prescriber"] = _print_text(d, (80, 200), pg["prescriber"], 30, bold=True)
    if pg["license_text"]:
        boxes["prescriber_license"] = _print_text(d, (80, 240), pg["license_text"], 26)
    d.line([(80, 300), (W - 80, 300)], fill=(0, 0, 0), width=3)
    if base_style == "printed":
        y = 330
        boxes["patient"] = _print_text(d, (80, y), f"Patient: {pg['patient']}", 30)
        if pg["dob_text"]:
            boxes["patient_dob"] = _print_text(d, (700, y), f"DOB: {pg['dob_text']}", 30)
        _print_text(d, (80, y + 50), f"Date: {pg['rx_date']}", 28)
        _print_text(d, (80, 470), "Rx", 64, bold=True)
        y = 570
        boxes["drug_name"] = _print_text(d, (120, y), pg["drug_text"], 36, bold=True)
        boxes["strength"] = _print_text(d, (boxes["drug_name"][2] + 20, y), pg["strength_text"], 36, bold=True)
        if pg["form_text"]:
            _print_text(d, (boxes["strength"][2] + 20, y), pg["form_text"], 36)
        if pg.get("nosub_text"):
            boxes["no_substitution"] = _print_text(d, (700, y + 250), pg["nosub_text"], 30, bold=True)
        boxes["sig"] = _print_text(d, (120, y + 70), "Sig: " + pg["sig_text"], 32)
        boxes["qty"] = _print_text(d, (120, y + 130), pg["qty_text"], 32)
        boxes["refills"] = _print_text(d, (560, y + 130), pg["ref_text"], 32)
        if pg["ds_text"]:
            boxes["ds"] = _print_text(d, (120, y + 190), pg["ds_text"], 32)
        _print_text(d, (700, 1250), "Signature:", 26)
        _hand_text(img, (840, 1225), "~" + pg["prescriber"].split()[-1], rng, size=48, color=(0, 0, 0))
    else:
        y = 330
        _print_text(d, (80, y + 8), "Name:", 26)
        boxes["patient"] = _hand_text(img, (170, y - 10), pg["patient"], rng)
        if pg["dob_text"]:
            _print_text(d, (700, y + 8), "DOB:", 26)
            boxes["patient_dob"] = _hand_text(img, (770, y - 10), pg["dob_text"], rng)
        _print_text(d, (80, y + 70), "Date:", 26)
        _hand_text(img, (170, y + 52), pg["rx_date"], rng, size=36)
        _print_text(d, (80, 470), "Rx", 64, bold=True)
        y = 560
        boxes["drug_name"] = _hand_text(img, (130, y), pg["drug_text"], rng, size=52)
        boxes["strength"] = _hand_text(img, (boxes["drug_name"][2] + 25, y), pg["strength_text"], rng, size=52)
        if pg["form_text"]:
            _hand_text(img, (boxes["strength"][2] + 25, y), pg["form_text"], rng, size=48)
        if pg.get("nosub_text"):
            boxes["no_substitution"] = _hand_text(img, (700, y + 260), pg["nosub_text"], rng, size=46)
        boxes["sig"] = _hand_text(img, (130, y + 90), pg["sig_text"], rng, size=48)
        boxes["qty"] = _hand_text(img, (130, y + 180), pg["qty_text"], rng, size=46)
        boxes["refills"] = _hand_text(img, (600, y + 180), pg["ref_text"], rng, size=46)
        if pg["ds_text"]:
            boxes["ds"] = _hand_text(img, (130, y + 260), pg["ds_text"], rng, size=44)
        _print_text(d, (700, 1250), "Signature:", 26)
        _hand_text(img, (840, 1225), "~" + pg["prescriber"].split()[-1], rng, size=48)
    _print_text(d, (80, H - 90), "This prescription is valid for one year from the date written.", 20)
    if "smudge_strength" in rec["traps"] and "strength" in boxes:
        x0, y0, x1, y1 = boxes["strength"]
        blot = Image.new("RGBA", img.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(blot)
        cx = x0 + (x1 - x0) * rng.uniform(0.15, 0.4)
        bd.ellipse([cx - 22, y0 + 5, cx + 22, y1 - 5], fill=(60, 60, 70, 150))
        img = Image.alpha_composite(img.convert("RGBA"), blot.filter(ImageFilter.GaussianBlur(4))).convert("RGB")
    if style == "fax":
        img = fax_degrade(img, rng, pg)
    elif base_style == "handwritten":
        img = img.rotate(rng.uniform(-1.2, 1.2), fillcolor=bg, resample=Image.BICUBIC)
    return img, boxes


def fax_degrade(img, rng, pg):
    """Standard-resolution fax look: coarse vertical resolution, hard black/white, noise, skew, streaks."""
    g = img.convert("L")
    g = g.resize((int(W * 0.5), int(H * 0.25)), Image.BILINEAR)       # ~100 x 50 dpi fax rows
    g = g.point(lambda v: 0 if v < rng.choice([165, 175, 185]) else 255)
    g = g.resize((W, H), Image.NEAREST)
    g = g.filter(ImageFilter.GaussianBlur(0.6)).point(lambda v: 0 if v < 140 else 255)
    px = g.load()
    for _ in range(6000):  # speckles
        x, y = rng.randrange(W), rng.randrange(H)
        px[x, y] = 0
    d = ImageDraw.Draw(g)
    for _ in range(rng.choice([1, 2, 3])):
        yy = rng.randrange(400, H - 100)
        d.line([(0, yy), (W, yy)], fill=0, width=rng.choice([1, 2]))
    d.text((20, 10), f"FROM: {pg['fax']}   {pg['rx_date']} 09:{rng.randrange(10, 59)}   P.001/001",
           font=_font("cour.ttf", 22), fill=0)
    g = g.rotate(rng.uniform(-2.0, 2.0), fillcolor=255)
    return g.convert("RGB")


def generate(n=150, seed=20261118, out_dir=OUT_DIR):
    rng = random.Random(seed)
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    styles = (["printed", "handwritten", "fax"] * (n // 3 + 1))[:n]
    rng.shuffle(styles)
    recs = []
    pharmacy_known, patient_hist = {}, {}
    for i, st in enumerate(styles):
        rec = make_record(i, rng, st)
        assign_din_basis(rec, rng, pharmacy_known, patient_hist)
        img, boxes = render(rec, rng)
        img.save(img_dir / f"{rec['id']}.png", optimize=True)
        rec["boxes"] = boxes
        recs.append(rec)
    with open(out_dir / "labels.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    # Simulated past selections ("DIN selected" in correction logs) and patients' last fills.
    pharmacy = {g: d for g, d in pharmacy_known.items() if d}
    for r in recs:  # groups seen only through patient refills still get a pharmacy habit 80% of the time
        if r["group"] and r["group"] not in pharmacy_known and rng.random() < 0.8:
            pharmacy[r["group"]] = _pharmacy_pick([{"din": d} for d in r["group_dins"]])
    (out_dir / "pharmacy_history.json").write_text(json.dumps(pharmacy, indent=1))
    (out_dir / "patient_history.json").write_text(json.dumps(patient_hist, indent=1))
    return recs


def load_history(out_dir=OUT_DIR):
    return dict(pharmacy=json.loads((out_dir / "pharmacy_history.json").read_text()),
                patients=json.loads((out_dir / "patient_history.json").read_text()))


def load_labels(out_dir=OUT_DIR):
    return [json.loads(l) for l in (out_dir / "labels.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
