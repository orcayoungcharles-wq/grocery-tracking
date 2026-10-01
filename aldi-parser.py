#!/usr/bin/env python3
"""
Aldi receipt parser.

Follows the locked rules from audit.md / decisions.md / aldi-parser.md:

1. Stop parsing at SUBTOTAL — footer never becomes items.
2. Item line starts with 6-digit SKU. Second-to-last token = LINE TOTAL.
   Last token = tax code (FA/FB/NB).
3. Child line (N x unit_price) merges into parent, never emits separately.
4. qty=1 defaults to 1. Duplicate identical-SKU rows stay separate.
5. is_food = true unless tax code starts with N.

Reconciles when sum(line_totals) == printed subtotal (all items, including
non-food). Spending metric elsewhere may still exclude non-food.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AldiItem:
    sku: str
    name: str
    qty: int
    unit_price: float
    line_total: float
    tax_code: str
    is_food: bool


@dataclass
class AldiReceipt:
    items: List[AldiItem]
    subtotal: Optional[float]
    reconciles: bool
    raw_item_count: int  # number of item lines before child merge (for debug)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Item line: starts with 6-digit SKU, ends with <price> <tax_code>
ITEM_RE = re.compile(
    r"^(\d{6})\s+(.+?)\s+([\d.]+)\s+([A-Za-z]{1,3})\s*$"
)

# Child line: "N x 0.89" or "6 x  0.89" (variable spaces)
CHILD_RE = re.compile(
    r"^\s*(\d+)\s*x\s+([\d.]+)\s*$", re.IGNORECASE
)

# Subtotal line – stop here
SUBTOTAL_RE = re.compile(r"^\s*SUBTOTAL\s+([\d.]+)", re.IGNORECASE)


def parseAldi(text: str) -> AldiReceipt:
    """
    Parse raw OCR / plain-text Aldi receipt into structured items.

    Returns AldiReceipt with:
      - items: list of AldiItem (children already merged)
      - subtotal: float from the SUBTOTAL line (or None)
      - reconciles: True iff sum(item.line_total) == subtotal (within 0.01)
      - raw_item_count: how many parent item lines were seen
    """
    items: List[AldiItem] = []
    subtotal: Optional[float] = None
    current: Optional[AldiItem] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 1. Stop at SUBTOTAL
        m_sub = SUBTOTAL_RE.match(line)
        if m_sub:
            subtotal = float(m_sub.group(1))
            break

        # 3. Child line → merge into the most recent parent
        m_child = CHILD_RE.match(line)
        if m_child and current is not None:
            qty = int(m_child.group(1))
            unit = float(m_child.group(2))
            current.qty = qty
            current.unit_price = unit
            # Keep the parent's printed line_total (authoritative)
            continue

        # 2. Item line: SKU + name + line_total + tax_code
        m_item = ITEM_RE.match(line)
        if m_item:
            sku = m_item.group(1)
            name = m_item.group(2).strip()
            line_total = float(m_item.group(3))
            tax_code = m_item.group(4).upper()

            # Alternative robust extraction (token-based) – kept as sanity check
            # tokens = line.split()
            # if len(tokens) >= 3 and tokens[0].isdigit() and len(tokens[0]) == 6:
            #     tax_code = tokens[-1].upper()
            #     line_total = float(tokens[-2])
            #     name = " ".join(tokens[1:-2])

            is_food = not tax_code.startswith("N")

            current = AldiItem(
                sku=sku,
                name=name,
                qty=1,               # default; overwritten by child if present
                unit_price=line_total,  # default for qty=1
                line_total=line_total,
                tax_code=tax_code,
                is_food=is_food,
            )
            items.append(current)
            continue

        # Anything else (header, cashier, etc.) is ignored until SUBTOTAL

    # Compute reconciliation
    computed = sum(i.line_total for i in items)
    reconciles = (
        subtotal is not None
        and abs(computed - subtotal) < 0.015  # allow 1-cent float noise
    )

    return AldiReceipt(
        items=items,
        subtotal=subtotal,
        reconciles=reconciles,
        raw_item_count=len(items),
    )


# ---------------------------------------------------------------------------
# Pretty-print helper (optional)
# ---------------------------------------------------------------------------

def print_receipt(receipt: AldiReceipt) -> None:
    print(f"{'SKU':<8} {'Name':<28} {'Qty':>3} {'Unit':>7} {'Total':>7} {'Tax':>4} Food")
    print("-" * 70)
    for it in receipt.items:
        print(
            f"{it.sku:<8} {it.name[:28]:<28} {it.qty:>3} "
            f"{it.unit_price:>7.2f} {it.line_total:>7.2f} {it.tax_code:>4} "
            f"{'Y' if it.is_food else 'N'}"
        )
    print("-" * 70)
    print(f"Subtotal (printed): {receipt.subtotal}")
    print(f"Sum of line_totals: {sum(i.line_total for i in receipt.items):.2f}")
    print(f"Reconciles:         {receipt.reconciles}")
    print(f"Item rows:          {receipt.raw_item_count}")


# ---------------------------------------------------------------------------
# Test receipts (exact OCR text)
# ---------------------------------------------------------------------------

ALDI_8_17 = """ALDI
Store #57
5959 Lorven Drive
Milford, OH
https://help.aldi.us
Your cashier today was Nicholas
399942 Deli Sliced Turkey     3.19 FA
399942 Deli Sliced Turkey     3.19 FA
383210 Baby Swiss Slices      3.49 FA
383210 Baby Swiss Slices      3.49 FA
567975 Red Thunder Energy     3.29 FB
388120 100% Wheat Bread       1.95 FA
SUBTOTAL                     18.60
B-Taxable @6.750%             0.22
A-Taxable @0.00%              0.00
AMOUNT DUE                   18.82
ROUNDING ADJUSTMENT          -0.02
T O T A L                $ 18.80
6 ITEMS
Cash                       $ 20.00
CHANGE DUE                 $ -1.20
*6656 FE69/003/004 08/17/26 04:14PM

