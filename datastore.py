"""Grocery tracker datastore — restored with rankings + seed auto-push."""
from __future__ import annotations

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
_current_user = DEFAULT_USER
DATA_DIR = BASE_DATA_DIR / _current_user
RECEIPTS_FILE = DATA_DIR / "receipts.json"
SPENDING_FILE = DATA_DIR / "monthly_spending.json"
ITEM_HISTORY_FILE = DATA_DIR / "item_history.json"
SEED_BACKUP_PATH = Path(__file__).parent / "seed_backup.json"


def _refresh_paths():
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
    _ensure_base_dir()
    legacy_receipts = BASE_DATA_DIR / "receipts.json"
    default_dir = BASE_DATA_DIR / DEFAULT_USER
    if legacy_receipts.exists() and not (default_dir / "receipts.json").exists():
        default_dir.mkdir(exist_ok=True)
        for name in ("receipts.json", "monthly_spending.json", "item_history.json"):
            src = BASE_DATA_DIR / name
            if src.exists():
                shutil.move(str(src), str(default_dir / name))


def _ensure_data_dir():
    _ensure_base_dir()
    _migrate_legacy_flat_data()
    DATA_DIR.mkdir(exist_ok=True)


def _load_json(filepath):
    if not filepath.exists():
        return [] if "receipts" in filepath.name or "history" in filepath.name else {}
    with open(filepath, "r") as f:
        return json.load(f)


def _save_json(filepath, data):
    _ensure_data_dir()
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, filepath)


def list_users() -> list[str]:
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
    slug = _slugify_user(name)
    users = list_users()
    if slug not in users:
        users.append(slug)
        _save_users(users)
    (BASE_DATA_DIR / slug).mkdir(exist_ok=True)
    return slug


def set_user(name: str) -> str:
    global _current_user
    slug = _slugify_user(name)
    add_user(slug)
    _current_user = slug
    _refresh_paths()
    _ensure_data_dir()
    try:
        ensure_seed_loaded()
    except Exception:
        pass
    return slug


def current_user() -> str:
    return _current_user


def delete_user(name: str) -> bool:
    slug = _slugify_user(name)
    users = list_users()
    if slug not in users or len(users) <= 1:
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


def load_receipts():
    return _load_json(RECEIPTS_FILE)


def save_receipts(receipts):
    _save_json(RECEIPTS_FILE, receipts)


def _receipt_spend(receipt_data) -> float:
    if receipt_data.get("total") is not None:
        return round(float(receipt_data["total"]), 2)
    sub = float(receipt_data.get("subtotal") or 0)
    tax = float(receipt_data.get("tax") or 0)
    if sub or tax:
        return round(sub + tax, 2)
    return round(sum(float(i.get("line_total") or 0) for i in receipt_data.get("items", [])), 2)


def load_monthly_spending():
    data = _load_json(SPENDING_FILE)
    return data if isinstance(data, dict) else {}


def save_monthly_spending(spending):
    _save_json(SPENDING_FILE, spending)


def load_item_history():
    return _load_json(ITEM_HISTORY_FILE)


def save_item_history(history):
    _save_json(ITEM_HISTORY_FILE, history)


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
            "is_food": item.get("is_food", True),
        })
    save_item_history(history)


def add_receipt(receipt_data):
    receipts = load_receipts()
    receipts.append(receipt_data)
    save_receipts(receipts)
    update_spending(receipt_data)
    update_item_history(receipt_data)
    try:
        maybe_push_seed_after_change()
    except Exception:
        pass


def delete_receipt(trip_id: str) -> bool:
    receipts = load_receipts()
    kept, removed = [], False
    for r in receipts:
        key = r.get("trip_id") or r.get("filename") or ""
        stem = Path(str(key)).stem if key else ""
        if key == trip_id or stem == trip_id or r.get("filename") == trip_id:
            removed = True
            continue
        kept.append(r)
    if not removed:
        return False
    save_receipts([])
    _save_json(SPENDING_FILE, {})
    _save_json(ITEM_HISTORY_FILE, [])
    for r in kept:
        add_receipt(r)
    try:
        maybe_push_seed_after_change()
    except Exception:
        pass
    return True


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


