"""Health Canada Drug Product Database (DPD): load the public extract and look products up.

Source: Health Canada, Drug Product Database (Open Government Licence - Canada).
The extract (allfiles.zip, marketed products) is downloaded into data/dpd_raw/.
"""
import csv
import io
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_ZIP = ROOT / "data" / "dpd_raw" / "allfiles.zip"
PRODUCTS_JSON = ROOT / "data" / "dpd_products.json"
DPD_URL = "https://www.canada.ca/content/dam/hc-sc/documents/services/drug-product-database/allfiles.zip"

SALT_WORDS = {
    "HYDROCHLORIDE", "HCL", "SODIUM", "POTASSIUM", "CALCIUM", "BESYLATE", "BESILATE", "MALEATE",
    "TARTRATE", "SUCCINATE", "FUMARATE", "MESYLATE", "CITRATE", "HYDROBROMIDE", "SULFATE", "SULPHATE",
    "PHOSPHATE", "MONOHYDRATE", "DIHYDRATE", "TRIHYDRATE", "HEMIHYDRATE", "MAGNESIUM", "ACETATE",
    "DIHYDROCHLORIDE", "BROMIDE", "CHLORIDE", "ANHYDROUS", "HYCLATE", "BITARTRATE", "DISODIUM",
}
RELEASE_WORDS = r"\b(ER|XR|XL|SR|CR|LA|CD|XLA|ER-?TAB|EXTENDED|PROLONGED|SUSTAINED|CONTROLLED)\b"


def ingredient_base(name: str) -> str:
    """'ATORVASTATIN (ATORVASTATIN CALCIUM)' -> 'ATORVASTATIN'; 'METFORMIN HYDROCHLORIDE' -> 'METFORMIN'."""
    s = name.upper().split(" (")[0]
    s = re.sub(r"[^A-Z0-9 \-]", " ", s)
    words = [w for w in s.split() if w not in SALT_WORDS]
    return " ".join(words).strip()


def release_of(form_text: str, brand: str = "", descriptor: str = "") -> str:
    f = (form_text or "").upper()
    if "EXTENDED" in f or "SUSTAINED" in f or "CONTROLLED" in f or "PROLONGED" in f or "MODIFIED" in f:
        return "ER"
    if re.search(RELEASE_WORDS, f"{brand} {descriptor}".upper()):
        return "ER"
    return "IR"


def form_family(text: str) -> str:
    t = (text or "").lower()
    if "inhal" in t or "aerosol" in t or "puffer" in t or "metered" in t:
        return "inhaler"
    if "caps" in t or re.search(r"\bcap\b", t):
        return "capsule"
    if "tab" in t or "caplet" in t:
        return "tablet"
    if "cream" in t:
        return "cream"
    if "solution" in t or "suspension" in t or "syrup" in t or "liquid" in t:
        return "liquid"
    return t.strip()


def to_mg(value: float, unit: str):
    u = (unit or "").upper().replace("µ", "MC")
    if u in ("MG",):
        return value
    if u in ("MCG", "UG"):
        return value / 1000.0
    if u == "G":
        return value * 1000.0
    return None


def _read(z, name):
    return list(csv.reader(io.StringIO(z.read(name).decode("latin-1"))))


def download(zip_path: Path = RAW_ZIP):
    import urllib.request
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(DPD_URL, zip_path)


