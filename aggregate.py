"""
Aggregation layer for Aldi + Kroger receipt items.

Pure functions only — no I/O, no UI.

Locked rules:
- Spending = merchandise subtotal of food items only (is_food == True).
- Tax, rounding, non-food excluded.
- Aldi identity = SKU. Kroger identity = normalized name string.
- Frozen/pantry classification stores a confidence flag; never silently guess.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Kroger name alias table
# Expand common abbreviations so identical products share a key.
# Order matters only for multi-token replacements applied left-to-right.
# ---------------------------------------------------------------------------

# Whole-token replacements (applied after uppercasing + whitespace collapse)
ALIAS_MAP: Dict[str, str] = {
    "HASHBRN": "HASHBROWN",
    "HASHBR": "HASHBROWN",
    "WW": "WHOLE WHEAT",
    "RT": "RTD",          # ready-to-drink / ready-to-
    "RTS": "READY TO SERVE",
    "CHK": "CHICKEN",
    "FR": "FRIED",
    "RCE": "RICE",
    "BRE": "BREAD",
    "BREPC": "BREAD",     # OCR glue of BRE + PC promo marker
    "BKRY": "BAKERY",
    "CHO": "CHOCOLATE",
    "CHIP": "CHIP",
    "CK": "COOKIE",
    "PRSL": "PRETZEL",
    "PRO": "PROTEIN",
    "SPSHRP": "SOFT PRETZEL SHARP",
    "RL": "ROLL",
    "STFR": "STOUFFER",
    "LASAGNA": "LASAGNA",
    "FRANKS": "FRANKS",
    "HOTDOG": "HOT DOG",
    "BUNS": "BUNS",
    "PEPPERONI": "PEPPERONI",
    "BACON": "BACON",
    "BURGERS": "BURGERS",
    "PATTIE": "PATTIE",
    "PATTIES": "PATTIES",
    "MEAT": "MEAT",
    "SQ": "SQUARE",
    "DR": "DR",           # brand prefix keep
    "SHAMPOO": "SHAMPOO",
    "BLPK": "BLPK",       # keep as-is (private-label brand token)
    "KRO": "KRO",         # keep as-is
    "BAR-S": "BAR-S",
    "PC": "",             # promo marker — drop if still present
}

# Tokens that should be dropped entirely after alias expansion
DROP_TOKENS = {"PC", ""}

# Non-food keyword filter (Kroger often omits the trailing N tax code on
# household items). Applied in normalize_item so spending stays food-only.
NON_FOOD_KEYWORDS = re.compile(
    r"\b(SHAMPOO|CONDITIONER|SOAP|DETERGENT|BLEACH|PAPER\s*BAG|"
    r"BAGS?|TOOTHPASTE|TOOTHBRUSH|DEODORANT|LOTION|TISSUE|NAPKIN|"
    r"PAPER\s*TOWEL|TRASH\s*BAG|CLEANER|DISH\s*SOAP)\b",
    re.IGNORECASE,
)


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _normalize_kroger_name(raw_name: str) -> tuple[str, str]:
    """
    Return (item_key, display_name).

    item_key  = uppercase, whitespace-collapsed, aliases expanded.
    display_name = original cleaned name (title-ish for humans).
    """
    cleaned = _collapse_ws(raw_name)
    upper = cleaned.upper()

    tokens = upper.split()
    expanded: List[str] = []
    for tok in tokens:
        # strip trailing punctuation that sometimes survives OCR
        tok = tok.rstrip(".,;|")
        if not tok:
            continue
        replacement = ALIAS_MAP.get(tok, tok)
        if replacement in DROP_TOKENS or replacement == "":
            continue
        # replacement may itself contain spaces (e.g. WHOLE WHEAT)
        expanded.extend(replacement.split())

    key = " ".join(expanded)
    # display_name keeps original casing/spacing but collapses whitespace
    display = cleaned
    return key, display


def _item_to_dict(item: Any) -> Dict[str, Any]:
    """Accept dataclass or plain dict."""
    if hasattr(item, "__dataclass_fields__"):
        return {
            "sku": getattr(item, "sku", None),
            "name": getattr(item, "name", None),
            "qty": getattr(item, "qty", 1),
            "line_total": float(getattr(item, "line_total", 0.0)),
            "is_food": bool(getattr(item, "is_food", True)),
            "tax_code": getattr(item, "tax_code", None),
            "unit_price": getattr(item, "unit_price", None),
        }
    return dict(item)


# ---------------------------------------------------------------------------
# 1. normalize_item
# ---------------------------------------------------------------------------

def normalize_item(item: Any, store: str) -> Dict[str, Any]:
    """
    Bridge Aldi / Kroger item shapes into a common dict.

    Aldi key  = SKU string
    Kroger key = uppercase name with whitespace collapsed + aliases expanded

    Returns:
      {
        item_key: str,
        display_name: str,
        store: str,          # "ALDI" | "KROGER"
        qty: int | float,
        line_total: float,
        is_food: bool,
        # optional passthrough for flagging
        category_confidence: str | None,   # "high" | "low" | None
        unsure: bool,
      }
    """
    store = store.upper().strip()
    d = _item_to_dict(item)

    qty = d.get("qty", 1) or 1
    line_total = float(d.get("line_total", 0.0))
    is_food = bool(d.get("is_food", True))

    if store == "ALDI":
        sku = str(d.get("sku") or "").strip()
        name = str(d.get("name") or "").strip()
        item_key = sku
        display_name = name
        # Aldi tax code already sets is_food; keep parser value.
    elif store == "KROGER":
        name = str(d.get("name") or "").strip()
        item_key, display_name = _normalize_kroger_name(name)
        # Keyword food filter: override when name is clearly non-food
        # (parser defaults is_food=True when the trailing tax code is absent).
        if NON_FOOD_KEYWORDS.search(name) or NON_FOOD_KEYWORDS.search(item_key):
            is_food = False
    else:
        raise ValueError(f"Unknown store: {store!r}")

    # Category confidence: parsers do not yet emit a category.
    # Default to None (unknown). Callers / future category layer can set
    # "high" / "low". We never invent a frozen/pantry label here.
    category_confidence = d.get("category_confidence")  # may be None

    unsure = bool(d.get("unsure", False))

    out = {
        "item_key": item_key,
        "display_name": display_name,
        "store": store,
        "qty": qty,
        "line_total": round(line_total, 2),
        "is_food": is_food,
        "category_confidence": category_confidence,
        "unsure": unsure,
    }
    # Optional passthrough fields for UI / datastore
    if d.get("sku") is not None:
        out["sku"] = d.get("sku")
    if d.get("unit_price") is not None:
        out["unit_price"] = d.get("unit_price")
    if d.get("tax_code") is not None:
        out["tax_code"] = d.get("tax_code")
    return out


# ---------------------------------------------------------------------------
# 2. merge_receipt
# ---------------------------------------------------------------------------

def merge_receipt(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge rows that share the same item_key *within one receipt*.

    Two KRO HASHBRN PATTIE lines → one row qty=2, total summed.
    Never merges across stores (caller must keep receipts separate).
    Preserves first-seen display_name and store.
    """
    if not items:
        return []

    buckets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for raw in items:
        # Accept either already-normalized dicts or raw parser items
        # (if store is present we treat as normalized; otherwise require
        # caller to have called normalize_item first).
        if "item_key" not in raw:
            raise ValueError(
                "merge_receipt expects normalized items "
                "(call normalize_item first)"
            )

        key = raw["item_key"]
        if key not in buckets:
            buckets[key] = {
                "item_key": key,
                "display_name": raw["display_name"],
                "store": raw["store"],
                "qty": 0,
                "line_total": 0.0,
                "is_food": raw.get("is_food", True),
                "category_confidence": raw.get("category_confidence"),
                "unsure": bool(raw.get("unsure", False)),
            }
            order.append(key)

        b = buckets[key]
        b["qty"] += raw.get("qty", 1) or 1
        b["line_total"] = round(b["line_total"] + float(raw.get("line_total", 0)), 2)
        # propagate unsure / low confidence if any row had it
        if raw.get("unsure"):
            b["unsure"] = True
        if raw.get("category_confidence") == "low":
            b["category_confidence"] = "low"

    return [buckets[k] for k in order]


