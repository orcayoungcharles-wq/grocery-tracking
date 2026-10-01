"""
test_pipeline.py — End-to-end test: parse receipts -> store -> aggregate -> shopping list
"""

import os
import sys
from pathlib import Path
import aldi_parser_shim as aldi_parser
import kroger_parser_shim as kroger_parser
import datastore


def find_receipts():
    txt_files = [f for f in os.listdir('.') if f.endswith('.txt')]
    aldi_files = sorted([f for f in txt_files if 'aldi' in f.lower()])
    kroger_files = sorted([f for f in txt_files if 'kroger' in f.lower()])
    return aldi_files, kroger_files


def detect_store(filename):
    if 'aldi' in filename.lower():
        return 'aldi'
    elif 'kroger' in filename.lower():
        return 'kroger'
    else:
        raise ValueError(f"Cannot determine store from filename: {filename}")


def parse_receipt(fname, store):
    """Parse a receipt and return a dict matching datastore.add_receipt expectations."""
    with open(fname, 'r', encoding='utf-8') as f:
        text = f.read()

    if store == 'aldi':
        result = aldi_parser.parse_receipt(text)
        # AldiReceipt object: .items (list of AldiItem), .subtotal, .reconciles
        items = result.items
        subtotal = result.subtotal
        reconciles = result.reconciles
        tax = round(subtotal - sum(i.line_total for i in items), 2) if subtotal else 0
    else:
        result = kroger_parser.parse_receipt(text)
        # Kroger returns dict: items, subtotal, printed_subtotal, printed_tax, reconciles
        items = result['items']
        subtotal = result['subtotal']
        reconciles = result['reconciles']
        # Prefer explicit printed_tax; fall back to balance - subtotal
        tax = result.get('printed_tax')
        if tax is None:
            bal = result.get('printed_balance')
            tax = round((bal or 0) - subtotal, 2) if bal is not None else 0.0
        tax = round(float(tax or 0), 2)
    # Normalize items to uniform dicts with item_key
    normalized = []
    for item in items:
        if hasattr(item, 'to_dict') or hasattr(item, '__dict__'):
            # AldiItem dataclass — identity is SKU
            name = item.name
            sku = getattr(item, 'sku', None)
            item_key = str(sku) if sku else name.strip().lower()
            normalized.append({
                'item_key': item_key,
                'name': name,
                'sku': sku,
                'qty': item.qty,
                'unit_price': item.unit_price,
                'line_total': item.line_total,
                'tax_code': getattr(item, 'tax_code', None),
                'is_food': item.is_food,
            })
        else:
            # Kroger dict — identity is normalized name
            d = dict(item)
            name = d.get('name', '')
            d['item_key'] = name.strip().lower()
            normalized.append(d)

    # trip_id distinguishes same-day trips (e.g. kroger-8.26-am vs kroger-8.26-pm)
    return {
        'store': store,
        'date': extract_date(fname),
        'trip_id': Path(fname).stem,
        'items': normalized,
        'subtotal': subtotal,
        'tax': round(tax, 2),
        'total': round(subtotal + tax, 2),
        'reconciled': reconciles,
        'filename': fname,
    }


def extract_date(filename):
    import re
    m = re.search(r'(\d{1,2})\.(\d{1,2})', filename)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        return f"2025-{month:02d}-{day:02d}"
    return "2025-01-01"


