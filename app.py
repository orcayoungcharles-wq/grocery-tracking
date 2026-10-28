"""
Grocery Receipt Tracker — Streamlit dashboard
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

import datastore
import aggregate
import aldi_parser_shim as aldi_parser
import kroger_parser_shim as kroger_parser

st.set_page_config(
    page_title="Grocery Tracker",
    page_icon="🛒",
    layout="wide",
)


def parse_uploaded_text(text: str, store: str, filename: str) -> dict:
    """Parse receipt text and return a dict for datastore.add_receipt.

    Item identity uses aggregate.normalize_item:
      - Aldi  → 6-digit SKU
      - Kroger → alias-expanded uppercase name
    is_food is re-checked against NON_FOOD_KEYWORDS so shampoo etc. stay out
    of food spend even if the parser misses a tax flag.
    """
    import re
    store_upper = store.upper()
    if store == "aldi":
        result = aldi_parser.parse_receipt(text)
        items_raw = result.items
        subtotal = result.subtotal
        reconciles = result.reconciles
        # Prefer printed B-Taxable / AMOUNT DUE when present
        tax = 0.0
        m_tax = re.search(r"B-Taxable\s*@[\d.]+%\s+([\d.]+)", text, re.I)
        if m_tax:
            tax = round(float(m_tax.group(1)), 2)
        else:
            m_due = re.search(r"AMOUNT\s+DUE\s+([\d.]+)", text, re.I)
            if m_due and subtotal is not None:
                tax = round(max(0.0, float(m_due.group(1)) - float(subtotal)), 2)
    else:
        result = kroger_parser.parse_receipt(text)
        items_raw = result["items"]
        subtotal = result["subtotal"]
        reconciles = result["reconciles"]
        tax = result.get("printed_tax") or 0.0
        if not tax and result.get("printed_balance") is not None:
            tax = round(result["printed_balance"] - subtotal, 2)
        tax = round(float(tax or 0), 2)

    normalized = []
    for item in items_raw:
        n = aggregate.normalize_item(item, store_upper)
        normalized.append({
            "item_key": n["item_key"],
            "name": n["display_name"],
            "sku": n.get("sku"),
            "qty": n["qty"],
            "unit_price": n.get("unit_price"),
            "line_total": n["line_total"],
            "tax_code": n.get("tax_code"),
            "is_food": n["is_food"],
        })

    # Prefer printed date on the receipt body (MM/DD/YY), else filename MM.DD / MM-DD
    import re
    from datetime import date
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2})", text)
    if m:
        month, day, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        receipt_date = f"{2000 + yy}-{month:02d}-{day:02d}"
    else:
        m = re.search(r"(\d{1,2})[._-](\d{1,2})", filename)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            receipt_date = f"{date.today().year}-{month:02d}-{day:02d}"
        else:
            receipt_date = date.today().isoformat()

    return {
        "store": store,
        "date": receipt_date,
        "trip_id": Path(filename).stem,
        "items": normalized,
        "subtotal": subtotal,
        "tax": tax,
        "total": round(subtotal + tax, 2),
        "reconciled": reconciles,
        "filename": filename,
    }


def detect_store_from_name(name: str) -> str | None:
    n = name.lower()
    if "aldi" in n:
        return "aldi"
    if "kroger" in n:
        return "kroger"
    return None


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🛒 Grocery Tracker")
page = st.sidebar.radio(
    "Navigate",
    ["Dashboard", "Shopping List", "Receipts", "Upload", "Backup"],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Data is stored in JSON under `data/`. "
    "On Streamlit Cloud the filesystem resets on redeploy — use **Backup** to export/import."
)
if st.sidebar.button("Reset all data", type="secondary"):
    datastore.reset_data()
    st.sidebar.success("Data cleared.")
    st.rerun()

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
if page == "Dashboard":
    st.title("Spending Dashboard")

    data = datastore.get_all_data()
    spending = data["spending"]
    current = data["current_month"]
    previous = data["previous_month"]
    receipt_count = data["receipt_count"]
    weekly = datastore.get_weekly_spending(max_weeks=8)

    def _amt(d):
        return float(d.get("total", d.get("food", 0)) or 0)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Receipts stored", receipt_count)
    col2.metric(
        "This month",
        f"${_amt(current):.2f}",
        delta=f"${_amt(current) - _amt(previous):.2f} vs prior",
    )
    col3.metric("Prior month", f"${_amt(previous):.2f}")
    stores = current.get("by_store", {})
    col4.metric("Stores this month", ", ".join(f"{k}: ${v:.2f}" for k, v in stores.items()) or "—")

    st.markdown("---")
    view = st.radio("View", ["Weekly", "Monthly"], horizontal=True, index=0)

    # Static charts: no zoom/pan/mode bar
    chart_config = {"staticPlot": True, "displayModeBar": False}

    if not spending and not weekly:
        st.info("No spending data yet. Upload receipts on the Upload page.")
    elif view == "Weekly":
        if not weekly:
            st.info("No weekly data yet.")
        else:
            labels = [weekly[w].get("label", w) for w in weekly]
            totals = [weekly[w].get("total", weekly[w].get("food", 0)) for w in weekly]
            fig = px.bar(
                x=labels,
                y=totals,
                labels={"x": "", "y": "Spend ($)"},
                title="Weekly spend (last 8 weeks)",
                text=[f"${v:.2f}" for v in totals],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                yaxis_title="USD",
                xaxis_title="",
                height=320,
                margin=dict(t=40, b=40, l=40, r=20),
                dragmode=False,
            )
            st.plotly_chart(fig, use_container_width=True, config=chart_config)

            # Pie: same window as the bar (sum by store across shown weeks)
            store_totals: dict[str, float] = {}
            for payload in weekly.values():
                for store, amount in payload.get("by_store", {}).items():
                    store_totals[store] = round(store_totals.get(store, 0.0) + amount, 2)
            if store_totals:
                fig_pie = px.pie(
                    names=[s.title() for s in store_totals.keys()],
                    values=list(store_totals.values()),
                    title="Spend by store (same weeks)",
                    hole=0.35,
                )
                fig_pie.update_traces(textposition="inside", textinfo="label+percent+value")
                fig_pie.update_layout(height=320, margin=dict(t=40, b=20, l=20, r=20))
                st.plotly_chart(fig_pie, use_container_width=True, config=chart_config)

            with st.expander("Raw weekly data"):
                st.json(weekly)
    else:
        months = list(spending.keys())
        totals = [_amt(spending[m]) for m in months]
        fig = px.bar(
            x=months,
            y=totals,
            labels={"x": "Month", "y": "Spend ($)"},
            title="Monthly spend",
            text=[f"${v:.2f}" for v in totals],
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(
            yaxis_title="USD",
            xaxis_title="",
            height=320,
            margin=dict(t=40, b=40, l=40, r=20),
            dragmode=False,
        )
        st.plotly_chart(fig, use_container_width=True, config=chart_config)

        store_totals = {}
        for payload in spending.values():
            for store, amount in payload.get("by_store", {}).items():
                store_totals[store] = round(store_totals.get(store, 0.0) + amount, 2)
        if store_totals:
            fig_pie = px.pie(
                names=[s.title() for s in store_totals.keys()],
                values=list(store_totals.values()),
                title="Spend by store (all months shown)",
                hole=0.35,
            )
            fig_pie.update_traces(textposition="inside", textinfo="label+percent+value")
            fig_pie.update_layout(height=320, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig_pie, use_container_width=True, config=chart_config)

        with st.expander("Raw monthly data"):
            st.json(spending)

# ---------------------------------------------------------------------------
# Shopping List
# ---------------------------------------------------------------------------
elif page == "Shopping List":
    st.title("Shopping List")
    st.caption("Items bought on at least the selected % of trips (food and non-food).")

    threshold = st.slider("Frequency threshold (%)", min_value=10, max_value=100, value=30, step=5)

    items = datastore.get_shopping_list(threshold=float(threshold))

    if not items:
        st.info("No items meet this threshold yet. Add more receipts.")
    else:
        st.dataframe(
            [
                {
                    "Item": i["name"],
                    "Bought on": f"{i['trip_count']} of {i['total_trips']} trips",
                    "Frequency": f"{i['frequency_pct']}%",
                    "Avg qty": i["avg_qty"],
                    "Avg price": f"${i['avg_price']:.2f}",
                    "Stores": ", ".join(i["stores"]),
                    "Last bought": i["last_bought"],
                }
                for i in items
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("---")
    st.subheader("All item frequencies")
    all_freq = datastore.get_item_frequencies()
    if all_freq:
        st.dataframe(
            [
                {
                    "Item": i["name"],
                    "Bought on": f"{i['trip_count']} of {i['total_trips']} trips",
                    "Frequency": f"{i['frequency_pct']}%",
                    "Avg qty": i["avg_qty"],
                    "Avg price": f"${i['avg_price']:.2f}",
                    "Last bought": i["last_bought"],
                }
                for i in all_freq
            ],
            use_container_width=True,
            hide_index=True,
        )

# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
elif page == "Receipts":
    st.title("Stored Receipts")
    st.caption(
        "**Reconciled** means the sum of parsed line items matches the printed subtotal. "
        "If it does not, OCR or the parser may have missed a line — totals can still be useful."
    )
    receipts = datastore.load_receipts()
    if not receipts:
        st.info("No receipts stored yet.")
    else:
        for i, r in enumerate(reversed(receipts)):
            trip_key = r.get("trip_id") or r.get("filename") or r.get("date")
            ok = r.get("reconciled")
            badge = "✅" if ok else "❌ not reconciled"
            label = (
                f"{r.get('date', '?')} · {r.get('store', '?').title()} · "
                f"${r.get('total', r.get('subtotal', 0)):.2f} · {badge} · "
                f"{r.get('filename', '')}"
            )
            with st.expander(label, expanded=(i == 0 and not ok)):
                c1, c2, c3, c4 = st.columns(4)
                c1.write(f"**Subtotal:** ${r.get('subtotal', 0):.2f}")
                c2.write(f"**Tax:** ${r.get('tax', 0):.2f}")
                c3.write(f"**Total:** ${r.get('total', 0):.2f}")
                c4.write(f"**Reconciled:** {badge}")
                rows = []
                for it in r.get("items", []):
                    rows.append({
                        "Name": it.get("name"),
                        "Qty": it.get("qty"),
                        "Unit": it.get("unit_price"),
                        "Line total": it.get("line_total"),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
                del_key = f"del_{trip_key}_{i}"
                if st.button("Delete this receipt", key=del_key, type="secondary"):
                    if datastore.delete_receipt(str(trip_key)):
                        st.success(f"Deleted {trip_key}.")
                        st.rerun()
                    else:
                        st.error("Could not find that receipt to delete.")

# ---------------------------------------------------------------------------
# Upload (text, photo, PDF + Gemini OCR)
# ---------------------------------------------------------------------------
elif page == "Upload":
    import ocr as ocr_mod

    st.title("Upload receipt")
    st.caption(
        "Photo, PDF scan, or text. Images/PDFs are OCR'd with Gemini, then parsed. "
        "Review the text before storing."
    )

    store = st.selectbox("Store", ["aldi", "kroger"])
    if ocr_mod.has_api_key():
        st.success("Gemini API key found — photo/PDF OCR is available.", icon="✅")
    else:
        st.warning(
            "No Gemini key yet. You can still paste text or upload .txt. "
            "Add the key under **Streamlit Secrets**:\n\n"
            "```toml\n[gemini]\napi_key = \"YOUR_KEY\"\n```",
            icon="⚠️",
        )

    tab_file, tab_camera, tab_paste = st.tabs(["File", "Camera", "Paste text"])

    raw_bytes: bytes | None = None
    filename = "receipt.jpg"
    text: str | None = None
    source = None

    with tab_file:
        uploaded = st.file_uploader(
            "Receipt file",
            type=["txt", "jpg", "jpeg", "png", "webp", "pdf"],
            key="upload_file",
        )
        if uploaded is not None:
            filename = uploaded.name
            detected = detect_store_from_name(filename)
            if detected and detected != store:
                st.warning(f"Filename suggests store={detected}; you selected {store}.")
            data = uploaded.getvalue()
            lower = filename.lower()
            if lower.endswith(".txt"):
                text = data.decode("utf-8", errors="replace")
                source = "text"
            else:
                raw_bytes = data
                source = "image"

    with tab_camera:
        cam = st.camera_input("Take a photo of the receipt")
        if cam is not None:
            raw_bytes = cam.getvalue()
            filename = "camera.jpg"
            source = "image"

    with tab_paste:
        pasted = st.text_area("Paste receipt text", height=220, key="paste_area")
        if pasted.strip():
            text = pasted
            filename = "pasted.txt"
            source = "text"

    # OCR step for images / PDFs
    if source == "image" and raw_bytes is not None:
        if st.button("Run OCR", type="primary", key="run_ocr"):
            if not ocr_mod.has_api_key():
                st.error("Add a Gemini API key in Streamlit Secrets first.")
            else:
                with st.spinner("OCR in progress…"):
                    try:
                        text = ocr_mod.extract_text_from_bytes(raw_bytes, filename)
                        st.session_state["ocr_text"] = text
                        st.session_state["ocr_filename"] = filename
                        st.success("OCR complete — review the text below.")
                    except Exception as e:
                        st.error(f"OCR failed: {e}")

    if "ocr_text" in st.session_state and source == "image":
        text = st.session_state["ocr_text"]
        filename = st.session_state.get("ocr_filename", filename)

    if text:
        st.subheader("Receipt text")
        edited = st.text_area(
            "Edit if OCR missed something, then store",
            value=text,
            height=280,
            key="final_text",
        )

        # Preview parse so we can warn on reconcile failure before storing
        preview = None
        preview_err = None
        try:
            preview = parse_uploaded_text(edited, store, filename)
        except Exception as e:
            preview_err = e

        if preview_err:
            st.error(f"Could not parse yet: {preview_err}")
        elif preview is not None:
            line_sum = round(sum(i["line_total"] for i in preview["items"]), 2)
            st.write(
                f"**Preview:** {len(preview['items'])} items · "
                f"line sum ${line_sum:.2f} · printed subtotal ${preview['subtotal']:.2f} · "
                f"tax ${preview.get('tax', 0):.2f} · total ${preview['total']:.2f}"
            )
            if not preview.get("reconciled"):
                st.warning(
                    "**Does not reconcile.** The sum of parsed lines does not match the "
                    "printed subtotal — a line may be missing or misread. "
                    "You can still include it (uses the printed total for spend) or discard and fix the text."
                )
                c_inc, c_disc = st.columns(2)
                with c_inc:
                    if st.button("Include anyway", type="primary", key="include_unrec"):
                        datastore.add_receipt(preview)
                        st.session_state.pop("ocr_text", None)
                        st.session_state.pop("ocr_filename", None)
                        st.success(
                            f"Stored {preview['filename']} (not reconciled): "
                            f"{len(preview['items'])} items, total ${preview['total']:.2f}"
                        )
                        st.rerun()
                with c_disc:
                    if st.button("Discard", type="secondary", key="discard_unrec"):
                        st.session_state.pop("ocr_text", None)
                        st.session_state.pop("ocr_filename", None)
                        st.info("Discarded. Edit the text above and try again.")
                        st.rerun()
            else:
                if st.button("Parse & store", type="primary", key="parse_store"):
                    try:
                        datastore.add_receipt(preview)
                        st.session_state.pop("ocr_text", None)
                        st.session_state.pop("ocr_filename", None)
                        st.success(
                            f"Stored {preview['filename']}: {len(preview['items'])} items, "
                            f"total ${preview['total']:.2f}, reconciled=True"
                        )
                        with st.expander("Parsed items"):
                            st.dataframe(
                                [
                                    {
                                        "Name": it["name"],
                                        "Qty": it["qty"],
                                        "Line total": it["line_total"],
                                        "Key": it.get("item_key"),
                                    }
                                    for it in preview["items"]
                                ],
                                use_container_width=True,
                                hide_index=True,
                            )
                    except Exception as e:
                        st.error(f"Parse/store failed: {e}")
                        st.exception(e)


# ---------------------------------------------------------------------------
# Backup / restore (survives Streamlit Cloud restarts)
# ---------------------------------------------------------------------------
elif page == "Backup":
    st.title("Backup & Restore")
    st.markdown(
        """
