import json
import os
import re
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

BASE_DATA_DIR = Path(__file__).parent / "data"
USERS_FILE = BASE_DATA_DIR / "users.json"
DEFAULT_USER = "default"

# Active user namespace — switched via set_user()
_current_user = DEFAULT_USER
DATA_DIR = BASE_DATA_DIR / _current_user

RECEIPTS_FILE = DATA_DIR / "receipts.json"
SPENDING_FILE = DATA_DIR / "monthly_spending.json"
ITEM_HISTORY_FILE = DATA_DIR / "item_history.json"


def _refresh_paths():
    """Recompute file paths after a user switch."""
    global DATA_DIR, RECEIPTS_FILE, SPENDING_FILE, ITEM_HISTORY_FILE
    DATA_DIR = BASE_DATA_DIR / _current_user
    RECEIPTS_FILE = DATA_DIR / "receipts.json"
    SPENDING_FILE = DATA_DIR / "monthly_spending.json"
    ITEM_HISTORY_FILE = DATA_DIR / "item_history.json"


def _slugify_user(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or DEFAULT_USER


def _ensure_base_dir():
    BASE_DATA_DIR.mkdir(exist_ok=True)


def _migrate_legacy_flat_data():
    """
    One-time: if receipts.json lives directly under data/ (old layout),
    move it into data/default/ so multi-user folders work.
    """
    _ensure_base_dir()
    legacy_receipts = BASE_DATA_DIR / "receipts.json"
    default_dir = BASE_DATA_DIR / DEFAULT_USER
    if legacy_receipts.exists() and not (default_dir / "receipts.json").exists():
        default_dir.mkdir(exist_ok=True)
        for name in ("receipts.json", "monthly_spending.json", "item_history.json"):
            src = BASE_DATA_DIR / name
            if src.exists():
                shutil.move(str(src), str(default_dir / name))


def list_users() -> list[str]:
    """Known users, always including default."""
    _ensure_base_dir()
    _migrate_legacy_flat_data()
    users = set()
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text())
            if isinstance(data, list):
                users.update(str(u) for u in data if u)
        except Exception:
            pass
    if BASE_DATA_DIR.exists():
        for p in BASE_DATA_DIR.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                users.add(p.name)
    users.add(DEFAULT_USER)
    return sorted(users)


def _save_users(users: list[str]):
    _ensure_base_dir()
    payload = sorted(set(users) | {DEFAULT_USER})
    tmp = USERS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, USERS_FILE)


def add_user(name: str) -> str:
    """Create a user namespace. Returns the slug used."""
    slug = _slugify_user(name)
    users = list_users()
    if slug not in users:
        users.append(slug)
        _save_users(users)
    (BASE_DATA_DIR / slug).mkdir(exist_ok=True)
    return slug


def set_user(name: str) -> str:
    """Switch active user namespace. Returns slug."""
    global _current_user
    slug = _slugify_user(name)
    add_user(slug)
    _current_user = slug
    _refresh_paths()
    _ensure_data_dir()
    # Auto-restore from committed seed when local data was wiped (Cloud sleep).
    try:
        ensure_seed_loaded()
    except Exception:
        pass
    return slug


def current_user() -> str:
    return _current_user


def delete_user(name: str) -> bool:
    """Remove a user namespace and its data folder. Cannot delete the last remaining user."""
    import shutil
    slug = _slugify_user(name)
    users = list_users()
    if slug not in users:
        return False
    if len(users) <= 1:
        return False
    user_dir = BASE_DATA_DIR / slug
    if user_dir.exists() and user_dir.is_dir():
        shutil.rmtree(user_dir)
    remaining = [u for u in users if u != slug]
    _save_users(remaining)
    global _current_user
    if _current_user == slug:
        _current_user = remaining[0]
        _refresh_paths()
        _ensure_data_dir()
    return True


def _ensure_data_dir():
    """Create active user's data directory if it doesn't exist."""
    _ensure_base_dir()
    _migrate_legacy_flat_data()
    DATA_DIR.mkdir(exist_ok=True)


# Seed backup lives in the repo root so it survives Streamlit Cloud sleep/redeploy.
SEED_BACKUP_PATH = Path(__file__).parent / "seed_backup.json"


