# Receipt Format Audit — Aldi + Kroger

Source: 5 real receipts (2 Aldi, 3 Kroger). No code written. This is the ground truth for parser design.

---

## ALDI

### Layout

Two behaviors in the same chain:

- **Small trip / staffed counter (8.17):** qty=1 implied. Duplicate items print as duplicate lines. No child line.
- **Larger trip (9.1):** qty>1 collapses to a child line under the parent: `N x unit_price`. qty=1 items have no child line.

Columns on an item line: `SKU  Name  LineTotal  TaxCode`
Child line (when present): `N x unit_price`

### Hidden fields that are easy to miss

| Field | Where it lives | Why easy to miss |
|---|---|---|
| 6-digit article # | Start of every item line | Looks like OCR noise; it is the stable item key |
| Tax code FA/FB/NB | After the price, same line | Last token is a letter, not money |
| Unit price + qty | Next line, only if qty>1, form `N x 0.89` | Not printed at all when qty=1 |
| Tax rate | Footer, not per item | 6.750% Milford vs 7.800% Cincinnati |
| Rounding | Cash only (−0.02 on 8.17) | Looks like a charge |
| Item count | Footer | Counts units (49), not SKU rows (25) |

**Weight / $/lb:** not present on either receipt. Every item is each-priced. Cannot evaluate weighable produce from this set.

### Tax-code pattern (holds on both receipts)

- First letter = merchandise class (F food, N nonfood)
- Second letter = tax group (A 0%, B taxable)

FB (Arizona, energy drink) and NB (bags) are the only B-taxable lines, and they explain the tax to the cent:
- 3.29 × 6.75% = 0.22
- (5.34 + 0.56) × 7.80% = 0.46

### Quantity vs price

Price in the right column is always the **line total**, never the unit price. Quantity is not on the item line. You cannot assume Aldi always prints `2 @ $1.50` — sometimes it just prints the item twice.

### Non-food line types

- Paper Bags (SKU 565534, 4 × $0.14, code NB) — the only non-food SKU seen.
- Footer: subtotal, tax buckets, amount due, rounding, tender, change, debit EMV stub, marketing.
- No deposits, pharmacy, gift cards observed.

---

## KROGER

### Promos and "what did they actually pay?"

On these three receipts, the number next to the item name is the **amount paid**. Do NOT subtract the `SC KROGER SAVINGS` line.

Proof on 8.23: 2.99×4 + 1.99 + 1.59×2 = 17.13, which matches the debit. Savings are vs. the non-card/regular price (bacon regular would be 2.99+1.80 = 4.79). TOTAL SAVINGS (20%) is 4.50 / 21.63 ≈ 21%, i.e. vs. pre-card, not vs. the printed lines.

**Promo form we DO have:** item line with paid price → next line `SC KROGER SAVINGS` with a positive dollar amount. No minus sign. A naive parser will treat $1.80 as a second product.

**PC** is a short code between name and price on some lines (bacon, buns). On the long bread name it glues on: `KRO 100% WW RT BREPC`. It is not a price.

### What this sample does NOT contain (cannot certify yet)

- 2 for $5.00
- BOGO / Buy 2 Get 1 Free
- Card Price $X vs Non-Card $Y as two printed prices on one item
- Manufacturer coupons with a negative amount
- Weighted lb @ $/lb

### Coupons vs items

In this set, matching is trivial: the savings line is immediately under the item it belongs to. Pepperoni has no savings line; everything else on 8.23 does. Footer TOTAL COUPONS $4.50 equals the sum of those six SC lines — recap, not a floating coupon to attach.

No coupon is orphaned. No coupon names a different product than the line above it.

### Non-food mixed in

None. Every Kroger item is flagged F, tax $0.00 all three trips (Ohio grocery exemption). No household, pharmacy, or gift cards. Bakery still rings as F.

---

## SIMPLE PARSE TEST: split newlines → name + last number

Rule under test: last number on the line is the paid price; name is whatever precedes it.

### Aldi — 1 of 2 receipts (50%)

| Receipt | Simple parse? | Why |
|---|---|---|
| 8.17 | Yes, if you stop at SUBTOTAL | All qty=1. Last number is the line total (3.19 FA → 3.19). Last token would fail (FA). |
| 9.1 | No | 14 child lines `6 x 0.89` etc. Last number is unit price. You invent 14 ghost items and inflate spend (e.g. Arizona 5.34 plus 0.89). |

A one-line regex skip of `^\s*\d+\s*x\s+[\d.]+` plus "stop at SUBTOTAL" would make both Aldi receipts parse. That is still rules, not vision.

**Trap if you don't skip the footer:** ROUNDING ADJUSTMENT -0.02, CHANGE DUE -1.20, tax 0.22 / 0.46 all look like items.

### Kroger — 2 of 3 receipts (67%) without a savings filter; 3 of 3 if you drop SC/KROGER SAVINGS/TAX/BALANCE/payment lines

