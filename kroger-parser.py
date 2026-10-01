"""
Kroger receipt parser.

Notes / known gaps:
- Produce sold by weight ($/lb) is not yet handled — unit price and weight
  lines may need manual correction.
- BOGO / multi-buy promo lines are not subtracted or merged; the printed
  item-line price is treated as the amount paid (see decisions.md).
- Abbreviation aliases below expand common Kroger shorthand before identity
  matching; expand the table as new codes appear.
"""

import re
from typing import List, Dict, Any, Tuple

# Common Kroger receipt abbreviations → expanded form.
# Applied as whole-token replacements (case-insensitive) before item_key.
ABBREV_ALIASES: Dict[str, str] = {
    "THB": "THB",           # keep as-is (usually bread brand/code)
    "LGE": "Large",
    "MED": "Medium",
    "SM": "Small",
    "ORG": "Organic",
    "GLN": "Gluten Free",
    "N/L": "No Sugar Added",
    "LT": "Lite",
    "1/EA": "Each",
    "EA": "Each",
}

def expand_abbreviations(name: str) -> str:
    """Replace known abbreviation tokens in an item name via dict lookup."""
    if not name:
        return name
    tokens = name.split()
    out = []
    for tok in tokens:
        key = tok.upper()
        # Preserve slash forms like N/L
        if key in ABBREV_ALIASES:
            out.append(ABBREV_ALIASES[key])
        else:
            out.append(tok)
    return " ".join(out)

# Denylist patterns for non-item lines
DENY_PATTERNS = [
    r'\bSC\b',
    r'KROGER\s+SAVINGS',
    r'TOTAL\s+COUPONS',
    r'You\s+Saved',
    r'\bSAVINGS\b',
    r'\bTAX\b',
    r'\bBALANCE\b',
    r'\bTOTAL\b',
    r'KROGER\s+PLUS\s+CUSTOMER',
    r'DEBIT',
    r'PURCHASE',
    r'CHANGE',
    r'NUMBER\s+OF\s+ITEMS',
    r'REF\s*#',
    r'VERIFIED\s+BY',
    r'AID:',
    r'TC:',
    r'FIFTHTHIRD',
    r'CASHBACK',
    r'STR\s+CPN',
    r'POINTS',
    r'REDEEM',
    r'COMMUNITY\s+REWARDS',
    r'FEEDBACK',
    r'Annual\s+Card',
    r'With\s+Our',
]

DENY_RE = re.compile('|'.join(DENY_PATTERNS), re.IGNORECASE)

# Stop markers - once we hit these, stop parsing items
STOP_PATTERNS = [
    r'\bBALANCE\b',
    r'\bTAX\b',
    r'TOTAL\s+COUPONS',
    r'You\s+Saved',
    r'KROGER\s+PLUS\s+CUSTOMER',
    r'\bDEBIT\b',
    r'PURCHASE:',
    r'TOTAL\s+NUMBER',
    r'STR\s+CPN',
]

STOP_RE = re.compile('|'.join(STOP_PATTERNS), re.IGNORECASE)

# Price pattern: number with optional decimal
PRICE_RE = re.compile(r'(\d+\.\d{2})')

def is_deny_line(line: str) -> bool:
    return bool(DENY_RE.search(line))

def is_stop_line(line: str) -> bool:
    return bool(STOP_RE.search(line))