def build_products(zip_path: Path = RAW_ZIP, out: Path = PRODUCTS_JSON) -> int:
    """Turn the raw DPD extract into one JSON list of marketed human products."""
    if not zip_path.exists():
        download(zip_path)
    z = zipfile.ZipFile(zip_path)
    drugs = {}
    for r in _read(z, "drug.txt"):
        code, _cat, cls, din, brand, desc, _ped, _acc, n_ai, _upd, aig = r[:11]
        if cls != "Human":
            continue
        drugs[code] = dict(drug_code=code, din=din, brand=brand.strip(), descriptor=desc.strip(),
                           n_ai=int(n_ai or 0), aig=aig, ingredients=[], forms=[], routes=[],
                           schedules=[], status="", company="")
    for r in _read(z, "ingred.txt"):
        if r[0] in drugs:
            try:
                val = float(r[4])
            except ValueError:
                continue
            drugs[r[0]]["ingredients"].append(dict(name=r[2], base=ingredient_base(r[2]), strength=val, unit=r[5]))
    for fname, key, col in (("form.txt", "forms", 2), ("route.txt", "routes", 2), ("schedule.txt", "schedules", 1)):
        for r in _read(z, fname):
            if r[0] in drugs:
                drugs[r[0]][key].append(r[col])
    for r in _read(z, "status.txt"):
        if r[0] in drugs and r[1] == "Y":
            drugs[r[0]]["status"] = r[2]
    for r in _read(z, "comp.txt"):
        if r[0] in drugs:
            drugs[r[0]]["company"] = r[3] if len(r) > 3 else ""
    products = []
    for d in drugs.values():
        if d["status"] != "MARKETED" or not d["ingredients"]:
            continue
        form = d["forms"][0] if d["forms"] else ""
        d["form_family"] = form_family(form)
        d["release"] = release_of(form, d["brand"], d["descriptor"])
        d["controlled"] = any(("CDSA" in s) for s in d["schedules"])
        products.append(d)
    products.sort(key=lambda p: p["din"])
    out.write_text(json.dumps(products))
    return len(products)


@lru_cache(maxsize=1)
def products():
    if not PRODUCTS_JSON.exists():
        build_products()
    return json.loads(PRODUCTS_JSON.read_text())


def group_key(p):
    """Products in one group are interchangeable for a prescription: same single ingredient,
    same strength, same form family, same release type."""
    ing = p["ingredients"][0]
    mg = to_mg(ing["strength"], ing["unit"])
    return (ing["base"], round(mg, 6) if mg is not None else (ing["strength"], ing["unit"]), p["form_family"], p["release"])


@lru_cache(maxsize=1)
def single_ingredient_index():
    """base ingredient -> list of single-ingredient marketed products."""
    idx = {}
    for p in products():
        if p["n_ai"] == 1 and len(p["ingredients"]) == 1:
            idx.setdefault(p["ingredients"][0]["base"], []).append(p)
    return idx


@lru_cache(maxsize=1)
def brand_index():
    """normalised brand name (release words removed) -> set of ingredient bases."""
    idx = {}
    for p in products():
        if p["n_ai"] != 1:
            continue
        b = norm_brand(p["brand"])
        if b:
            idx.setdefault(b, set()).add(p["ingredients"][0]["base"])
            first = b.split()[0]
            # "VENTOLIN HFA" is also reachable as "VENTOLIN"; skip maker prefixes like "APO METFORMIN"
            if len(b.split()) > 1 and len(first) >= 5 and first not in MAKER_PREFIXES:
                idx.setdefault(first, set()).add(p["ingredients"][0]["base"])
    return idx


MAKER_PREFIXES = {"APO", "TEVA", "SANDOZ", "AURO", "JAMP", "MINT", "MAR", "PMS", "NRA", "RIVA", "ACT", "MYLAN",
                  "SANIS", "SIVEM", "PRO", "RAN", "DOM", "MED", "NOVO", "PHARMA", "GLENMARK", "ACCEL", "BIO",
                  "AG", "M", "NAT", "AA", "VAN", "ZYM", "TARO", "STRIDES", "REDDY", "ACH", "MANDY", "ATORVASTATIN"}


def norm_brand(s: str) -> str:
    s = re.sub(RELEASE_WORDS, " ", (s or "").upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(s.split())


def resolve_name(text: str):
    """Strict lookup used by the scorer and the simple baseline: generic or brand name -> (bases, release)."""
    t = (text or "").upper()
    release = "ER" if re.search(RELEASE_WORDS, t) else "IR"
    n = norm_brand(t)
    if not n:
        return set(), release
    idx = single_ingredient_index()
    base = ingredient_base(n)
    if base in idx:
        return {base}, release
    if n in brand_index():
        return set(brand_index()[n]), release
    return set(), release