def ensure_seed_loaded() -> bool:
    _ensure_data_dir()
    if load_receipts():
        return False
    if not SEED_BACKUP_PATH.exists():
        return False
    try:
        with open(SEED_BACKUP_PATH, "r") as f:
            bundle = json.load(f)
        if not isinstance(bundle, dict) or not bundle.get("receipts"):
            return False
        import_bundle(bundle, mode="replace")
        return True
    except Exception:
        return False


def write_seed_backup(bundle: dict | None = None) -> Path:
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


def get_monthly_spending():
    return dict(sorted(load_monthly_spending().items()))


def get_available_months():
    return sorted(get_monthly_spending().keys())


def get_month_data(month_key: str):
    d = get_monthly_spending().get(month_key, {"total": 0.0, "by_store": {}})
    if "total" not in d and "food" in d:
        d = {**d, "total": d["food"]}
    return d


def get_receipts_in_month(month_key: str):
    return [r for r in load_receipts() if (r.get("date") or "").startswith(month_key)]


def display_store(store: str) -> str:
    s = (store or "").strip().lower()
    if s == "aldi":
        return "Aldi"
    if s == "kroger":
        return "Kroger"
    return (store or "Unknown").title()


def get_item_frequencies():
    history = load_item_history()
    if not history:
        return []
    item_data, all_trip_ids = {}, set()
    for entry in history:
        trip_id = entry.get("trip_id") or entry["date"]
        all_trip_ids.add(trip_id)
        key = entry["item_key"]
        if key not in item_data:
            item_data[key] = {
                "item_key": key, "name": entry["name"], "trip_ids": set(),
                "total_qty": 0, "total_price": 0.0, "stores": set(),
                "last_bought": entry["date"], "is_food": entry["is_food"],
            }
        item_data[key]["trip_ids"].add(trip_id)
        item_data[key]["total_qty"] += entry["qty"]
        item_data[key]["total_price"] += entry["price"]
        item_data[key]["stores"].add(entry["store"])
        if entry["date"] > item_data[key]["last_bought"]:
            item_data[key]["last_bought"] = entry["date"]
    total_trips = len(all_trip_ids) or 1
    results = []
    for key, data in item_data.items():
        trip_count = len(data["trip_ids"])
        results.append({
            "item_key": data["item_key"], "name": data["name"],
            "trip_count": trip_count, "total_trips": total_trips,
            "frequency_pct": round((trip_count / total_trips) * 100, 1),
            "avg_qty": round(data["total_qty"] / trip_count, 1) if trip_count else 0,
            "avg_price": round(data["total_price"] / trip_count, 2) if trip_count else 0,
            "stores": sorted(data["stores"]), "last_bought": data["last_bought"],
            "is_food": data["is_food"],
        })
    results.sort(key=lambda x: (-x["frequency_pct"], -x["trip_count"]))
    return results


def get_shopping_list(threshold=50.0):
    return [i for i in get_item_frequencies() if i["frequency_pct"] >= threshold]


def get_weekly_spending(max_weeks: int | None = None, fill_gaps: bool = True):
    from collections import defaultdict
    from datetime import datetime as dt, timedelta, date
    receipts = load_receipts()
    weeks = defaultdict(lambda: {"total": 0.0, "by_store": {}, "trips": 0, "week_start": None})
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


def get_available_weeks():
    return list(get_weekly_spending(max_weeks=52, fill_gaps=False).keys())