def extract_price_and_name(line: str) -> Tuple[str, float, bool] | None:
    """
    Parse an item line.
    Rules:
    - Strip trailing single-letter tax code (F) — last token is F, not a price.
    - Second-to-last token is the LINE TOTAL.
    - "PC" between name and price is a promo marker, NOT a price.
    - Watch for it gluing onto long names (e.g. BREPC).
    Returns (name, line_total, is_food) or None if not parseable.
    """
    # Clean up common OCR noise
    line = line.strip()
    if not line:
        return None
    
    # Normalize whitespace
    line = re.sub(r'\s+', ' ', line)
    
    # Remove trailing tax code like " F" or "F" or " | " etc
    # Tax code is typically a single letter at the end, possibly with OCR junk
    tokens = line.split()
    if not tokens:
        return None
    
    is_food = True  # default; F means food (tax-exempt grocery)
    
    # Check last token for tax code
    last = tokens[-1].upper().rstrip('.,;|')
    if last in ('F', 'N', 'T', 'A', 'B') or (len(last) == 1 and last.isalpha()):
        if last == 'N':
            is_food = False  # non-food
        tokens = tokens[:-1]
    elif last.endswith('F') and len(last) > 1 and not re.search(r'\d', last):
        # e.g. "2.99F" glued
        # try to split
        m = re.match(r'^(.*?)(F)$', last, re.I)
        if m:
            tokens[-1] = m.group(1)
            is_food = True
    
    if not tokens:
        return None
    
    # Now find the price: should be the last token that looks like a price
    # or second-to-last if last was tax
    price = None
    price_idx = None
    for i in range(len(tokens) - 1, -1, -1):
        t = tokens[i].replace(',', '').replace('$', '').rstrip('.,;|)')
        # Handle glued like "2.99F"
        m = re.match(r'^(\d+\.\d{2})([A-Za-z]?)$', t)
        if m:
            price = float(m.group(1))
            price_idx = i
            if m.group(2).upper() == 'N':
                is_food = False
            break
        if re.match(r'^\d+\.\d{2}$', t):
            price = float(t)
            price_idx = i
            break
    
    if price is None or price_idx is None:
        return None
    
    # Name is everything before the price token
    name_tokens = tokens[:price_idx]
    
    # Clean PC promo marker: if last name token is PC, drop it
    # Also handle glued PC like BREPC → BRE (but keep BREPC as name part? No:
    # "PC is a short code between name and price" — strip standalone PC
    # "Watch for it gluing onto long names (e.g. BREPC)" — leave as is in name
    if name_tokens and name_tokens[-1].upper() == 'PC':
        name_tokens = name_tokens[:-1]
    
    name = ' '.join(name_tokens).strip()
    # Clean trailing punctuation from name
    name = re.sub(r'[.,;|]+$', '', name).strip()
    
    if not name or len(name) < 2:
        return None
    
    # Filter out pure numbers or very short garbage
    if re.match(r'^[\d.]+$', name):
        return None

    # Expand known abbreviations before identity / fuzzy matching
    name = expand_abbreviations(name)
    
    return (name, price, is_food)


def parseKroger(text: str) -> Dict[str, Any]:
    """
    Parse Kroger receipt text.
    Returns:
    {
      "items": [ {name, qty, unit_price, line_total, is_food}, ... ],
      "subtotal": float,  # sum of line_totals (merchandise)
      "printed_subtotal": float | None,  # from BALANCE or computed before tax if found
      "reconciles": bool,
      "unsure": [str, ...]  # lines that looked suspicious
    }
    """
    lines = text.splitlines()
    items = []
    unsure = []
    printed_balance = None
    printed_tax = None
    
    # First pass: find printed BALANCE and TAX for reconciliation reference
    for line in lines:
        m = re.search(r'BALANCE\s+([\d.]+)', line, re.I)
        if m:
            try:
                printed_balance = float(m.group(1))
            except ValueError:
                pass
        m = re.search(r'\bTAX\s+([\d.]+)', line, re.I)
        if m:
            try:
                printed_tax = float(m.group(1))
            except ValueError:
                pass
    
    # Compute expected merchandise subtotal = balance - tax (if both known)
    expected_subtotal = None
    if printed_balance is not None and printed_tax is not None:
        expected_subtotal = round(printed_balance - printed_tax, 2)
    elif printed_balance is not None and (printed_tax is None or printed_tax == 0):
        expected_subtotal = printed_balance
    
    stopped = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        
        if is_stop_line(line):
            stopped = True
            # still try to capture balance/tax if not yet
            continue
        
        if stopped:
            continue
        
        if is_deny_line(line):
            # Savings lines etc — skip entirely
            continue
        
        # Skip header-ish lines
        if re.search(r'(CORRY|CHEC|FRESH|EVERYONE|513)', line, re.I) and not PRICE_RE.search(line):
            continue
        if re.match(r'^[\d\s.W]+$', line):  # address fragments
            continue
        
        parsed = extract_price_and_name(line)
        if parsed is None:
            # Might be a multi-line name or OCR garbage; note if it has a number
            if PRICE_RE.search(line) and len(line) > 5:
                unsure.append(f"Could not parse (has price?): {line}")
            continue
        
        name, line_total, is_food = parsed

        # Keyword denylist forces non-food (household / personal care / pet)
        # Exceptions: sour cream; cat|dog food stay food
        name_l = name.lower()
        deny_words = (
            "shampoo", "soap", "bleach", "dish", "laundry", "paper", "bag",
            "tissue", "toilet", "cleaner", "spray", "deodorant", "lotion",
            "pet", "household",
        )
        if any(w in name_l for w in deny_words):
            if "sour cream" not in name_l and not re.search(r"\b(cat|dog)\s*food\b", name_l):
                is_food = False
        if "cream" in name_l and "sour cream" not in name_l:
            # lotion/cream style products — already partially covered; keep conservative
            if any(w in name_l for w in ("hand", "face", "body", "skin", "lotion")):
                is_food = False
        
        # qty is always 1 for Kroger in this set (duplicates are separate lines)
        qty = 1
        unit_price = line_total  # since qty=1
        
        items.append({
            "name": name,
            "qty": qty,
            "unit_price": unit_price,
            "line_total": line_total,
            "is_food": is_food,
        })
    
    subtotal = round(sum(it["line_total"] for it in items), 2)
    
    # Reconciles against expected merchandise subtotal
    reconciles = False
    if expected_subtotal is not None:
        reconciles = abs(subtotal - expected_subtotal) < 0.02
    else:
        # fallback: if no tax info, compare to balance assuming tax=0
        if printed_balance is not None:
            reconciles = abs(subtotal - printed_balance) < 0.02
            expected_subtotal = printed_balance
    
    return {
        "items": items,
        "subtotal": subtotal,
        "printed_subtotal": expected_subtotal,
        "printed_balance": printed_balance,
        "printed_tax": printed_tax,
        "reconciles": reconciles,
        "unsure": unsure,
    }


