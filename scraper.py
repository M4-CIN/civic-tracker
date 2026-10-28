import json, os, re, sys, time, datetime as dt
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


def page(n):
    r = requests.get(URL + (f"&page={n}" if n > 1 else ""), headers=H, timeout=30)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("Brak __NEXT_DATA__ (blokada albo zmiana strony)")
    s = find(json.loads(m.group(1)), "advertSearch") or {}
    return [e["node"] for e in s.get("edges", [])]


def norm(n):
    p = {x["key"]: x.get("value") for x in n.get("parameters", [])}
    pr = (n.get("price") or {}).get("amount") or {}
    to_int = lambda v: int(float(v)) if v not in (None, "") else None
    return dict(id=str(n["id"]), title=n.get("title"), url=n.get("url"),
                price=to_int(pr.get("value")), year=to_int(p.get("year")),
                mileage=to_int(p.get("mileage")), engine=p.get("engine_capacity"),
                fuel=p.get("fuel_type"), city=((n.get("location") or {}).get("city") or {}).get("name"))


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

gone = {i: l for i, l in db.items() if i not in items and not l.get("relisted_as")}
for i, a in items.items():
    fp = f"{a['year']}|{a['mileage']}|{a['engine']}|{a['city']}" if a["mileage"] else None
    if i in db:
        l = db[i]
        if a["price"] != l["history"][-1][1]:
            l["history"].append([today, a["price"]])
            l["tag"], l["tag_date"] = "price", today
        l.update(a, fp=fp, active=True, last_seen=today)
        l.pop("gone_date", None)
    else:
        old = next((g for g in gone.values() if fp and g.get("fp") == fp), None)
        l = dict(a, fp=fp, first_seen=today, last_seen=today, active=True,
                 history=[[today, a["price"]]], tag="new", tag_date=today)
        if old:
            l.update(tag="relisted", first_seen=old["first_seen"], relisted_from=old["id"],
                     history=old["history"] + [[today, a["price"]]])
            old["relisted_as"] = i
            gone.pop(old["id"])
        db[i] = l

for g in gone.values():
    if g.get("active"):
        g["active"], g["gone_date"] = False, today

cnt = lambda t: sum(1 for l in db.values() if l.get("active") and l.get("tag") == t and l.get("tag_date") == today)
msg = f"Nowe: {cnt('new')}, zmiany ceny: {cnt('price')}, ponownie: {cnt('relisted')}, aktywne: {len(items)}"
print(msg)
DATA.parent.mkdir(exist_ok=True)
DATA.write_text(json.dumps({"updated": today, "items": db}, ensure_ascii=False))
if os.getenv("NTFY_TOPIC"):
    requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}", data=msg.encode("utf-8"), headers={"Title": "Civic"})
