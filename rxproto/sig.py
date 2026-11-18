"""Directions (sig): write them for synthetic prescriptions, and parse them back into parts.

Parsed form: dose (number), unit (tablet/capsule/puff), route, per_day (doses per day), prn (bool).
The parser handles both pharmacy shorthand ("1 tab PO BID") and plain English
("Take 1 tablet by mouth twice daily").
"""
import re

FREQ = {  # code -> (per_day, shorthand, plain English)
    "OD": (1, "OD", "once daily"),
    "DAILY": (1, "daily", "once a day"),
    "QD": (1, "QD", "once daily"),           # ISMP "Do Not Use"
    "BID": (2, "BID", "twice daily"),
    "TID": (3, "TID", "three times daily"),
    "QID": (4, "QID", "four times daily"),
    "QHS": (1, "QHS", "at bedtime"),
    "Q12H": (2, "q12h", "every 12 hours"),
    "Q8H": (3, "q8h", "every 8 hours"),
    "Q6H": (4, "q6h", "every 6 hours"),
}
UNIT_WORDS = {
    "tablet": ["tablets", "tablet", "tabs", "tab", "t"],
    "capsule": ["capsules", "capsule", "caps", "cap", "c"],
    "puff": ["puffs", "puff", "inhalations", "inhalation"],
}
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "half": 0.5, "one-half": 0.5, "½": 0.5}


def write_sig(dose, unit, freq_code, prn=False, duration_days=None, style="short", route="PO"):
    per_day, short, plain = FREQ[freq_code]
    d = int(dose) if float(dose).is_integer() else dose
    if style == "short":
        u = {"tablet": "tab", "capsule": "cap", "puff": "puffs" if d != 1 else "puff"}[unit]
        r = {"PO": "PO", "INH": "INH"}[route]
        s = f"{d} {u} {r} {short}"
        if prn:
            s += " PRN"
        if duration_days:
            s += f" x {duration_days} days"
        return s
    u = unit + ("s" if d != 1 else "")
    r = {"PO": "by mouth", "INH": "by inhalation"}[route]
    s = f"Take {d} {u} {r} {plain}"
    if prn:
        s += " as needed"
    if duration_days:
        s += f" for {duration_days} days"
    return s


def parse_sig(text):
    """Return dict(dose, unit, route, per_day, prn) or None if the core parts cannot be found."""
    if not text:
        return None
    t = " " + text.lower().replace(".", " ").replace(",", " ") + " "
    t = re.sub(r"\s+", " ", t)
    for w, n in NUM_WORDS.items():
        t = re.sub(rf"\b{w}\b", str(n), t)
    t = re.sub(r"\b(iii|ii|i)\s+(?=tab|cap|puff)", lambda m: f"{len(m.group(1))} ", t)
    prn = bool(re.search(r"\bprn\b|as needed|when needed|if needed", t))
    unit = None
    dose = None
    for u, words in UNIT_WORDS.items():
        m = re.search(r"(\d+(?:\s*/\s*\d+)?(?:\.\d+)?)\s*(" + "|".join(words) + r")\b", t)
        if m:
            raw = m.group(1).replace(" ", "")
            dose = float(eval(raw)) if "/" in raw else float(raw)  # "1/2" only digits and slash
            unit = u
            break
    per_day = None
    checks = [
        (r"\bq\s?6\s?h\b|every 6 hours|\b4 times (a day|daily|per day)|\bqid\b", 4),
        (r"\bq\s?8\s?h\b|every 8 hours|\b3 times (a day|daily|per day)|\btid\b", 3),
        (r"\bq\s?12\s?h\b|every 12 hours|twice (a day|daily)|\b2 times (a day|daily|per day)|\bbid\b", 2),
        (r"\bqhs\b|\bhs\b|at bedtime|once (a day|daily)|\bdaily\b|\bod\b|\bqd\b|every day|each day|\bqam\b|in the morning", 1),
    ]
    for pat, n in checks:
        if re.search(pat, t):
            per_day = n
            break
    route = "INH" if re.search(r"\binh\b|inhal|puff", t) else ("PO" if re.search(r"\bpo\b|by mouth|orally|\boral\b", t) else None)
    if route is None and unit in ("tablet", "capsule"):
        route = "PO"
    if dose is None or per_day is None:
        return None
    return dict(dose=dose, unit=unit, route=route, per_day=per_day, prn=prn)


def same_meaning(a, b):
    """Two parsed sigs mean the same thing."""
    if a is None or b is None:
        return False
    return (abs(a["dose"] - b["dose"]) < 1e-9 and a["unit"] == b["unit"] and a["per_day"] == b["per_day"]
            and a["prn"] == b["prn"] and (a["route"] or "PO") == (b["route"] or "PO"))


def ismp_flags(text):
    """ISMP Canada 'Do Not Use' items found in free text (sig or strength)."""
    out = []
    if not text:
        return out
    if re.search(r"\bq\.?\s?d\.?\b", text, re.I):
        out.append("ISMP: 'QD' (use 'daily')")
    if re.search(r"\d+\.0\b(?!\d)", text):
        out.append("ISMP: trailing zero (e.g. '5.0 mg')")
    if re.search(r"(?<![\d])\.\d", text):
        out.append("ISMP: no leading zero (e.g. '.5 mg')")
    if re.search(r"\d\s*U\b", text):
        out.append("ISMP: 'U' for units")
    return out