def ensure_seed_loaded() -> bool:
    """If the active user has no receipts, restore from seed_backup.json. Returns True if restored."""
    _ensure_data_dir()
    receipts = load_receipts()
    if receipts:
        return False
    if not SEED_BACKUP_PATH.exists():
        return False
    try:
        with open(SEED_BACKUP_PATH, "r") as f:
            bundle = json.load(f)
        if not isinstance(bundle, dict) or "receipts" not in bundle:
            return False
        if not bundle.get("receipts"):
            return False
        import_bundle(bundle, mode="replace")
        return True
    except Exception:
        return False


def write_seed_backup(bundle: dict | None = None) -> Path:
    """Write current (or provided) bundle to seed_backup.json for the next cold start."""
    if bundle is None:
        bundle = export_bundle()
    SEED_BACKUP_PATH.parent.mkdir(exist_ok=True)
    tmp = SEED_BACKUP_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(bundle, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SEED_BACKUP_PATH)
    return SEED_BACKUP_PATH


def _load_json(filepath):
    """Load JSON from file, return empty structure if missing."""
    if not filepath.exists():
        return [] if "receipts" in filepath.name or "history" in filepath.name else {}
    with open(filepath, "r") as f:
        return json.load(f)


def _save_json(filepath, data):
    """Save data to JSON file (atomic write)."""
    _ensure_data_dir()
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, filepath)


def load_receipts():
    return _load_json(RECEIPTS_FILE)


def save_receipts(receipts):
    _save_json(RECEIPTS_FILE, receipts)


def add_receipt(receipt_data):
    receipts = load_receipts()
    receipts.append(receipt_data)
    save_receipts(receipts)
    update_spending(receipt_data)
    update_item_history(receipt_data)


def load_monthly_spending():
    data = _load_json(SPENDING_FILE)
    if not isinstance(data, dict):
        data = {}
    return data


def save_monthly_spending(spending):
    _save_json(SPENDING_FILE, spending)


def _receipt_spend(receipt_data) -> float:
    if receipt_data.get("total") is not None:
        return round(float(receipt_data["total"]), 2)
    sub = float(receipt_data.get("subtotal") or 0)
    tax = float(receipt_data.get("tax") or 0)
    if sub or tax:
        return round(sub + tax, 2)
    return round(sum(float(i.get("line_total") or 0) for i in receipt_data.get("items", [])), 2)


def update_spending(receipt_data):
    spending = load_monthly_spending()
    date = datetime.fromisoformat(receipt_data["date"])
    month_key = "%d-%02d" % (date.year, date.month)
    if month_key not in spending:
        spending[month_key] = {"total": 0.0, "by_store": {}}
    if "total" not in spending[month_key] and "food" in spending[month_key]:
        spending[month_key]["total"] = spending[month_key].pop("food")
    amount = _receipt_spend(receipt_data)
    store = receipt_data["store"]
    spending[month_key]["total"] = round(spending[month_key].get("total", 0.0) + amount, 2)
    spending[month_key]["by_store"][store] = round(
        spending[month_key]["by_store"].get(store, 0.0) + amount, 2
    )
    save_monthly_spending(spending)


def get_monthly_spending():
    spending = load_monthly_spending()
    return dict(sorted(spending.items()))


def get_current_month_data():
    spending = get_monthly_spending()
    now = datetime.utcnow()
    current_key = "%d-%02d" % (now.year, now.month)
    if current_key in spending:
        d = spending[current_key]
        if "total" not in d and "food" in d:
            d = {**d, "total": d["food"]}
        return d
    return {"total": 0.0, "by_store": {}}


def get_previous_month_data():
    spending = get_monthly_spending()
    now = datetime.utcnow()
    if now.month == 1:
        prev_month, prev_year = 12, now.year - 1
    else:
        prev_month, prev_year = now.month - 1, now.year
    prev_key = "%d-%02d" % (prev_year, prev_month)
    return spending.get(prev_key, {"total": 0.0, "by_store": {}})


def load_item_history():
    return _load_json(ITEM_HISTORY_FILE)


def save_item_history(history):
    _save_json(ITEM_HISTORY_FILE, history)


def update_item_history(receipt_data):
    history = load_item_history()
    date = receipt_data["date"]
    store = receipt_data["store"]
    trip_id = receipt_data.get("trip_id") or receipt_data.get("filename") or date
    for item in receipt_data["items"]:
        history.append({
            "item_key": item["item_key"],
            "name": item["name"],
            "store": store,
            "date": date,
            "trip_id": trip_id,
            "qty": item.get("qty", 1),
            "price": item["line_total"],
            "is_food": item.get("is_food", True)
        })
    save_item_history(history)


