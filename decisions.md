# Decisions Log

Every design decision locked for this project, with date, rationale, and revision history.

---

## Decision: spending metric
**Date:** 2026-10-01  
**Choice:** merchandise subtotal (exclude tax, rounding, non-food items)  
**Reason:** Tax is not food cost. Rounding is a cash-only artifact. Paper bags (NB tax code) are non-food.  
**Reversed by:** N/A  

---

## Decision: Aldi item identity
**Date:** 2026-10-01  
**Choice:** 6-digit SKU (exact match)  
**Reason:** SKU is stable across receipts. Display names can be identical for different SKUs (e.g., two Campbell's Soup with different SKUs).  
**Reversed by:** N/A  

---

## Decision: Kroger item identity
**Date:** 2026-10-01  
**Choice:** abbreviated name string with fuzzy merge  
**Reason:** Kroger prints no SKU or UPC. Names are mutilated (e.g., PRSL PRO SPSHRP RL). Duplicate items print as separate lines with no qty multiplier.  
**Reversed by:** N/A  

---

## Decision: Kroger savings lines
**Date:** 2026-10-01  
**Choice:** do NOT subtract SC KROGER SAVINGS from line price  
**Reason:** The number on the item line IS the price paid. Savings lines are informational only. Subtracting them understates spend by ~20%.  
**Reversed by:** N/A  

---

## Decision: Aldi child lines
**Date:** 2026-10-01  
**Choice:** merge child lines (N x unit_price) into parent item; never emit as separate rows  
**Reason:** Child lines describe the parent item's quantity and unit price, not a new item. Emitting them as separate rows invents ghost items and inflates spend.  
**Reversed by:** N/A  

---

## Decision: audit scope
**Date:** 2026-10-01  
**Choice:** grocery-only (no restaurant, no pharmacy, no fuel)  
**Reason:** User's use case is tracking grocery spending per month.  
**Reversed by:** N/A  

---

## Decision: OCR provider
**Date:** 2026-10-01  
**Choice:** Google Document AI (primary), GPT-4o vision (fallback for unreadable/faded thermal paper)  
**Reason:** Free tier of 1,000 pages/month covers personal volume. Document AI returns structured line items. GPT-4o fallback only for OCR failures, not for parsing logic.  
**Reversed by:** N/A  

---

## Decision: reconciliation gate
**Date:** 2026-10-01  
**Choice:** every parsed receipt must include a `reconciles` boolean: sum of line_totals == printed subtotal  
**Reason:** Catches bad parses immediately instead of shipping silent errors. Both test receipts reconcile so this guardrail is validated.  
**Reversed by:** N/A  

---

## Decision: category mapping approach
**Date:** 2026-10-01  
**Choice:** keyword rules + abbreviation alias table + Aldi tax-code hints (FB → Beverages, NB → Household)  
**Reason:** Keywords cover the current sample. A neural classifier becomes worth it only when produce-by-weight and prepared foods appear. Frozen/pantry distinction is a guess — store it with a confidence flag.  
**Reversed by:** N/A  

---

## Decision: project structure
**Date:** 2026-10-01  
**Choice:** separate Grok projects per phase (00-architecture, 01-aldi-parser, 02-kroger-parser, 03-dashboard)  
**Reason:** Prevents context bloat. Each project starts clean. Completed projects are closed and not re-opened.  
**Reversed by:** N/A  

---

## Decision: file-based context persistence
**Date:** 2026-10-01  
**Choice:** all audit findings, specs, and decisions saved in .md files in a local directory  
**Reason:** When a usage limit resets, pasting a file is faster than re-explaining. The audit, parser specs, and decisions are the complete context needed to resume work.  
**Reversed by:** N/A  

---

## Decision: frozen/pantry classification
**Date:** 2026-10-01  
**Choice:** classify as "frozen" only when the receipt explicitly says so; otherwise default to pantry with a confidence flag  
**Reason:** Nothing on paper says "frozen" — hash browns and lasagna are product-knowledge guesses. Trust the receipt over assumptions.  
**Reversed by:** N/A  