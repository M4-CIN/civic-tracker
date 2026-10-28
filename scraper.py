import json, os, re, sys, time, unicodedata, datetime as dt
from pathlib import Path
import requests

URL = ("https://www.otomoto.pl/osobowe/honda/civic/od-2018?search%5Bfilter_enum_damaged%5D=0"
       "&search%5Bfilter_enum_fuel_type%5D%5B0%5D=petrol&search%5Bfilter_enum_fuel_type%5D%5B1%5D=petrol-lpg"
       "&search%5Bfilter_enum_gearbox%5D=manual&search%5Bfilter_float_engine_capacity%3Afrom%5D=1400"
       "&search%5Bfilter_float_engine_capacity%3Ato%5D=1900&search%5Bfilter_float_mileage%3Ato%5D=200000"
       "&search%5Bfilter_float_price%3Ato%5D=70000&search%5Badvanced_search_expanded%5D=true")
DATA = Path("docs/data.json")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Accept-Language": "pl-PL,pl;q=0.9"}
today = dt.date.today().isoformat()
NEXT = r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>'
VIN_RX = re.compile(r"\bvin\b.{0,60}?\b([a-hj-npr-z0-9]{17})\b", re.I)
MAX_DETAIL, calls = 60, 0  # tyle podstron ogłoszeń na jeden przebieg
TRIMS = [("Prestige", r"prestig"), ("Executive", r"execut"), ("Sport Plus", r"sport\s*plus"),
         ("Sport", r"sport\b"), ("Elegance", r"eleganc"), ("Comfort", r"(?:comfort|komfort)"),
         ("Lifestyle", r"life\s*style"), ("Type R", r"type\s*r\b")]


def find(o, key):
    """Szuka klucza w całej strukturze (także w zagnieżdżonych stringach JSON)."""
    if isinstance(o, str) and o.startswith("{") and key in o:
        try:
            return find(json.loads(o), key)
        except ValueError:
            return None
    if isinstance(o, dict):
        if key in o:
            return o[key]
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = find(v, key)
            if r is not None:
                return r


