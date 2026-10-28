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
    # Also discover existing folders
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
    add_user(slug)  # ensure exists
    _current_user = slug
    _refresh_paths()
    _ensure_data_dir()
    return slug


def current_user() -> str:
    return _current_user


def _ensure_data_dir():
    """Create active user's data directory if it doesn't exist."""
    _ensure_base_dir()
    _migrate_legacy_flat_data()
    DATA_DIR.mkdir(exist_ok=True)


def _load_json(filepath):
    """Load JSON from file, return empty structure if missing."""
    if not filepath.exists():
        return [] if "receipts" in filepath.name or "history" in filepath.name else {}
    with open(filepath, "r") as f:
        return json.load(f)


def _save_json(filepath, data):
    """Save data to JSON file (atomic write to avoid mount I/O glitches)."""
    _ensure_data_dir()
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, filepath)


# --- Receipts ---

def load_receipts():
    """Load all stored receipts."""
    return _load_json(RECEIPTS_FILE)


def save_receipts(receipts):
    """Save all receipts."""
    _save_json(RECEIPTS_FILE, receipts)


def add_receipt(receipt_data):
    """
    Add a single parsed receipt to storage.
    
    receipt_data should match the output of aldi-parser or kroger-parser:
    {
        "store": "aldi" | "kroger",
        "date": "YYYY-MM-DD",
        "items": [...],
        "subtotal": float,
        "tax": float,
        "total": float,
        "reconciled": bool,
        "filename": str
    }
    """
    receipts = load_receipts()
    receipts.append(receipt_data)
    save_receipts(receipts)
    
    # Update derived data structures
    update_spending(receipt_data)
    update_item_history(receipt_data)


# --- Monthly Spending ---

def load_monthly_spending():
    """Load monthly spending data."""
    data = _load_json(SPENDING_FILE)
    if not isinstance(data, dict):
        data = {}
    return data


def save_monthly_spending(spending):
    _save_json(SPENDING_FILE, spending)


def _receipt_spend(receipt_data) -> float:
    """Full amount paid: prefer stored total, else subtotal+tax, else sum of lines."""
    if receipt_data.get("total") is not None:
        return round(float(receipt_data["total"]), 2)
    sub = float(receipt_data.get("subtotal") or 0)
    tax = float(receipt_data.get("tax") or 0)
    if sub or tax:
        return round(sub + tax, 2)
    return round(sum(float(i.get("line_total") or 0) for i in receipt_data.get("items", [])), 2)


def update_spending(receipt_data):
    """Add a receipt's full spend (items + tax) to the monthly totals."""
    spending = load_monthly_spending()

    date = datetime.fromisoformat(receipt_data["date"])
    month_key = "%d-%02d" % (date.year, date.month)

    if month_key not in spending:
        spending[month_key] = {"total": 0.0, "by_store": {}}
    # Migrate old "food" key if present
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
    """Return monthly spending data, sorted by month key."""
    spending = load_monthly_spending()
    return dict(sorted(spending.items()))


def get_current_month_data():
    """Get current month's spending data."""
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
    """Get previous month's spending data."""
    spending = get_monthly_spending()
    now = datetime.utcnow()
    
    if now.month == 1:
        prev_month = 12
        prev_year = now.year - 1
    else:
        prev_month = now.month - 1
        prev_year = now.year
    
    prev_key = "%d-%02d" % (prev_year, prev_month)
    return spending.get(prev_key, {"total": 0.0, "by_store": {}})


# --- Item History ---

def load_item_history():
    """Load item history."""
    return _load_json(ITEM_HISTORY_FILE)


def save_item_history(history):
    _save_json(ITEM_HISTORY_FILE, history)


