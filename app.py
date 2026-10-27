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
    store_upper = store.upper()
    if store == "aldi":
        result = aldi_parser.parse_receipt(text)
        items_raw = result.items
        subtotal = result.subtotal
        reconciles = result.reconciles
        tax = 0.0
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
    weekly = datastore.get_weekly_spending()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Receipts stored", receipt_count)
    col2.metric(
        "This month (food)",
        f"${current.get('food', 0):.2f}",
        delta=f"${current.get('food', 0) - previous.get('food', 0):.2f} vs prior",
    )
    col3.metric("Prior month (food)", f"${previous.get('food', 0):.2f}")
    stores = current.get("by_store", {})
    col4.metric("Stores this month", ", ".join(f"{k}: ${v:.2f}" for k, v in stores.items()) or "—")

    st.markdown("---")
    view = st.radio("View", ["Weekly", "Monthly"], horizontal=True, index=0)

    if not spending and not weekly:
        st.info("No spending data yet. Upload receipts on the Upload page.")
    elif view == "Weekly":
        if not weekly:
            st.info("No weekly data yet.")
        else:
            weeks = list(weekly.keys())
            food_vals = [weekly[w].get("food", 0) for w in weeks]
            fig = px.bar(
                x=weeks,
                y=food_vals,
                labels={"x": "Week (ISO)", "y": "Food spend ($)"},
                title="Weekly food spend",
                text=[f"${v:.2f}" for v in food_vals],
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(yaxis_title="USD", xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)

            rows = []
            for week, payload in weekly.items():
                for store, amount in payload.get("by_store", {}).items():
                    rows.append({
                        "week": week,
                        "store": store,
                        "amount": amount,
                        "trips": payload.get("trips", 0),
                    })
            if rows:
                fig2 = px.bar(
                    rows,
                    x="week",
                    y="amount",
                    color="store",
                    barmode="group",
                    title="Food spend by store (weekly)",
                    labels={"amount": "USD", "week": ""},
                )
                st.plotly_chart(fig2, use_container_width=True)

            with st.expander("Raw weekly data"):
                st.json(weekly)
    else:
        # Monthly food spend bar chart
        months = list(spending.keys())
        food_vals = [spending[m].get("food", 0) for m in months]
        fig = px.bar(
            x=months,
            y=food_vals,
            labels={"x": "Month", "y": "Food spend ($)"},
            title="Monthly food spend",
            text=[f"${v:.2f}" for v in food_vals],
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(yaxis_title="USD", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

        rows = []
        for month, payload in spending.items():
            for store, amount in payload.get("by_store", {}).items():
                rows.append({"month": month, "store": store, "amount": amount})
        if rows:
            fig2 = px.bar(
                rows,
                x="month",
                y="amount",
                color="store",
                barmode="group",
                title="Food spend by store",
                labels={"amount": "USD", "month": ""},
            )
            st.plotly_chart(fig2, use_container_width=True)

        with st.expander("Raw monthly data"):
            st.json(spending)

# ---------------------------------------------------------------------------
# Shopping List
# ---------------------------------------------------------------------------
elif page == "Shopping List":
    st.title("Shopping List")
    st.caption("Items bought on at least the selected % of trips.")

    threshold = st.slider("Frequency threshold (%)", min_value=10, max_value=100, value=30, step=5)
    food_only = st.checkbox("Food items only", value=True)

    items = datastore.get_shopping_list(threshold=float(threshold))
    if food_only:
        items = [i for i in items if i.get("is_food", True)]

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
                    "Food": "Yes" if i.get("is_food", True) else "No",
                }
                for i in items
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("---")
    st.subheader("All item frequencies")
    all_freq = datastore.get_item_frequencies()
    if food_only:
        all_freq = [i for i in all_freq if i.get("is_food", True)]
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
    receipts = datastore.load_receipts()
    if not receipts:
        st.info("No receipts stored yet.")
    else:
        for i, r in enumerate(reversed(receipts)):
            food_total = sum(
                it["line_total"] for it in r.get("items", []) if it.get("is_food", True)
            )
            label = (
                f"{r.get('date', '?')} · {r.get('store', '?').title()} · "
                f"${r.get('subtotal', 0):.2f} (food ${food_total:.2f}) · "
                f"{r.get('filename', '')}"
            )
            with st.expander(label, expanded=(i == 0)):
                c1, c2, c3, c4 = st.columns(4)
                c1.write(f"**Subtotal:** ${r.get('subtotal', 0):.2f}")
                c2.write(f"**Tax:** ${r.get('tax', 0):.2f}")
                c3.write(f"**Total:** ${r.get('total', 0):.2f}")
                c4.write(f"**Reconciled:** {'✅' if r.get('reconciled') else '❌'}")
                rows = []
                for it in r.get("items", []):
                    rows.append({
                        "Name": it.get("name"),
                        "Qty": it.get("qty"),
                        "Unit": it.get("unit_price"),
                        "Line total": it.get("line_total"),
                        "Food": "Yes" if it.get("is_food", True) else "No",
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)

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
        if st.button("Parse & store", type="primary", key="parse_store"):
            try:
                receipt = parse_uploaded_text(edited, store, filename)
                datastore.add_receipt(receipt)
                st.session_state.pop("ocr_text", None)
                st.session_state.pop("ocr_filename", None)
                food = sum(
                    i["line_total"] for i in receipt["items"] if i.get("is_food", True)
                )
                st.success(
                    f"Stored {receipt['filename']}: {len(receipt['items'])} items, "
                    f"food ${food:.2f}, subtotal ${receipt['subtotal']:.2f}, "
                    f"reconciled={receipt['reconciled']}"
                )
                with st.expander("Parsed items"):
                    st.dataframe(
                        [
                            {
                                "Name": it["name"],
                                "Qty": it["qty"],
                                "Line total": it["line_total"],
                                "Food": "Yes" if it.get("is_food", True) else "No",
                                "Key": it.get("item_key"),
                            }
                            for it in receipt["items"]
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