Sign up for ALDI emails
for a sneak peek on the weekly ad!
www.aldi.us/signup"""

ALDI_9_1 = """ALDI
Store #025
5505 Ridge Ave
Cincinnati
https://help.aldi.us
593037 Arizona Big Can           5.34 FB
6 x  0.89
565534 Paper Bags                0.56 NB
4 x  0.14
466810 Chicken Noodle            6.76 FA
4 x  1.69
733530 Campbell's Soup           4.96 FA
2 x  2.48
733540 Campbell's Soup           4.96 FA
2 x  2.48
576717 RTS Rice                  2.58 FA
2 x  1.29
576712 RTS Rice                  2.58 FA
2 x  1.29
651744 Ready Rice                3.56 FA
4 x  0.89
400739 12 oz Hot Dogs            1.98 FA
2 x  0.99
481342 Hot Dog Buns              2.78 FA
2 x  1.39
383324 Flour Tortillas           1.95 FA
399942 Deli Sliced Turkey        9.57 FA
3 x  3.19
383210 Baby Swiss Slices         3.49 FA
398792 Premium Sausage           5.78 FA
2 x  2.89
399338 Thick Cut Bacon           7.90 FA
2 x  3.95
270039 Hash Brown Patties        4.99 FA
382931 Sour Cream                1.75 FA
343665 Skyline Chili             5.95 FA
367839 Lasagna 38oz              5.99 FA
716326 Spicy Marg or Pine        1.59 FA
488858 Jumbo Blueberries         4.59 FA
371531 Pretzel Sticks/Mini       2.19 FA
416943 Whole Milk                2.71 FA
382251 Yellow Mustard 20oz       0.99 FA
282819 36 oz. Ketchup            1.95 FA
SUBTOTAL                        97.45
B:Taxable @7.800%                0.46
A:Taxable @0.00%                 0.00
AMOUNT DUE                      97.91
T O T A L                   $ 97.91
49 ITEMS
Debit Card
*5156 F507/005/802 09/01/26 01:55PM

Sign up for ALDI emails
for a sneak peek on the weekly ad!
http://www.aldi.us/signup
97.91
Debit
********************6539 PIN
09/01/26 13:55 Ref/Seq # 132785
Trace # 132785
Auth # 931116
AID A0000000042203
TVR 0000000000
IAD 0110A00012200000000000000000
EntryMode 07"""


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

def run_tests() -> None:
    print("=" * 70)
    print("TEST: Aldi 8.17 (all qty=1, duplicate SKUs stay separate)")
    print("=" * 70)
    r1 = parseAldi(ALDI_8_17)
    print_receipt(r1)
    assert r1.reconciles is True, "8.17 must reconcile"
    assert len(r1.items) == 6, f"expected 6 items, got {len(r1.items)}"
    assert r1.items[0].sku == "399942" and r1.items[0].qty == 1
    assert r1.items[4].tax_code == "FB" and r1.items[4].is_food is True
    assert all(i.qty == 1 for i in r1.items)
    print("PASS\n")

    print("=" * 70)
    print("TEST: Aldi 9.1 (child lines merge, non-food bags, mixed qty)")
    print("=" * 70)
    r2 = parseAldi(ALDI_9_1)
    print_receipt(r2)
    assert r2.reconciles is True, "9.1 must reconcile"
    # 25 parent lines (the 49 ITEMS counts units, not rows)
    assert len(r2.items) == 25, f"expected 25 items, got {len(r2.items)}"

    # Arizona Big Can: 6 x 0.89 → line_total 5.34
    az = r2.items[0]
    assert az.sku == "593037"
    assert az.qty == 6
    assert abs(az.unit_price - 0.89) < 0.001
    assert abs(az.line_total - 5.34) < 0.001
    assert az.tax_code == "FB" and az.is_food is True

    # Paper Bags: non-food
    bags = r2.items[1]
    assert bags.sku == "565534"
    assert bags.qty == 4
    assert bags.tax_code == "NB" and bags.is_food is False
    assert abs(bags.line_total - 0.56) < 0.001

    # Flour Tortillas: qty=1 (no child)
    tortillas = next(i for i in r2.items if i.sku == "383324")
    assert tortillas.qty == 1
    assert abs(tortillas.unit_price - 1.95) < 0.001

    # Turkey: 3 x 3.19
    turkey = next(i for i in r2.items if i.sku == "399942")
    assert turkey.qty == 3
    assert abs(turkey.unit_price - 3.19) < 0.001
    assert abs(turkey.line_total - 9.57) < 0.001

    print("PASS\n")
    print("All tests passed.")


if __name__ == "__main__":
    run_tests()
