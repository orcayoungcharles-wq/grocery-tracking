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
import aldi_parser_shim as aldi_parser
import kroger_parser_shim as kroger_parser

st.set_page_config(
    page_title="Grocery Tracker",
    page_icon="🛒",
    layout="wide",
)


def make_item_key(name: str, sku=None, store: str = "kroger") -> str:
    if store == "aldi" and sku:
        return str(sku)
    return name.strip().lower()


def parse_uploaded_text(text: str, store: str, filename: str) -> dict:
    """Parse receipt text and return a dict for datastore.add_receipt."""
    if store == "aldi":
        result = aldi_parser.parse_receipt(text)
        items_raw = result.items
        subtotal = result.subtotal
        reconciles = result.reconciles
        tax = 0.0
        normalized = []
        for item in items_raw:
            sku = getattr(item, "sku", None)
            normalized.append({
                "item_key": make_item_key(item.name, sku, "aldi"),
                "name": item.name,
                "sku": sku,
                "qty": item.qty,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
                "tax_code": getattr(item, "tax_code", None),
                "is_food": item.is_food,
            })
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
            d = dict(item)
            d["item_key"] = make_item_key(d.get("name", ""), store="kroger")
            normalized.append(d)

    # Date from filename if possible, else today
    import re
    from datetime import date
    m = re.search(r"(\d{1,2})[._-](\d{1,2})", filename)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = date.today().year
        receipt_date = f"{year}-{month:02d}-{day:02d}"
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
    ["Dashboard", "Shopping List", "Receipts", "Upload"],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")
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

    if not spending:
        st.info("No spending data yet. Upload receipts on the Upload page.")
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

        # By-store breakdown for each month
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
# Upload
# ---------------------------------------------------------------------------
elif page == "Upload":
    st.title("Upload receipt")
    st.caption("Upload OCR'd receipt text (.txt) or paste text. PDFs need OCR first.")

    store = st.selectbox("Store", ["aldi", "kroger"])
    uploaded = st.file_uploader("Receipt text file", type=["txt"])
    pasted = st.text_area("Or paste receipt text", height=200)

    text = None
    filename = "pasted.txt"
    if uploaded is not None:
        text = uploaded.read().decode("utf-8", errors="replace")
        filename = uploaded.name
        detected = detect_store_from_name(filename)
        if detected and detected != store:
            st.warning(f"Filename suggests store={detected}; you selected {store}.")
    elif pasted.strip():
        text = pasted

    if text and st.button("Parse & store", type="primary"):
        try:
            receipt = parse_uploaded_text(text, store, filename)
            datastore.add_receipt(receipt)
            st.success(
                f"Stored {receipt['filename']}: {len(receipt['items'])} items, "
                f"subtotal ${receipt['subtotal']:.2f}, "
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