# --- Test with cleaned OCR texts ---

text_823 = """
1 W. CORRY ST.
513 872 1500 CHEC 502
KRO BACON PC 2.99 F
SC KROGER SAVINGS 1.80
KRO PEPPERONI 2.99 F
BLPK HOTDOG BUNS PC 2.99 F
SC KROGER SAVINGS 1.00
BLPK HOTDOG BUNS PC 2.99 F
SC KROGER SAVINGS 1.00
KRO 100% WW RT BREPC 1.99 F
SC KROGER SAVINGS 0.50
BAR-S FRANKS 1.59 F
SC KROGER SAVINGS 0.10
BAR-S FRANKS 1.59 F
SC KROGER SAVINGS 0.10
KROGER PLUS CUSTOMER ******0280
TAX 0.00
BALANCE 17.13
"""

text_826 = """
1 W. CORRY ST.
513 872 1500 CHEC 503
KRO BEEF BURGERS 10.00 F
KRO HASHBRN PATTIE 3.79 F
KRO HASHBRN PATTIE 3.79 F
KROGER PLUS CUSTOMER ******0280
TAX 0.00
BALANCE 17.58
"""

text_826b = """
1 W. CORRY ST.
513 872 1500 CHEC 512
BKRY CHO CHIP CK 3.00 F
PRSL PRO SPSHRP RL 6.00 F
PRSL PRO SPSHRP RL 6.00 F
KROGER PLUS CUSTOMER ******0280
TAX 0.00
BALANCE 15.00
"""

text_914 = """
1 W. CORRY ST.
513 872 1500 CHEC 513
DR SQ SHAMPOO PC 13.99
SC KROGER SAVINGS 1.00
KRO MEAT LASAGNA 8.49 F
STFR CHK FR RCE 3.29 F
KROGER PLUS CUSTOMER ******0280
TAX 1.09
BALANCE 26.86
"""

if __name__ == "__main__":
    for label, txt in [
        ("Kroger 8.23", text_823),
        ("Kroger 8.26", text_826),
        ("Kroger2 8.26", text_826b),
        ("Kroger 9.14", text_914),
    ]:
        result = parseKroger(txt)
        print(f"\n=== {label} ===")
        for it in result["items"]:
            food = "food" if it["is_food"] else "NON-FOOD"
            print(f"  {it['name']:30s}  qty={it['qty']}  unit={it['unit_price']:.2f}  total={it['line_total']:.2f}  [{food}]")
        print(f"  subtotal (sum lines): {result['subtotal']:.2f}")
        print(f"  printed_subtotal:     {result['printed_subtotal']}")
        print(f"  printed_balance:      {result['printed_balance']}")
        print(f"  printed_tax:          {result['printed_tax']}")
        print(f"  reconciles:           {result['reconciles']}")
        if result["unsure"]:
            print(f"  UNSURE lines: {result['unsure']}")