def run_pipeline():
    print("=" * 60)
    print("GROCERY TRACKING PIPELINE TEST")
    print("=" * 60)

    aldi_files, kroger_files = find_receipts()
    print(f"\nFound {len(aldi_files)} Aldi receipts: {aldi_files}")
    print(f"Found {len(kroger_files)} Kroger receipts: {kroger_files}")

    # --- STEP 1: Parse every receipt ---
    print("\n--- STEP 1: Parsing receipts ---")
    parsed_receipts = []

    for fname in aldi_files + kroger_files:
        store = detect_store(fname)
        result = parse_receipt(fname, store)
        line_sum = round(sum(i['line_total'] for i in result['items']), 2)
        print(f"  {fname:25s} | {store:6s} | {len(result['items']):2d} items | subtotal=${result['subtotal']:.2f} | reconciled={result['reconciled']}")
        parsed_receipts.append(result)

    print(f"\nAll {len(parsed_receipts)} receipts parsed.")

    # --- STEP 2: Load into datastore ---
    print("\n--- STEP 2: Loading into datastore ---")
    datastore.reset_data()

    for r in parsed_receipts:
        datastore.add_receipt(r)
        print(f"  Stored: {r['filename']} ({r['store']}, {r['date']}, ${r['subtotal']:.2f})")

    # --- STEP 3: Verify stored data ---
    print("\n--- STEP 3: Verifying aggregates ---")
    monthly = datastore.load_monthly_spending()
    item_hist = datastore.load_item_history()
    frequencies = datastore.get_item_frequencies()

    for month in sorted(monthly.keys()):
        actual = monthly[month].get('food', 0.0)
        by_store = monthly[month].get('by_store', {})
        print(f"  {month} food spend: ${actual:.2f}  by_store={by_store}")

    print(f"  Total item-history rows: {len(item_hist)}")
    shampoo = [e for e in item_hist if 'SHAMPOO' in e.get('name', '').upper()]
    bags = [e for e in item_hist if 'BAG' in e.get('name', '').upper()]
    print(f"  Shampoo in history: {[(e['name'], e.get('is_food')) for e in shampoo]}")
    print(f"  Bags in history: {[(e['name'], e.get('is_food')) for e in bags]}")

    # --- Assertions (regression guards) ---
    print("\n--- ASSERTIONS ---")
    aug = monthly.get('2025-08', {}).get('food', 0.0)
    sep = monthly.get('2025-09', {}).get('food', 0.0)
    total_trips = frequencies[0]['total_trips'] if frequencies else 0

    assert abs(aug - 68.31) < 0.01, f"August food expected $68.31, got ${aug}"
    print(f"  PASS  August food == $68.31 (got ${aug:.2f})")

    assert abs(sep - 108.67) < 0.01, f"September food expected $108.67, got ${sep}"
    print(f"  PASS  September food == $108.67 (got ${sep:.2f})")

    assert total_trips == 6, f"Expected 6 distinct trips, got {total_trips}"
    print(f"  PASS  total_trips == 6")

    assert len(shampoo) >= 1, "Shampoo missing from item history"
    assert all(e.get('is_food') is False for e in shampoo), \
        f"Shampoo must be non-food, got {[(e['name'], e.get('is_food')) for e in shampoo]}"
    print(f"  PASS  shampoo is non-food ({len(shampoo)} history row(s))")

    # Duplicate lines on one receipt must still count as 1 trip
    # BAR-S FRANKS appears twice on kroger-8.23 → trip_count must be 1
    franks = [f for f in frequencies if 'FRANK' in f['name'].upper()]
    for f in franks:
        assert f['trip_count'] == 1, \
            f"{f['name']} appears on 1 trip but trip_count={f['trip_count']}"
        print(f"  PASS  {f['name']}: trip_count=1 (duplicate lines collapsed)")

    # --- STEP 4: Shopping list ---
    print("\n--- STEP 4: Shopping list (threshold=30%) ---")
    print(f"  (total trips = {total_trips})")
    sl = datastore.get_shopping_list(threshold=30.0)
    if sl:
        print(f"  {'ITEM':35s} {'BOUGHT ON':>14s} {'FREQ%':>7s} {'AVG QTY':>8s}")
        print("  " + "-" * 70)
        for entry in sorted(sl, key=lambda e: (-e['frequency_pct'], -e['trip_count'])):
            bought = f"{entry['trip_count']} of {entry['total_trips']} trips"
            print(f"  {entry['name'][:35]:35s} {bought:>14s} {entry['frequency_pct']:>6.1f}% {entry['avg_qty']:>8.1f}")
    else:
        print("  (empty — no items appear on >= 30% of trips)")

    # Full frequency dump for eyeballing
    print("\n--- All item frequencies (top 15) ---")
    print(f"  {'ITEM':35s} {'BOUGHT ON':>14s} {'FREQ%':>7s} {'FOOD':>5s}")
    print("  " + "-" * 70)
    for entry in frequencies[:15]:
        bought = f"{entry['trip_count']} of {entry['total_trips']} trips"
        food = "Y" if entry.get('is_food', True) else "N"
        print(f"  {entry['name'][:35]:35s} {bought:>14s} {entry['frequency_pct']:>6.1f}% {food:>5s}")

    print("\n" + "=" * 60)
    print("PIPELINE TEST COMPLETE — ALL ASSERTIONS PASSED")
    print("=" * 60)


if __name__ == '__main__':
    run_pipeline()