Streamlit Community Cloud **wipes the filesystem** on every redeploy and often
after idle timeouts. Use this page to keep a local copy of your data.

1. Click **Download backup** after each shopping trip (or weekly).
2. After a restart, upload that JSON file and choose **Replace** or **Merge**.
"""
    )

    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Export")
        bundle = datastore.export_bundle()
        import json
        payload = json.dumps(bundle, indent=2, default=str)
        st.download_button(
            label=f"Download backup ({len(bundle.get('receipts', []))} receipts)",
            data=payload,
            file_name="grocery-tracker-backup.json",
            mime="application/json",
            type="primary",
        )
        st.caption("Includes receipts, monthly totals, and item history.")

    with col_b:
        st.subheader("Import")
        up = st.file_uploader("Backup JSON", type=["json"])
        mode = st.radio("Mode", ["replace", "merge"], horizontal=True,
                        help="replace = wipe current data first; merge = add only new trip_ids")
        if up is not None and st.button("Restore from backup", type="primary"):
            try:
                import json
                bundle = json.loads(up.read().decode("utf-8"))
                n = datastore.import_bundle(bundle, mode=mode)
                st.success(f"Restored. Now storing {n} receipts.")
                st.rerun()
            except Exception as e:
                st.error(f"Restore failed: {e}")
                st.exception(e)

    st.markdown("---")
    st.subheader("Current store summary")
    receipts = datastore.load_receipts()
    if not receipts:
        st.info("Nothing stored yet.")
    else:
        for r in sorted(receipts, key=lambda x: x.get("date", "")):
            food = sum(i["line_total"] for i in r.get("items", []) if i.get("is_food", True))
            st.write(
                f"- **{r.get('date')}** · {r.get('store')} · "
                f"food ${food:.2f} · `{r.get('filename')}`"
            )