| Receipt | Simple parse? | Why |
|---|---|---|
| 8.26 AM | Yes | 3 clean NAME PRICE F lines |
| 8.26 PM | Yes | Same |
| 8.23 | No (without a denylist) | 6 savings lines become $4.50 of fake items. Item prices themselves are correct. |

Same last-token trap as Aldi: line ends in F, not 2.99.

Kroger never collapsed qty here. Two hash browns = two lines. Two buns = two lines. Tracking "by item" means you must merge after parse (KRO HASHBRN PATTIE × 2).

---

## GPT-4o VISION FALLBACK

Structure on all five is regular enough that 0/5 need a model to understand layout, once OCR is clean. Where vision earns its keep is photo OCR of thermal paper, not promo logic:

| Receipt | OCR risk |
|---|---|
| Aldi 8.17 | Low. Large type, 6 items. |
| Aldi 9.1 | Medium. Dense, 49 units, child qty lines can merge with the parent if the photo skews. |
| Kroger 8.26 AM | Low–medium. |
| Kroger 8.26 PM | Medium. Payment block already smears (DFR for DEBIT). Item block is still readable. |
| Kroger 8.23 | Highest. Faded, SC prefix, PC colliding with names (BREPC). |

Honest split on this set:
- **Aldi:** ~50% simple as specified; ~0% need vision for structure; ~0–50% might need vision as OCR (the 49-item photo).
- **Kroger:** ~67% simple as specified; ~0% need vision for structure; ~33% (the savings receipt) need either a denylist or vision if you refuse to write that denylist.

Would NOT put GPT-4o on the hot path for these five if the OCR engine returns the columns that are already on the paper. Would keep a vision fallback for unreadable/wrapped/new promo formats not yet seen (BOGO, lb @, mfr coupons).

---

## CATEGORY MAPPING: keywords vs classifier

Eight buckets: Produce, Dairy, Meat, Pantry, Bakery, Beverages, Frozen, Household.

Keyword rules cover this sample, with an abbreviation list. No neural classifier needed for these 5 receipts. But you do need more than naive word-contains, because Kroger names are mutilated.

### What maps cleanly

| Bucket | Hits in this set |
|---|---|
| Meat | bacon, turkey, pepperoni, franks, hot dogs, sausage, beef burgers |
| Dairy | swiss, sour cream, milk |
| Bakery | bread, buns, tortillas (debatable), BKRY, pretzel rolls |
| Beverages | Arizona, energy, FB tax code as a hint |
| Produce | blueberries only |
| Pantry | soup, rice, chili, mustard, ketchup, pretzels, chicken noodle |
| Frozen | hash brown patties, lasagna 38oz — inferred, never printed |
| Household | paper bags only (NB) |

### Where keywords alone get shaky

- **Kroger abbreviations** — HASHBRN, BLPK, PRSL PRO SPSHRP RL, WW RT BRED, CHO CHIP CK. Need a small alias table, not a model.
- **Frozen vs pantry** — nothing on the receipt says frozen. Hash browns and 38oz lasagna are a guess from product knowledge.
- **Same display name, different SKU** — two "Campbell's Soup" and two "RTS Rice" on Aldi 9.1. Category is the same; item identity is not. Aldi SKU is the real key. Kroger prints no SKU/UPC at all, so identity is the messy name string.
- **Spicy Marg or Pine** — already ambiguous (drink vs juice vs mix).
- Department prefixes help on Kroger (BKRY → Bakery, KRO → store brand, any bucket) but are incomplete.

**Verdict:** keyword + abbreviation list + Aldi tax-code hint (FB → Beverages, NB → Household) is enough to start. A classifier becomes worth it when you hit produce-by-weight, prepared foods, and Kroger names that don't share tokens with the real product (PRSL PRO SPSHRP RL). Not before.

---

## FACTS THAT WILL BITE TRACKING "BY INDIVIDUAL FOOD ITEM"

1. **Aldi identity = SKU.** 399942 turkey is the same product on 8.17 ($3.19 × 2) and 9.1 ($3.19 × 3). Use the 6-digit number, not the name.
2. **Kroger identity = abbreviated string, no SKU.** KRO HASHBRN PATTIE is all you get. Fuzzy merge is mandatory.
3. **Do not subtract Kroger savings from the line price.** You will understate spend by ~20% on trip 8.23.
4. **Do not ingest Aldi `N x unit` lines as items.** You will overstate spend.
5. **Qty is inconsistent even within Aldi.** Duplicate lines vs `N x`. Monthly totals need a merge step either way.
6. **Weighable produce and BOGO are unseen.** Any parser locked to this sample will break the first time someone buys apples or a 2-for.
7. **Tax is not spend-on-food.** $0.22/$0.46 sit in the footer; rounding −$0.02 is cash-only. Decide now whether monthly "spending" is merchandise subtotal or amount tendered.
8. **Two Aldi tax jurisdictions already** (6.75% vs 7.80%). Don't hardcode one rate.