def get_trip_and_basket_series(max_weeks: int = 8):
    weekly = get_weekly_spending(max_weeks=max_weeks)
    out = {}
    for k, v in weekly.items():
        trips = v.get("trips") or 0
        total = v.get("total") or 0.0
        out[k] = {
            "label": v.get("label", k),
            "trips": trips,
            "total": total,
            "avg_basket": round(total / trips, 2) if trips else 0.0,
            "by_store": v.get("by_store") or {},
        }
    return out


def get_monthly_trip_and_basket():
    from collections import defaultdict
    by_month = defaultdict(lambda: {"trips": 0, "total": 0.0})
    for r in load_receipts():
        d = r.get("date") or ""
        if len(d) < 7:
            continue
        mk = d[:7]
        by_month[mk]["trips"] += 1
        by_month[mk]["total"] = round(by_month[mk]["total"] + _receipt_spend(r), 2)
    out = {}
    for mk in sorted(by_month.keys()):
        v = by_month[mk]
        trips = v["trips"]
        out[mk] = {
            "trips": trips,
            "total": v["total"],
            "avg_basket": round(v["total"] / trips, 2) if trips else 0.0,
        }
    return out


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
        if d:
            by_day[d] = round(by_day[d] + _receipt_spend(r), 2)
    last_day = monthrange(year, month)[1]
    today = datetime.utcnow().date()
    if year == today.year and month == today.month:
        end_day = min(last_day, today.day)
    elif by_day:
        end_day = max(int(d[8:10]) for d in by_day.keys())
    else:
        end_day = 1
    running, out = 0.0, []
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
    series_map = defaultdict(lambda: [0.0] * len(week_keys))
    names, totals, months_seen = {}, defaultdict(float), defaultdict(set)
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
        series_map[key][week_index[wk]] = round(series_map[key][week_index[wk]] + amt, 2)
        totals[key] = round(totals[key] + amt, 2)
        names[key] = entry.get("name") or key
        months_seen[key].add(d_str[:7])
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:top_n]
    n_weeks = max(len(week_keys), 1)
    out = []
    for key, total in ranked:
        series = series_map[key]
        n_months = len(months_seen.get(key) or []) or 1
        out.append({
            "item_key": key, "name": names.get(key, key),
            "total_spend": total, "avg_per_week": round(sum(series) / n_weeks, 2),
            "avg_per_month": round(total / n_months, 2),
            "weeks": labels, "series": series,
        })
    return out


