import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

DATA_DIR = Path(__file__).parent / "data"

RECEIPTS_FILE = DATA_DIR / "receipts.json"
SPENDING_FILE = DATA_DIR / "monthly_spending.json"
ITEM_HISTORY_FILE = DATA_DIR / "item_history.json"


def _ensure_data_dir():
    """Create data directory if it doesn't exist."""
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


def update_spending(receipt_data):
    """Add a receipt's food spend to the monthly totals."""
    spending = load_monthly_spending()
    
    date = datetime.fromisoformat(receipt_data["date"])
    month_key = "%d-%02d" % (date.year, date.month)
    
    if month_key not in spending:
        spending[month_key] = {"food": 0.0, "by_store": {}}
    
    food_total = sum(item["line_total"] for item in receipt_data["items"] if item.get("is_food", True))
    store = receipt_data["store"]
    
    spending[month_key]["food"] = round(spending[month_key]["food"] + food_total, 2)
    spending[month_key]["by_store"][store] = round(
        spending[month_key]["by_store"].get(store, 0.0) + food_total, 2
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
        return spending[current_key]
    return {"food": 0.0, "by_store": {}}


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
    return spending.get(prev_key, {"food": 0.0, "by_store": {}})


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