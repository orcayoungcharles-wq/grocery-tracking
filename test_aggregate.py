#!/usr/bin/env python3
"""
Tests for aggregate.py using the five real receipts + the 9.14 shampoo receipt.

Assertions required by the spec:
- August food total excludes tax and shampoo
- Campbell's Soup SKUs 733530 and 733540 stay SEPARATE items
- Two hash brown lines merge into qty=2
- Deli Turkey SKU 399942 merges correctly within its two 8.17 rows
  AND reports separately per receipt (not merged across trips)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure local imports work when run from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aldi_parser_shim import parseAldi, ALDI_8_17, ALDI_9_1  # noqa: E402
from kroger_parser_shim import parseKroger, text_823, text_826, text_826b, text_914  # noqa: E402
from aggregate import normalize_item, merge_receipt, monthly_summary  # noqa: E402


def _aldi_items(receipt):
    return list(receipt.items)


def _kroger_items(result):
    return result["items"]


def test_normalize_aldi():
    r = parseAldi(ALDI_8_17)
    it = r.items[0]
    n = normalize_item(it, "ALDI")
    assert n["item_key"] == "399942"
    assert n["display_name"] == "Deli Sliced Turkey"
    assert n["store"] == "ALDI"
    assert n["qty"] == 1
    assert abs(n["line_total"] - 3.19) < 0.001
    assert n["is_food"] is True
    print("PASS normalize_item Aldi")


def test_normalize_kroger_aliases():
    raw = {"name": "KRO HASHBRN PATTIE", "qty": 1, "line_total": 3.79, "is_food": True}
    n = normalize_item(raw, "KROGER")
    assert n["item_key"] == "KRO HASHBROWN PATTIE"
    assert "HASHBRN" not in n["item_key"]
    assert n["display_name"] == "KRO HASHBRN PATTIE"
    print("PASS normalize_item Kroger aliases (HASHBRN→HASHBROWN)")

    raw2 = {"name": "KRO 100% WW RT BREPC", "qty": 1, "line_total": 1.99, "is_food": True}
    n2 = normalize_item(raw2, "KROGER")
    # WW → WHOLE WHEAT, RT → RTD, BREPC → BREAD
    assert "WHOLE WHEAT" in n2["item_key"]
    assert "RTD" in n2["item_key"] or "BREAD" in n2["item_key"]
    print(f"  expanded key: {n2['item_key']!r}")
    print("PASS normalize_item Kroger WW/RT/BREPC expansion")


def test_merge_hashbrowns():
    """Two KRO HASHBRN PATTIE lines → one row qty=2 total=7.58"""
    result = parseKroger(text_826)
    items = result["items"]
    assert len(items) == 3  # bacon? no — burgers + 2 hash brown
    # text_826: BEEF BURGERS + 2x HASHBRN
    normalized = [normalize_item(it, "KROGER") for it in items]
    merged = merge_receipt(normalized)
    hash_rows = [m for m in merged if "HASHBROWN" in m["item_key"]]
    assert len(hash_rows) == 1, f"expected 1 merged hashbrown row, got {hash_rows}"
    h = hash_rows[0]
    assert h["qty"] == 2, f"expected qty=2, got {h['qty']}"
    assert abs(h["line_total"] - 7.58) < 0.001, f"expected 7.58, got {h['line_total']}"
    print("PASS merge_receipt: two hash brown lines → qty=2 total=7.58")


def test_merge_turkey_within_receipt():
    """Two Deli Turkey lines on Aldi 8.17 share SKU → merge to qty=2."""
    r = parseAldi(ALDI_8_17)
    normalized = [normalize_item(it, "ALDI") for it in r.items]
    merged = merge_receipt(normalized)
    turkey = [m for m in merged if m["item_key"] == "399942"]
    assert len(turkey) == 1
    assert turkey[0]["qty"] == 2
    assert abs(turkey[0]["line_total"] - 6.38) < 0.001
    print("PASS merge_receipt: Deli Turkey 399942 merges within 8.17 (qty=2)")


def test_campbell_soups_stay_separate():
    """Campbell's Soup SKUs 733530 and 733540 must remain distinct."""
    r = parseAldi(ALDI_9_1)
    normalized = [normalize_item(it, "ALDI") for it in r.items]
    merged = merge_receipt(normalized)
    keys = {m["item_key"] for m in merged}
    assert "733530" in keys
    assert "733540" in keys
    s530 = next(m for m in merged if m["item_key"] == "733530")
    s540 = next(m for m in merged if m["item_key"] == "733540")
    assert abs(s530["line_total"] - 4.96) < 0.001
    assert abs(s540["line_total"] - 4.96) < 0.001
    assert s530["qty"] == 2
    assert s540["qty"] == 2
    print("PASS Campbell's Soup 733530 and 733540 stay SEPARATE")