def update_item_history(receipt_data):
    """Add items from a receipt to item history."""
    history = load_item_history()
    date = receipt_data["date"]
    store = receipt_data["store"]
    # Prefer explicit trip_id so same-day AM/PM trips count separately
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
    """
    Calculate how frequently each item appears across trips.

    Trip counting rules:
    - A trip is identified by trip_id (filename stem), falling back to date.
    - Multiple line items of the same item_key on one receipt still count as
      ONE trip (trip_ids is a set).
    - Same calendar date with different trip_ids (e.g. 8.26-am / 8.26-pm)
      count as separate trips.

    Returns sorted list (most frequent first).
    """
    history = load_item_history()
    if not history:
        return []

    # Group by item_key; trip_ids is a set so duplicate lines on one receipt
    # still count as a single trip.
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

        item_data[key]["trip_ids"].add(trip_id)  # unique per trip
        item_data[key]["total_qty"] += entry["qty"]
        item_data[key]["total_price"] += entry["price"]
        item_data[key]["stores"].add(entry["store"])

        if entry["date"] > item_data[key]["last_bought"]:
            item_data[key]["last_bought"] = entry["date"]

    total_trips = len(all_trip_ids)

    results = []
    for key, data in item_data.items():
        trip_count = len(data["trip_ids"])  # UNIQUE trips, not line count
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

    # Sort by frequency (descending), then by trip count
    results.sort(key=lambda x: (-x["frequency_pct"], -x["trip_count"]))

    return results


def get_shopping_list(threshold=50.0):
    """
    Generate shopping list: items bought on >= threshold% of trips.
    threshold: minimum frequency percentage to include (default 50%)
    """
    frequencies = get_item_frequencies()
    return [item for item in frequencies if item["frequency_pct"] >= threshold]


# --- Utility ---

def get_all_data():
    """Load all data at once for the Streamlit app."""
    return {
        "spending": get_monthly_spending(),
        "item_frequencies": get_item_frequencies(),
        "current_month": get_current_month_data(),
        "previous_month": get_previous_month_data(),
        "shopping_list": get_shopping_list(),
        "receipt_count": len(load_receipts())
    }


def reset_data():
    """Delete all stored data (for testing)."""
    for filepath in [RECEIPTS_FILE, SPENDING_FILE, ITEM_HISTORY_FILE]:
        if filepath.exists():
            filepath.unlink()
    print("All data deleted.")


def export_bundle() -> dict:
    """Return a single JSON-serializable bundle of all persisted data."""
    return {
        "version": 1,
        "receipts": load_receipts(),
        "monthly_spending": load_monthly_spending(),
        "item_history": load_item_history(),
    }


def import_bundle(bundle: dict, mode: str = "replace") -> int:
    """
    Restore from an export_bundle() dict.
    mode='replace' overwrites everything; mode='merge' appends receipts
    whose trip_id / filename is not already present.
    Returns number of receipts after import.
    """
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