def get_weekly_item_rankings(week_key: str, top_n: int = 15):
    from collections import defaultdict
    from datetime import datetime as dt

    weekly = get_weekly_spending(max_weeks=None, fill_gaps=False)
    active = [k for k, v in weekly.items() if (v.get("trips") or 0) > 0]
    if week_key not in weekly and week_key not in active:
        return {"week_key": week_key, "previous_week_key": None, "label": week_key,
                "previous_label": None, "rankings": []}
    if week_key not in active:
        active = list(weekly.keys())
    idx = active.index(week_key) if week_key in active else -1
    prev_week = active[idx - 1] if idx > 0 else None
    label = weekly.get(week_key, {}).get("label", week_key)
    prev_label = weekly.get(prev_week, {}).get("label", prev_week) if prev_week else None

    week_items = defaultdict(lambda: {"spend": 0.0, "qty": 0.0, "name": "", "prices": []})
    life_trips = defaultdict(set)
    all_trips = set()
    month_spend = defaultdict(float)
    last_bought = {}
    prev_items = set()
    try:
        ws = weekly.get(week_key, {}).get("week_start")
        mk = dt.strptime(ws, "%Y-%m-%d").strftime("%Y-%m") if isinstance(ws, str) else ""
    except Exception:
        mk = ""

    for entry in load_item_history():
        d_str = entry.get("date") or ""
        try:
            d = dt.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        iso = d.isocalendar()
        wk = f"{iso.year}-W{iso.week:02d}"
        key = entry.get("item_key") or entry.get("name")
        trip = entry.get("trip_id") or d_str
        amt = float(entry.get("price") or 0)
        qty = float(entry.get("qty") or 0)
        all_trips.add(trip)
        life_trips[key].add(trip)
        if d_str > last_bought.get(key, ""):
            last_bought[key] = d_str
        if mk and d_str.startswith(mk):
            month_spend[key] = round(month_spend[key] + amt, 2)
        if wk == week_key:
            week_items[key]["spend"] = round(week_items[key]["spend"] + amt, 2)
            week_items[key]["qty"] = round(week_items[key]["qty"] + qty, 2)
            week_items[key]["name"] = entry.get("name") or key
            if qty:
                week_items[key]["prices"].append(amt / qty)
        if prev_week and wk == prev_week:
            prev_items.add(key)

    total_trips = max(len(all_trips), 1)
    scored = sorted(
        ((round(len(life_trips[k]) / total_trips * 100, 1), len(life_trips[k]), k, v)
         for k, v in week_items.items()),
        key=lambda x: (-x[0], -x[1], x[2]),
    )
    prev_rank = {}
    if prev_week:
        prev_scored = sorted(
            ((len(life_trips[k]) / total_trips * 100, len(life_trips[k]), k) for k in prev_items),
            key=lambda x: (-x[0], -x[1], x[2]),
        )
        for i, (_, _, k) in enumerate(prev_scored, 1):
            prev_rank[k] = i

    rankings = []
    for i, (freq, trips, key, data) in enumerate(scored[:top_n], 1):
        pr = prev_rank.get(key)
        prices = data["prices"]
        rankings.append({
            "current_rank": i,
            "previous_rank": pr,
            "rank_delta": (pr - i) if pr is not None else None,
            "name": data["name"] or key,
            "item_key": key,
            "frequency_pct": freq,
            "times_bought": trips,
            "week_spend": data["spend"],
            "month_spend": month_spend.get(key, 0.0),
            "avg_qty_per_trip": round(data["qty"] / max(trips, 1), 2),
            "avg_unit_price": round(sum(prices) / len(prices), 2) if prices else None,
            "price_delta": None,
            "last_bought": last_bought.get(key),
        })
    return {
        "week_key": week_key,
        "previous_week_key": prev_week,
        "label": label,
        "previous_label": prev_label,
        "rankings": rankings,
    }


def push_seed_to_github(bundle=None):
    import base64, json as _j, urllib.error, urllib.request
    try:
        import streamlit as st
        gh = st.secrets.get("github", {})
        token = gh.get("token") or gh.get("pat")
        repo = gh.get("repo") or "orcayoungcharles-wq/grocery-tracking"
        branch = gh.get("branch") or "main"
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not token:
        return {"ok": False, "error": "missing secrets.github.token"}
    if bundle is None:
        bundle = export_bundle()
    body = _j.dumps(bundle, separators=(",", ":"), default=str)
    b64 = base64.b64encode(body.encode()).decode()
    api = f"https://api.github.com/repos/{repo}/contents/seed_backup.json"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "grocery-seed"}
    sha = None
    try:
        req = urllib.request.Request(f"{api}?ref={branch}", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            sha = _j.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    payload = {"message": f"chore: seed update ({len(bundle.get('receipts') or [])} receipts)",
               "content": b64, "branch": branch}
    if sha:
        payload["sha"] = sha
    req = urllib.request.Request(api, data=_j.dumps(payload).encode(),
        headers={**headers, "Content-Type": "application/json"}, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = _j.loads(r.read().decode())
        try:
            write_seed_backup(bundle)
        except Exception:
            pass
        return {"ok": True, "sha": (res.get("content") or {}).get("sha"),
                "receipts": len(bundle.get("receipts") or [])}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def maybe_push_seed_after_change():
    bundle = export_bundle()
    try:
        write_seed_backup(bundle)
    except Exception:
        pass
    try:
        import streamlit as st
        gh = st.secrets.get("github", {})
        if not (gh.get("token") or gh.get("pat")):
            return None
    except Exception:
        return None
    return push_seed_to_github(bundle)