# ---------------------------------------------------------------------------
# 3. monthly_summary
# ---------------------------------------------------------------------------

def _month_key(date_str: str) -> str:
    """Accept '2026-08-17' or '2026-08' → '2026-08'."""
    date_str = str(date_str).strip()
    if re.match(r"^\d{4}-\d{2}$", date_str):
        return date_str
    m = re.match(r"^(\d{4}-\d{2})", date_str)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract YYYY-MM from date: {date_str!r}")


def monthly_summary(receipts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate a list of parsed receipts into per-month food spending.

    Each receipt dict must contain:
      {
        "date": "2026-08-17",          # or "2026-08"
        "store": "ALDI" | "KROGER",
        "items": [ ... parser items or already-normalized ... ],
        "unsure": [str, ...] optional  # from Kroger parse
      }

    Output:
      {
        "months": [
          {
            "month": "2026-08",
            "total_food_spend": float,
            "by_store": {"ALDI": x, "KROGER": y},
            "top_items": [
              {"item_key", "display_name", "qty", "total_spent"}, ...
            ],  # top 10 by total_spent
            "flagged": [ ... items with low confidence or unsure ... ]
          },
          ...
        ]
      }
    """
    # month → accumulator
    # items are tracked globally per month (across receipts) for top_items,
    # but we still process each receipt independently for merge.
    month_food: Dict[str, float] = defaultdict(float)
    month_by_store: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # item_key → {display_name, qty, total_spent, store, ...}
    month_items: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    month_flagged: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for receipt in receipts:
        date = receipt["date"]
        store = receipt["store"].upper().strip()
        month = _month_key(date)
        raw_items = receipt.get("items") or []
        receipt_unsure = receipt.get("unsure") or []

        # Normalize every item, keep only food for spend totals
        normalized = [normalize_item(it, store) for it in raw_items]

        # Merge within this receipt only
        merged = merge_receipt(normalized)

        for it in merged:
            if not it.get("is_food", True):
                # still surface non-food in flagged if unsure, otherwise skip spend
                if it.get("unsure") or it.get("category_confidence") == "low":
                    month_flagged[month].append({
                        "item_key": it["item_key"],
                        "display_name": it["display_name"],
                        "store": store,
                        "qty": it["qty"],
                        "total_spent": it["line_total"],
                        "reason": "non-food or low-confidence",
                    })
                continue

            spend = float(it["line_total"])
            month_food[month] += spend
            month_by_store[month][store] += spend

            key = it["item_key"]
            # Cross-receipt accumulation for top_items (same key may appear
            # on multiple trips — we intentionally aggregate for ranking).
            # Deli Turkey on two different Aldi trips stays one bucket in
            # top_items; the test only requires they not be merged *inside*
            # a single receipt incorrectly, and that per-receipt merge works.
            if key not in month_items[month]:
                month_items[month][key] = {
                    "item_key": key,
                    "display_name": it["display_name"],
                    "qty": 0,
                    "total_spent": 0.0,
                    "store": store,
                }
            bucket = month_items[month][key]
            bucket["qty"] += it["qty"]
            bucket["total_spent"] = round(bucket["total_spent"] + spend, 2)

            # Flag low-confidence / unsure rows
            if it.get("category_confidence") == "low" or it.get("unsure"):
                month_flagged[month].append({
                    "item_key": key,
                    "display_name": it["display_name"],
                    "store": store,
                    "qty": it["qty"],
                    "total_spent": spend,
                    "reason": "low category confidence or parse unsure",
                })

        # Surface receipt-level unsure lines (Kroger)
        for u in receipt_unsure:
            month_flagged[month].append({
                "item_key": None,
                "display_name": str(u),
                "store": store,
                "qty": None,
                "total_spent": None,
                "reason": "parse unsure line",
            })

    # Build sorted output
    months_out: List[Dict[str, Any]] = []
    for month in sorted(month_food.keys()):
        items = list(month_items[month].values())
        items.sort(key=lambda x: (-x["total_spent"], x["item_key"]))
        top10 = [
            {
                "item_key": i["item_key"],
                "display_name": i["display_name"],
                "qty": i["qty"],
                "total_spent": round(i["total_spent"], 2),
            }
            for i in items[:10]
        ]

        by_store = {
            s: round(v, 2)
            for s, v in sorted(month_by_store[month].items())
        }

        months_out.append({
            "month": month,
            "total_food_spend": round(month_food[month], 2),
            "by_store": by_store,
            "top_items": top10,
            "flagged": month_flagged[month],
        })

    return {"months": months_out}