def clean(text):
    """Małe litery i ujednolicone znaki: 'Sport-Plus', 'SPORT+' i 'sport  plus' wyglądają tak samo."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", " ", re.sub(r"[-_/]", " ", t.replace("+", " plus ")))


def trim_of(text, desc=False):
    """W opisie samo 'sport' to często tryb jazdy, więc liczy się tylko 'wersja Sport' / 'Turbo Sport'."""
    text = clean(text)
    for name, rx in TRIMS:
        if desc and name == "Sport":
            rx = r"(?:wersj\w*|turbo) sport\b"
        if re.search(r"\b" + rx, text):
            return name


def page(n):
    r = requests.get(URL + (f"&page={n}" if n > 1 else ""), headers=H, timeout=30)
    r.raise_for_status()
    m = re.search(NEXT, r.text, re.S)
    if not m:
        raise RuntimeError("Brak __NEXT_DATA__ (blokada albo zmiana strony)")
    s = find(json.loads(m.group(1)), "advertSearch") or {}
    return [e["node"] for e in s.get("edges", [])]


def norm(n):
    p = {x["key"]: x.get("value") for x in n.get("parameters", [])}
    pr = (n.get("price") or {}).get("amount") or {}
    to_int = lambda v: int(float(v)) if v not in (None, "") else None
    trim, src = "?", None  # kolejność: tytuł → pole „wersja" → krótki opis
    for label, txt, desc in (("tytuł", n.get("title"), False), ("wersja", p.get("version"), False),
                             ("opis", n.get("shortDescription"), True)):
        t = trim_of(txt, desc)
        if t:
            trim, src = t, label
            break
    return dict(id=str(n["id"]), title=n.get("title"), url=n.get("url"), version=p.get("version"),
                trim=trim, trim_src=src, price=to_int(pr.get("value")), year=to_int(p.get("year")),
                mileage=to_int(p.get("mileage")), engine=p.get("engine_capacity"), fuel=p.get("fuel_type"),
                city=((n.get("location") or {}).get("city") or {}).get("name"))


def enrich(l):
    """Wchodzi na stronę ogłoszenia: czyta VIN i (gdy brak wersji) pełny opis. False = limit wyczerpany."""
    global calls
    if not l.get("url") or calls >= MAX_DETAIL:
        return False
    calls += 1
    time.sleep(1.5)
    l["vin_tries"] = l.get("vin_tries", 0) + 1
    try:
        t = requests.get(l["url"], headers=H, timeout=30).text
    except requests.RequestException:
        return True
    for m in VIN_RX.finditer(t):
        v = m.group(1).upper()
        if re.search(r"\d", v) and re.search(r"[A-Z]", v):
            l["vin"] = v
            break
    if l.get("trim", "?") == "?":
        m = re.search(NEXT, t, re.S)
        try:
            adv = find(json.loads(m.group(1)), "advert") if m else None
        except ValueError:
            adv = None
        if isinstance(adv, dict):
            tr = trim_of(re.sub(r"<[^>]+>", " ", adv.get("description") or ""), True)
            if tr:
                l["trim"], l["trim_src"] = tr, "opis"
    return True


def link_by_vin():
    """Ten sam VIN pod innym ID = to samo auto wystawione ponownie. Działa też, gdy VIN dociągnie się dopiero następnego dnia."""
    by = {}
    for l in db.values():
        if l.get("vin"):
            by.setdefault(l["vin"], []).append(l)
    for ls in by.values():
        ls.sort(key=lambda l: (l["first_seen"], l["id"]))
        for prev, cur in zip(ls, ls[1:]):
            if cur.get("relisted_from"):
                continue
            cur.update(relisted_from=prev["id"], relisted_by="VIN", tag="relisted", tag_date=today,
                       since=prev.get("since") or prev["first_seen"], history=prev["history"] + cur["history"])
            prev["relisted_as"] = cur["id"]


db = json.loads(DATA.read_text())["items"] if DATA.exists() else {}
items = {}
for n in range(1, 40):
    try:
        nodes = page(n)
    except Exception as e:
        if n == 1:
            sys.exit(f"Błąd pobierania: {e}")
        break
    fresh = [norm(x) for x in nodes if str(x["id"]) not in items]
    if not fresh:
        break
    items.update({a["id"]: a for a in fresh})
    time.sleep(2)

prev_active = sum(1 for l in db.values() if l.get("active"))
if not items or len(items) < 0.5 * prev_active:
    sys.exit(f"Podejrzanie mało wyników ({len(items)} vs {prev_active}), nic nie zapisuję")

for i, a in items.items():
    if i in db:
        l = db[i]
        if a["trim"] == "?":  # nie nadpisuj wersji znalezionej wcześniej w opisie
            a["trim"], a["trim_src"] = l.get("trim", "?"), l.get("trim_src")
        if a["price"] != l["history"][-1][1]:
            l["history"].append([today, a["price"]])
            l["tag"], l["tag_date"] = "price", today
        l.update(a, active=True, last_seen=today)
        l.pop("gone_date", None)
    else:
        l = dict(a, first_seen=today, last_seen=today, active=True,
                 history=[[today, a["price"]]], tag="new", tag_date=today)
        db[i] = l
        enrich(l)

for l in db.values():
    if l["id"] not in items and l.get("active"):
        l["active"], l["gone_date"] = False, today

for l in db.values():  # dociąganie brakujących VIN-ów i wersji (max 3 próby na ogłoszenie)
    if calls >= MAX_DETAIL:
        break
    if l.get("active") and (not l.get("vin") or l.get("trim", "?") == "?") and l.get("vin_tries", 0) < 3:
        enrich(l)

link_by_vin()

cnt = lambda t: sum(1 for l in db.values() if l.get("active") and l.get("tag") == t and l.get("tag_date") == today)
vins = sum(1 for l in db.values() if l.get("active") and l.get("vin"))
msg = f"Nowe: {cnt('new')}, zmiany ceny: {cnt('price')}, ponownie: {cnt('relisted')}, aktywne: {len(items)}, z VIN: {vins}"
print(msg)
DATA.parent.mkdir(exist_ok=True)
DATA.write_text(json.dumps({"updated": today, "items": db}, ensure_ascii=False))
if os.getenv("NTFY_TOPIC"):
    requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}", data=msg.encode("utf-8"), headers={"Title": "Civic"})