def get_item_frequencies():
    history = load_item_history()
    if not history:
        return []
    item_data = {}
    all_trip_ids = set()
    for entry in history:
        trip_id = entry.get("trip_id") or entry["date"]
        all_trip_ids.add(trip_id)
        key = entry["item_key"]
        if key not in item_data:
            item_data[key] = {
                "item_key": key,
                "name": entry["name"],
                "trip_ids": set(),
                "total_qty": 0,
                "total_price": 0.0,
                "stores": set(),
                "last_bought": entry["date"],
                "is_food": entry["is_food"],
            }
        item_data[key]["trip_ids"].add(trip_id)
        item_data[key]["total_qty"] += entry["qty"]
        item_data[key]["total_price"] += entry["price"]
        item_data[key]["stores"].add(entry["store"])
        if entry["date"] > item_data[key]["last_bought"]:
            item_data[key]["last_bought"] = entry["date"]
    total_trips = len(all_trip_ids)
    results = []
    for key, data in item_data.items():
        trip_count = len(data["trip_ids"])
        frequency_pct = round((trip_count / total_trips) * 100, 1) if total_trips > 0 else 0
        avg_qty = round(data["total_qty"] / trip_count, 1) if trip_count > 0 else 0
        avg_price = round(data["total_price"] / trip_count, 2) if trip_count > 0 else 0
        results.append({
            "item_key": data["item_key"],
            "name": data["name"],
            "trip_count": trip_count,
            "total_trips": total_trips,
            "frequency_pct": frequency_pct,
            "avg_qty": avg_qty,
            "avg_price": avg_price,
            "stores": sorted(data["stores"]),
            "last_bought": data["last_bought"],
            "is_food": data["is_food"],
        })
    results.sort(key=lambda x: (-x["frequency_pct"], -x["trip_count"]))
    return results


def get_shopping_list(threshold=50.0):
    frequencies = get_item_frequencies()
    return [item for item in frequencies if item["frequency_pct"] >= threshold]


def get_all_data():
    return {
        "spending": get_monthly_spending(),
        "item_frequencies": get_item_frequencies(),
        "current_month": get_current_month_data(),
        "previous_month": get_previous_month_data(),
        "shopping_list": get_shopping_list(),
        "receipt_count": len(load_receipts())
    }


def reset_data():
    for filepath in [RECEIPTS_FILE, SPENDING_FILE, ITEM_HISTORY_FILE]:
        if filepath.exists():
            filepath.unlink()
    print("All data deleted.")


def export_bundle() -> dict:
    return {
        "version": 1,
        "receipts": load_receipts(),
        "monthly_spending": load_monthly_spending(),
        "item_history": load_item_history(),
    }


def import_bundle(bundle: dict, mode: str = "replace") -> int:
    if not isinstance(bundle, dict) or "receipts" not in bundle:
        raise ValueError("Invalid backup: missing receipts")
    incoming = bundle.get("receipts") or []
    if mode == "replace":
        save_receipts([])
        _save_json(SPENDING_FILE, {})
        _save_json(ITEM_HISTORY_FILE, [])
        for r in incoming:
            add_receipt(r)
    else:
        existing = {r.get("trip_id") or r.get("filename") for r in load_receipts()}
        for r in incoming:
            key = r.get("trip_id") or r.get("filename")
            if key in existing:
                continue
            add_receipt(r)
            existing.add(key)
    return len(load_receipts())