def test_turkey_not_merged_across_receipts():
    """
    Turkey on 8.17 (two lines → qty=2) and turkey on 9.1 (qty=3) are
    separate receipts. monthly_summary top_items may aggregate them for
    ranking, but we verify each receipt's merge is independent and that
    we can still see both contributions.
    """
    r1 = parseAldi(ALDI_8_17)
    r2 = parseAldi(ALDI_9_1)

    norm1 = [normalize_item(it, "ALDI") for it in r1.items]
    norm2 = [normalize_item(it, "ALDI") for it in r2.items]
    m1 = merge_receipt(norm1)
    m2 = merge_receipt(norm2)

    t1 = next(x for x in m1 if x["item_key"] == "399942")
    t2 = next(x for x in m2 if x["item_key"] == "399942")
    assert t1["qty"] == 2 and abs(t1["line_total"] - 6.38) < 0.001
    assert t2["qty"] == 3 and abs(t2["line_total"] - 9.57) < 0.001
    # They are independent objects / independent merges
    assert t1 is not t2
    print("PASS Deli Turkey reports separately per receipt (8.17 qty=2, 9.1 qty=3)")


def test_august_food_excludes_tax_and_shampoo():
    """
    Build monthly_summary from all receipts.
    August food total must exclude:
      - tax
      - shampoo (non-food on 9.14)
      - paper bags (non-food on Aldi 9.1)
    """
    receipts = [
        {
            "date": "2026-08-17",
            "store": "ALDI",
            "items": _aldi_items(parseAldi(ALDI_8_17)),
        },
        {
            "date": "2026-08-23",
            "store": "KROGER",
            "items": _kroger_items(parseKroger(text_823)),
            "unsure": parseKroger(text_823).get("unsure", []),
        },
        {
            "date": "2026-08-26",
            "store": "KROGER",
            "items": _kroger_items(parseKroger(text_826)),
        },
        {
            "date": "2026-08-26",
            "store": "KROGER",
            "items": _kroger_items(parseKroger(text_826b)),
        },
        {
            "date": "2026-09-01",
            "store": "ALDI",
            "items": _aldi_items(parseAldi(ALDI_9_1)),
        },
        {
            "date": "2026-09-14",
            "store": "KROGER",
            "items": _kroger_items(parseKroger(text_914)),
            "unsure": parseKroger(text_914).get("unsure", []),
        },
    ]

    summary = monthly_summary(receipts)
    months = {m["month"]: m for m in summary["months"]}

    assert "2026-08" in months
    assert "2026-09" in months

    aug = months["2026-08"]
    sep = months["2026-09"]

    # --- Manual expected food totals ---
    # Aldi 8.17: all 6 items food → 18.60
    aldi_817_food = 18.60

    # Kroger 8.23: all food (BACON, PEPPERONI, 2x BUNS, WW bread, 2x FRANKS)
    # line totals as printed (savings NOT subtracted): 2.99+2.99+2.99+2.99+1.99+1.59+1.59 = 17.13
    k823 = parseKroger(text_823)
    k823_food = sum(i["line_total"] for i in k823["items"] if i["is_food"])
    assert abs(k823_food - 17.13) < 0.02, f"k823 food={k823_food}"

    # Kroger 8.26: BURGERS 10.00 + 2x HASHBRN 3.79 = 17.58
    k826 = parseKroger(text_826)
    k826_food = sum(i["line_total"] for i in k826["items"] if i["is_food"])
    assert abs(k826_food - 17.58) < 0.02

    # Kroger 8.26b: 3.00 + 6.00 + 6.00 = 15.00
    k826b = parseKroger(text_826b)
    k826b_food = sum(i["line_total"] for i in k826b["items"] if i["is_food"])
    assert abs(k826b_food - 15.00) < 0.02

    expected_aug = round(aldi_817_food + k823_food + k826_food + k826b_food, 2)
    print(f"  expected August food: {expected_aug}")
    print(f"  actual   August food: {aug['total_food_spend']}")
    assert abs(aug["total_food_spend"] - expected_aug) < 0.02, (
        f"August food mismatch: got {aug['total_food_spend']}, expected {expected_aug}"
    )

    # Shampoo must NOT be in food totals
    # September: Aldi 9.1 food = subtotal 97.45 - bags 0.56 = 96.89
    # Kroger 9.14: lasagna 8.49 + rice 3.29 = 11.78 (shampoo 13.99 is non-food)
    aldi_91 = parseAldi(ALDI_9_1)
    aldi_91_food = sum(i.line_total for i in aldi_91.items if i.is_food)
    assert abs(aldi_91_food - 96.89) < 0.02, f"aldi 9.1 food={aldi_91_food}"

    k914 = parseKroger(text_914)
    # Parser may mark shampoo is_food=True (no trailing tax code).
    # Aggregation layer applies keyword non-food filter.
    norm_914 = [normalize_item(it, "KROGER") for it in k914["items"]]
    k914_food = sum(i["line_total"] for i in norm_914 if i["is_food"])
    assert abs(k914_food - 11.78) < 0.02, f"k914 food after normalize={k914_food}"
    shampoo = [i for i in norm_914 if "SHAMPOO" in i["display_name"].upper()]
    assert len(shampoo) == 1
    assert shampoo[0]["is_food"] is False, "shampoo must be non-food after normalize"

    expected_sep = round(aldi_91_food + k914_food, 2)
    print(f"  expected September food: {expected_sep}")
    print(f"  actual   September food: {sep['total_food_spend']}")
    assert abs(sep["total_food_spend"] - expected_sep) < 0.02

    # by_store checks
    assert "ALDI" in aug["by_store"]
    assert "KROGER" in aug["by_store"]
    assert abs(aug["by_store"]["ALDI"] - 18.60) < 0.02
    assert abs(sep["by_store"]["ALDI"] - 96.89) < 0.02
    assert abs(sep["by_store"]["KROGER"] - 11.78) < 0.02

    # top_items non-empty
    assert len(aug["top_items"]) > 0
    assert len(sep["top_items"]) > 0

    # Campbell soups appear as separate keys in September top/all items
    sep_keys = {t["item_key"] for t in sep["top_items"]}
    # they may or may not both be in top-10 depending on spend; force check via full month data
    # Re-run a targeted merge for 9.1 to confirm separation is preserved in pipeline
    norm_91 = [normalize_item(it, "ALDI") for it in aldi_91.items]
    merged_91 = merge_receipt(norm_91)
    soup_keys = {m["item_key"] for m in merged_91 if "73353" in m["item_key"] or "73354" in m["item_key"]}
    assert soup_keys == {"733530", "733540"}

    print("PASS August food total excludes tax and shampoo")
    print("PASS September food total excludes bags and shampoo")
    print(f"  August  by_store: {aug['by_store']}")
    print(f"  September by_store: {sep['by_store']}")
    print(f"  August top_items (first 5): {aug['top_items'][:5]}")


def main():
    print("=" * 70)
    print("aggregate.py test suite")
    print("=" * 70)

    test_normalize_aldi()
    test_normalize_kroger_aliases()
    test_merge_hashbrowns()
    test_merge_turkey_within_receipt()
    test_campbell_soups_stay_separate()
    test_turkey_not_merged_across_receipts()
    test_august_food_excludes_tax_and_shampoo()

    print("=" * 70)
    print("ALL ASSERTIONS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()
