# Grocery Tracking

Personal Aldi + Kroger receipt tracker: parse text receipts → food-only monthly spend → frequency shopping list → Streamlit dashboard.

## Status

Working end-to-end on the six sample receipts (2 Aldi, 4 Kroger).

| Layer | Status |
|-------|--------|
| Format audit + locked decisions | `audit.md`, `decisions.md` |
| Aldi parser (SKU, child qty lines, tax codes) | `aldi-parser.py` |
| Kroger parser (savings denylist, abbreviations, non-food keywords) | `kroger-parser.py` |
| Pure aggregation (normalize / merge / monthly summary) | `aggregate.py` |
| JSON datastore + item frequency / shopping list | `datastore.py` |
| Streamlit UI | `app.py` |
| Unit tests | `test_aggregate.py` |
| E2E pipeline test | `test_pipeline.py` |
| Live OCR (Document AI / vision) | Not wired yet — upload expects text |

## Quick start

```bash
pip install -r requirements.txt
python test_pipeline.py          # E2E: parse → store → assert $68.31 / $108.67
python -m pytest test_aggregate.py -v
streamlit run app.py             # Dashboard / Shopping List / Upload
```

Upload page accepts `.txt` receipt text (or paste). PDFs need OCR first.

## Locked rules (see `decisions.md`)

- **Spend metric:** food merchandise subtotal only (no tax, rounding, bags, shampoo).
- **Aldi identity:** 6-digit SKU (exact).
- **Kroger identity:** alias-expanded name string (`HASHBRN` → `HASHBROWN`, etc.).
- **Do not subtract** `SC KROGER SAVINGS` — the printed line price is what you paid.
- **Aldi child lines** (`N x unit_price`) merge into the parent; never emit as items.
- Every parse exposes `reconciles` (line sum vs printed subtotal).

## Sample numbers (pipeline assertions)

- August food: **$68.31** (Aldi $18.60 + Kroger $49.71)
- September food: **$108.67** (Aldi $96.89 + Kroger $11.78; shampoo excluded)
- 6 distinct trips; duplicate lines on one receipt count as one trip

## Layout

```
aldi-parser.py / kroger-parser.py   # text → structured items
*_parser_shim.py                    # import helpers (hyphenated filenames)
aggregate.py                        # pure normalize / merge / monthly_summary
datastore.py                        # JSON under data/
app.py                              # Streamlit
test_aggregate.py / test_pipeline.py
audit.md / decisions.md             # design ground truth
*.txt                               # sample OCR text
```

## Next (optional)

1. Wire Document AI (or GPT-4o vision fallback) for photo → text.
2. More receipts (produce-by-weight, BOGO) — parsers will need extensions called out in the audit.
3. Category keywords layer on top of `normalize_item`.