def get_weekly_spending(max_weeks: int | None = None, fill_gaps: bool = True):
    from collections import defaultdict
    from datetime import datetime as dt, timedelta, date
    receipts = load_receipts()
    weeks: dict = defaultdict(lambda: {"total": 0.0, "by_store": {}, "trips": 0, "week_start": None})
    for r in receipts:
        try:
            d = dt.strptime(r["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        week_start = d - timedelta(days=d.weekday())
        amount = _receipt_spend(r)
        store = r.get("store", "unknown")
        weeks[key]["total"] = round(weeks[key]["total"] + amount, 2)
        weeks[key]["by_store"][store] = round(weeks[key]["by_store"].get(store, 0.0) + amount, 2)
        weeks[key]["trips"] += 1
        if weeks[key]["week_start"] is None or week_start < weeks[key]["week_start"]:
            weeks[key]["week_start"] = week_start
    def _label(ws):
        if ws is None:
            return ""
        if isinstance(ws, str):
            try:
                ws = dt.strptime(ws, "%Y-%m-%d").date()
            except ValueError:
                return str(ws)
        return f"Week of {ws.strftime('%b')} {ws.day}"
    if max_weeks is not None and max_weeks > 0 and fill_gaps:
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        ordered = {}
        for i in range(max_weeks - 1, -1, -1):
            ws = this_monday - timedelta(weeks=i)
            iso = ws.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            if key in weeks:
                payload = dict(weeks[key])
                if payload["week_start"] is None:
                    payload["week_start"] = ws
            else:
                payload = {"total": 0.0, "by_store": {}, "trips": 0, "week_start": ws}
            payload["label"] = _label(payload["week_start"])
            ws_val = payload["week_start"]
            payload["week_start"] = ws_val.isoformat() if hasattr(ws_val, "isoformat") else ws_val
            ordered[key] = payload
        return ordered
    ordered = dict(sorted(weeks.items()))
    for key, payload in ordered.items():
        ws = payload.get("week_start")
        payload["label"] = _label(ws) if ws else key
        payload["week_start"] = ws.isoformat() if ws and hasattr(ws, "isoformat") else ws
    if max_weeks is not None and max_weeks > 0:
        keys = list(ordered.keys())[-max_weeks:]
        ordered = {k: ordered[k] for k in keys}
    return ordered


def rebuild_derived():
    receipts = load_receipts()
    _save_json(SPENDING_FILE, {})
    _save_json(ITEM_HISTORY_FILE, [])
    save_receipts([])
    for r in receipts:
        add_receipt(r)


def delete_receipt(trip_id: str) -> bool:
    receipts = load_receipts()
    kept = []
    removed = False
    for r in receipts:
        key = r.get("trip_id") or r.get("filename") or ""
        stem = Path(str(key)).stem if key else ""
        if key == trip_id or stem == trip_id or r.get("filename") == trip_id:
            removed = True
            continue
        kept.append(r)
    if not removed:
        return False
    save_receipts(kept)
    _save_json(SPENDING_FILE, {})
    _save_json(ITEM_HISTORY_FILE, [])
    save_receipts([])
    for r in kept:
        add_receipt(r)
    return True


def display_store(store: str) -> str:
    s = (store or "").strip().lower()
    if s == "aldi":
        return "Aldi"
    if s == "kroger":
        return "Kroger"
    return (store or "Unknown").title()


def get_receipts_in_month(month_key: str):
    receipts = load_receipts()
    out = []
    for r in receipts:
        d = r.get("date") or ""
        if d.startswith(month_key):
            out.append(r)
    return out


def get_item_movers(month_key: str | None = None, top_n: int = 8):
    from collections import defaultdict
    history = load_item_history()
    if not history:
        return {"top": [], "rising": [], "falling": []}
    now = datetime.utcnow()
    if month_key is None:
        month_key = "%d-%02d" % (now.year, now.month)
    y, m = map(int, month_key.split("-"))
    if m == 1:
        prev_key = "%d-12" % (y - 1)
    else:
        prev_key = "%d-%02d" % (y, m - 1)
    cur = defaultdict(lambda: {"spend": 0.0, "name": "", "qty": 0})
    prev = defaultdict(lambda: {"spend": 0.0, "name": "", "qty": 0})
    for entry in history:
        d = entry.get("date") or ""
        key = entry.get("item_key") or entry.get("name")
        amt = float(entry.get("price") or 0)
        qty = float(entry.get("qty") or 0)
        name = entry.get("name") or key
        if d.startswith(month_key):
            cur[key]["spend"] = round(cur[key]["spend"] + amt, 2)
            cur[key]["qty"] = round(cur[key]["qty"] + qty, 2)
            cur[key]["name"] = name
        elif d.startswith(prev_key):
            prev[key]["spend"] = round(prev[key]["spend"] + amt, 2)
            prev[key]["qty"] = round(prev[key]["qty"] + qty, 2)
            prev[key]["name"] = name
    top = sorted(
        [{"item_key": k, "name": v["name"], "spend": v["spend"], "qty": v["qty"]} for k, v in cur.items()],
        key=lambda x: -x["spend"],
    )[:top_n]
    deltas = []
    for k in set(cur) | set(prev):
        c = cur.get(k, {"spend": 0.0, "name": k, "qty": 0})
        p = prev.get(k, {"spend": 0.0, "name": k, "qty": 0})
        name = c["name"] or p["name"] or k
        delta = round(c["spend"] - p["spend"], 2)
        deltas.append({
            "item_key": k,
            "name": name,
            "current": c["spend"],
            "previous": p["spend"],
            "delta": delta,
        })
    rising = sorted([d for d in deltas if d["delta"] > 0], key=lambda x: -x["delta"])[:top_n]
    falling = sorted([d for d in deltas if d["delta"] < 0], key=lambda x: x["delta"])[:top_n]
    return {"top": top, "rising": rising, "falling": falling, "month_key": month_key, "prev_key": prev_key}


def get_cumulative_month_spend(month_key: str | None = None):
    from calendar import monthrange
    from collections import defaultdict
    if month_key is None:
        now = datetime.utcnow()
        month_key = "%d-%02d" % (now.year, now.month)
    year, month = map(int, month_key.split("-"))
    by_day = defaultdict(float)
    for r in get_receipts_in_month(month_key):
        d = r.get("date") or ""
        if not d:
            continue
        by_day[d] = round(by_day[d] + _receipt_spend(r), 2)
    last_day = monthrange(year, month)[1]
    today = datetime.utcnow().date()
    if year == today.year and month == today.month:
        end_day = min(last_day, today.day)
    elif by_day:
        end_day = max(int(d[8:10]) for d in by_day.keys())
    else:
        end_day = 1
    running = 0.0
    out = []
    for day in range(1, end_day + 1):
        d = f"{year:04d}-{month:02d}-{day:02d}"
        daily = by_day.get(d, 0.0)
        running = round(running + daily, 2)
        out.append({"date": d, "daily": daily, "cumulative": running})
    return out


def get_top_item_sparklines(top_n: int = 6, max_weeks: int = 8):
    from collections import defaultdict
    from datetime import datetime as dt
    weekly = get_weekly_spending(max_weeks=max_weeks)
    week_keys = list(weekly.keys())
    if not week_keys:
        return []
    week_index = {k: i for i, k in enumerate(week_keys)}
    labels = []
    for k in week_keys:
        ws = weekly[k].get("week_start")
        if ws:
            try:
                d = dt.strptime(ws, "%Y-%m-%d")
                labels.append(d.strftime("%b ") + str(d.day))
            except ValueError:
                labels.append(weekly[k].get("label", k))
        else:
            labels.append(weekly[k].get("label", k))
    series_map: dict = defaultdict(lambda: [0.0] * len(week_keys))
    names: dict = {}
    totals: dict = defaultdict(float)
    months_seen: dict = defaultdict(set)
    for entry in load_item_history():
        d_str = entry.get("date") or ""
        try:
            d = dt.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        iso = d.isocalendar()
        wk = f"{iso.year}-W{iso.week:02d}"
        if wk not in week_index:
            continue
        key = entry.get("item_key") or entry.get("name")
        amt = float(entry.get("price") or 0)
        idx = week_index[wk]
        series_map[key][idx] = round(series_map[key][idx] + amt, 2)
        totals[key] = round(totals[key] + amt, 2)
        names[key] = entry.get("name") or key
        months_seen[key].add(d_str[:7])
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:top_n]
    out = []
    n_weeks = max(len(week_keys), 1)
    for key, total in ranked:
        series = series_map[key]
        avg_week = round(sum(series) / n_weeks, 2)
        n_months = len(months_seen.get(key) or []) or 1
        out.append({
            "item_key": key,
            "name": names.get(key, key),
            "total_spend": total,
            "avg_per_week": avg_week,
            "avg_per_month": round(total / n_months, 2),
            "weeks": labels,
            "series": series,
        })
    return out


def get_available_months():
    spending = get_monthly_spending()
    return sorted(spending.keys())


def get_month_data(month_key: str):
    spending = get_monthly_spending()
    d = spending.get(month_key, {"total": 0.0, "by_store": {}})
    if "total" not in d and "food" in d:
        d = {**d, "total": d["food"]}
    return d