def get_weekly_spending(max_weeks: int | None = None):
    """
    Aggregate full spend by ISO week.
    Returns ordered dict keyed by ISO week id, each value:
      {label, total, by_store, trips, week_start}
    label looks like "Week of Oct 6".
    If max_weeks is set, only the most recent N weeks are returned.
    """
    from collections import defaultdict
    from datetime import datetime as dt, timedelta

    receipts = load_receipts()
    weeks: dict = defaultdict(lambda: {"total": 0.0, "by_store": {}, "trips": 0, "week_start": None})

    for r in receipts:
        try:
            d = dt.strptime(r["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        # Monday of that ISO week
        week_start = d - timedelta(days=d.weekday())
        amount = _receipt_spend(r)
        store = r.get("store", "unknown")
        weeks[key]["total"] = round(weeks[key]["total"] + amount, 2)
        weeks[key]["by_store"][store] = round(
            weeks[key]["by_store"].get(store, 0.0) + amount, 2
        )
        weeks[key]["trips"] += 1
        if weeks[key]["week_start"] is None or week_start < weeks[key]["week_start"]:
            weeks[key]["week_start"] = week_start

    ordered = dict(sorted(weeks.items()))
    # Attach friendly labels
    for key, payload in ordered.items():
        ws = payload.get("week_start")
        if ws:
            payload["label"] = f"Week of {ws.strftime('%b')} {ws.day}"
        else:
            payload["label"] = key
        # JSON-serializable
        payload["week_start"] = ws.isoformat() if ws else None

    if max_weeks is not None and max_weeks > 0:
        keys = list(ordered.keys())[-max_weeks:]
        ordered = {k: ordered[k] for k in keys}
    return ordered


def rebuild_derived():
    """Recompute monthly spending and item history from stored receipts."""
    receipts = load_receipts()
    _save_json(SPENDING_FILE, {})
    _save_json(ITEM_HISTORY_FILE, [])
    # Temporarily clear receipts so add_receipt can re-append without duplicating
    save_receipts([])
    for r in receipts:
        add_receipt(r)
    return len(load_receipts())


def delete_receipt(trip_id: str) -> bool:
    """
    Remove one receipt by trip_id (or filename stem) and rebuild derived data.
    Returns True if something was removed.
    """
    receipts = load_receipts()
    kept = []
    removed = False
    for r in receipts:
        key = r.get("trip_id") or r.get("filename") or ""
        # Also match stem without extension
        stem = Path(str(key)).stem if key else ""
        if key == trip_id or stem == trip_id or r.get("filename") == trip_id:
            removed = True
            continue
        kept.append(r)
    if not removed:
        return False
    save_receipts(kept)
    # Rebuild from remaining
    _save_json(SPENDING_FILE, {})
    _save_json(ITEM_HISTORY_FILE, [])
    save_receipts([])
    for r in kept:
        add_receipt(r)
    return True


def display_store(store: str) -> str:
    """Canonical display name for a store (Aldi / Kroger)."""
    s = (store or "").strip().lower()
    if s == "aldi":
        return "Aldi"
    if s == "kroger":
        return "Kroger"
    return (store or "Unknown").title()


def get_available_months():
    """Sorted list of YYYY-MM keys that have spending data."""
    return list(get_monthly_spending().keys())


def get_month_data(month_key: str):
    """Spending payload for a specific YYYY-MM key."""
    spending = get_monthly_spending()
    d = spending.get(month_key, {"total": 0.0, "by_store": {}})
    if "total" not in d and "food" in d:
        d = {**d, "total": d["food"]}
    return d


def get_receipts_in_month(month_key: str):
    """Receipts whose date falls in YYYY-MM."""
    out = []
    for r in load_receipts():
        d = r.get("date") or ""
        if d.startswith(month_key):
            out.append(r)
    return out


def get_item_movers(current_key: str, previous_key: str, top_n: int = 10):
    """
    Rank items by spend in current_key vs previous_key.
    Returns dict with rising, falling, and current_top lists.
    """
    from collections import defaultdict

    def month_totals(month_key: str) -> dict:
        totals = defaultdict(lambda: {"spend": 0.0, "qty": 0.0, "name": ""})
        for entry in load_item_history():
            d = entry.get("date") or ""
            if not d.startswith(month_key):
                continue
            key = entry.get("item_key") or entry.get("name")
            totals[key]["spend"] = round(
                totals[key]["spend"] + float(entry.get("price") or 0), 2
            )
            totals[key]["qty"] = round(
                totals[key]["qty"] + float(entry.get("qty") or 0), 2
            )
            totals[key]["name"] = entry.get("name") or key
        return dict(totals)

    cur = month_totals(current_key)
    prev = month_totals(previous_key)

    def ranked(d):
        return sorted(d.items(), key=lambda kv: -kv[1]["spend"])

    cur_rank = {k: i + 1 for i, (k, _) in enumerate(ranked(cur))}
    prev_rank = {k: i + 1 for i, (k, _) in enumerate(ranked(prev))}

    movers = []
    for key, data in cur.items():
        pr = prev_rank.get(key)
        cr = cur_rank.get(key)
        prev_spend = prev.get(key, {}).get("spend", 0.0)
        delta = round(data["spend"] - prev_spend, 2)
        rank_delta = None
        if pr is not None and cr is not None:
            rank_delta = pr - cr  # positive = moved up the chart
        movers.append({
            "item_key": key,
            "name": data["name"],
            "current_spend": data["spend"],
            "previous_spend": prev_spend,
            "delta": delta,
            "current_rank": cr,
            "previous_rank": pr,
            "rank_delta": rank_delta,
            "qty": data["qty"],
        })

    rising = sorted(
        [
            m
            for m in movers
            if (m["rank_delta"] is not None and m["rank_delta"] > 0)
            or m["previous_rank"] is None
        ],
        key=lambda m: (-(m["rank_delta"] if m["rank_delta"] is not None else 99), -m["current_spend"]),
    )[:top_n]

    falling = sorted(
        [m for m in movers if m["rank_delta"] is not None and m["rank_delta"] < 0],
        key=lambda m: (m["rank_delta"], -m["previous_spend"]),
    )[:top_n]

    current_top = sorted(movers, key=lambda m: -m["current_spend"])[:top_n]

    return {
        "rising": rising,
        "falling": falling,
        "current_top": current_top,
        "current_key": current_key,
        "previous_key": previous_key,
    }


def get_trip_and_basket_series(max_weeks: int | None = 8):
    """
    Weekly series for trip count and average basket size.
    Returns ordered dict: week_key -> {label, trips, total, avg_basket}
    """
    weekly = get_weekly_spending(max_weeks=max_weeks)
    out = {}
    for key, payload in weekly.items():
        trips = int(payload.get("trips") or 0)
        total = float(payload.get("total") or 0)
        out[key] = {
            "label": payload.get("label", key),
            "trips": trips,
            "total": total,
            "avg_basket": round(total / trips, 2) if trips else 0.0,
        }
    return out


def get_monthly_trip_and_basket():
    """
    Calendar-month series for trip count and average basket.
    Returns ordered dict: YYYY-MM -> {label, trips, total, avg_basket}
    """
    from collections import defaultdict

    receipts = load_receipts()
    months: dict = defaultdict(lambda: {"trips": 0, "total": 0.0})
    for r in receipts:
        d = r.get("date") or ""
        if len(d) < 7:
            continue
        mk = d[:7]
        months[mk]["trips"] += 1
        months[mk]["total"] = round(months[mk]["total"] + _receipt_spend(r), 2)
    ordered = dict(sorted(months.items()))
    for mk, payload in ordered.items():
        trips = payload["trips"]
        total = payload["total"]
        try:
            dt = datetime.strptime(mk + "-01", "%Y-%m-%d")
            label = dt.strftime("%b %Y")
        except ValueError:
            label = mk
        payload["label"] = label
        payload["avg_basket"] = round(total / trips, 2) if trips else 0.0
    return ordered


def get_cumulative_month_spend(month_key: str):
    """
    Running total of spend within a calendar month, by receipt date.
    Returns list of {date, daily, cumulative} sorted by date.
    Multiple trips same day are summed into daily.
    """
    from collections import defaultdict

    by_day: dict = defaultdict(float)
    for r in get_receipts_in_month(month_key):
        d = r.get("date") or ""
        if not d:
            continue
        by_day[d] = round(by_day[d] + _receipt_spend(r), 2)
    running = 0.0
    out = []
    for d in sorted(by_day.keys()):
        running = round(running + by_day[d], 2)
        out.append({"date": d, "daily": by_day[d], "cumulative": running})
    return out


def get_top_item_sparklines(top_n: int = 6, max_weeks: int = 8):
    """
    Top items by recent spend with weekly spend series for sparklines.
    Returns list of {
      item_key, name, total_spend,
      weeks: [week_label, ...],
      series: [spend_week_0, ...]  # aligned to weeks
    }
    """
    from collections import defaultdict
    from datetime import datetime as dt, timedelta

    weekly = get_weekly_spending(max_weeks=max_weeks)
    week_keys = list(weekly.keys())
    if not week_keys:
        return []

    # Map each ISO week key to index
    week_index = {k: i for i, k in enumerate(week_keys)}
    labels = [weekly[k].get("label", k) for k in week_keys]

    # Aggregate item spend per week
    # item_key -> list of spends aligned to week_keys
    series_map: dict = defaultdict(lambda: [0.0] * len(week_keys))
    names: dict = {}
    totals: dict = defaultdict(float)

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

    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:top_n]
    out = []
    for key, total in ranked:
        out.append({
            "item_key": key,
            "name": names.get(key, key),
            "total_spend": total,
            "weeks": labels,
            "series": series_map[key],
        })
    return out
