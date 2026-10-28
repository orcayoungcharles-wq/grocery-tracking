"""
Grocery Receipt Tracker — Streamlit dashboard
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from datetime import datetime

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
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Theme — dark tactical / ops-dashboard aesthetic
# ---------------------------------------------------------------------------
THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
  font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}

.stApp {
  background: linear-gradient(160deg, #0a0e14 0%, #0d1219 40%, #0a1018 100%);
  color: #e8edf2;
}

/* Sidebar */
section[data-testid="stSidebar"] {
  background: #080c12 !important;
  border-right: 1px solid #1a2332;
}
section[data-testid="stSidebar"] * {
  color: #c8d0da !important;
}
section[data-testid="stSidebar"] .stRadio label {
  color: #c8d0da !important;
}

/* Headings */
h1, h2, h3 {
  font-family: 'IBM Plex Sans', sans-serif !important;
  font-weight: 600 !important;
  letter-spacing: -0.02em;
  color: #f0f4f8 !important;
}
h1 {
  font-size: 1.85rem !important;
  border-bottom: 1px solid #1e2a3a;
  padding-bottom: 0.4rem;
}

/* Metric cards */
div[data-testid="stMetric"] {
  background: linear-gradient(145deg, #111820 0%, #0e141c 100%);
  border: 1px solid #1e2a3a;
  border-radius: 6px;
  padding: 0.85rem 1rem;
  box-shadow: 0 0 0 1px rgba(0, 180, 255, 0.04);
}
div[data-testid="stMetric"] label {
  color: #7a8a9a !important;
  font-size: 0.75rem !important;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 500 !important;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
  color: #00c8ff !important;
  font-family: 'IBM Plex Mono', monospace !important;
  font-weight: 500 !important;
  font-size: 1.45rem !important;
}
div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
  font-size: 0.8rem !important;
}

/* Big month banner */
.month-banner {
  font-family: 'IBM Plex Sans', sans-serif;
  font-size: 2.4rem;
  font-weight: 700;
  letter-spacing: -0.03em;
  color: #f0f4f8;
  margin: 0.2rem 0 0.6rem 0;
  line-height: 1.1;
}
.month-banner span {
  color: #00c8ff;
  font-weight: 600;
}

/* Section labels */
.section-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: #5a6a7a;
  font-weight: 600;
  margin-bottom: 0.35rem;
}

/* Rank rows */
.rank-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid #1a2332;
  font-size: 0.9rem;
}
.rank-num {
  font-family: 'IBM Plex Mono', monospace;
  color: #00c8ff;
  font-weight: 600;
  min-width: 1.6rem;
}
.rank-up { color: #3dd68c; font-weight: 600; }
.rank-down { color: #ff6b6b; font-weight: 600; }
.rank-new { color: #f0c040; font-weight: 600; }

/* Buttons */
.stButton > button {
  border-radius: 4px !important;
  border: 1px solid #1e3a5f !important;
  background: #0f1a28 !important;
  color: #c8d8e8 !important;
}
.stButton > button:hover {
  border-color: #00c8ff !important;
  color: #00c8ff !important;
}
.stButton > button[kind="primary"] {
  background: linear-gradient(135deg, #0a3a55 0%, #0d4a6a 100%) !important;
  border-color: #00a0d0 !important;
  color: #e8f4ff !important;
}

/* Radio / select */
div[role="radiogroup"] label {
  color: #c8d0da !important;
}

/* Expander */
.streamlit-expanderHeader {
  background: #0e141c !important;
  border: 1px solid #1a2332 !important;
  border-radius: 4px !important;
  color: #c8d0da !important;
}

/* Dataframes */
div[data-testid="stDataFrame"] {
  border: 1px solid #1a2332;
  border-radius: 4px;
}

/* Divider */
hr {
  border-color: #1a2332 !important;
}

/* Caption */
.stCaption, small {
  color: #6a7a8a !important;
}

/* Hide Streamlit chrome clutter a bit */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
</style>
"""

st.markdown(THEME_CSS, unsafe_allow_html=True)

# Plotly shared dark layout
PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="IBM Plex Sans, sans-serif", color="#c8d0da", size=12),
    margin=dict(t=48, b=40, l=48, r=24),
    dragmode=False,
)
CHART_CONFIG = {"staticPlot": True, "displayModeBar": False}

# Bar colors (cool cyan/blue) vs pie colors (fixed per store)
BAR_COLOR = "#00a8e8"
BAR_COLOR_ALT = "#0077aa"
STORE_PIE_COLORS = {
    "aldi": "#f0a030",    # amber
    "kroger": "#3d8fd1",  # steel blue
}
PIE_FALLBACK = ["#e07040", "#c05060", "#8060a0", "#30a090"]


def _store_color(store: str) -> str:
    return STORE_PIE_COLORS.get((store or "").lower(), "#888888")


def _pie_colors_for(by_store: dict) -> list[str]:
    colors = []
    fallback_i = 0
    for s in by_store.keys():
        key = (s or "").lower()
        if key in STORE_PIE_COLORS:
            colors.append(STORE_PIE_COLORS[key])
        else:
            colors.append(PIE_FALLBACK[fallback_i % len(PIE_FALLBACK)])
            fallback_i += 1
    return colors


def _store_legend_html() -> str:
    """Single small Aldi / Kroger color key for pie sections."""
    return (
        '<div style="display:flex;gap:1.25rem;align-items:center;'
        'font-size:0.78rem;color:#8a9aaa;margin:0.15rem 0 0.5rem 0;">'
        '<span style="display:flex;align-items:center;gap:0.35rem;">'
        '<span style="width:10px;height:10px;border-radius:50%;'
        'background:#f0a030;display:inline-block;"></span>Aldi</span>'
        '<span style="display:flex;align-items:center;gap:0.35rem;">'
        '<span style="width:10px;height:10px;border-radius:50%;'
        'background:#3d8fd1;display:inline-block;"></span>Kroger</span>'
        "</div>"
    )


def parse_uploaded_text(text: str, store: str, filename: str) -> dict:
    """Parse receipt text and return a dict for datastore.add_receipt."""
    import re
    store_upper = store.upper()
    if store == "aldi":
        result = aldi_parser.parse_receipt(text)
        items_raw = result.items
        subtotal = result.subtotal
        reconciles = result.reconciles
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


def _amt(d) -> float:
    return float(d.get("total", d.get("food", 0)) or 0)


def _month_label(key: str) -> str:
    """'2026-10' → 'October 2026'."""
    try:
        dt = datetime.strptime(key + "-01", "%Y-%m-%d")
        return dt.strftime("%B %Y")
    except ValueError:
        return key


def _prev_month_key(key: str) -> str:
    try:
        dt = datetime.strptime(key + "-01", "%Y-%m-%d")
        if dt.month == 1:
            return f"{dt.year - 1}-12"
        return f"{dt.year}-{dt.month - 1:02d}"
    except ValueError:
        return key


# ---------------------------------------------------------------------------
# Sidebar — multi-user (lightweight, no password)
# ---------------------------------------------------------------------------
st.sidebar.markdown("### 🛒 Grocery Tracker")

# Restore / init active user
if "active_user" not in st.session_state:
    st.session_state["active_user"] = datastore.DEFAULT_USER
datastore.set_user(st.session_state["active_user"])

users = datastore.list_users()
user_col1, user_col2 = st.sidebar.columns([3, 1])
with user_col1:
    selected_user = st.selectbox(
        "User",
        options=users,
        index=users.index(st.session_state["active_user"])
        if st.session_state["active_user"] in users
        else 0,
        key="user_select",
    )
if selected_user != st.session_state["active_user"]:
    st.session_state["active_user"] = selected_user
    datastore.set_user(selected_user)
    st.rerun()

with st.sidebar.expander("Add user"):
    new_name = st.text_input("Name", key="new_user_name", placeholder="e.g. alex")
    if st.button("Create user", key="create_user_btn") and new_name.strip():
        slug = datastore.add_user(new_name.strip())
        st.session_state["active_user"] = slug
        datastore.set_user(slug)
        st.rerun()

st.sidebar.caption(f"Active: **{datastore.current_user()}**")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    ["Dashboard", "Shopping List", "Receipts", "Upload", "Backup"],
    label_visibility="collapsed",
)

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
if page == "Dashboard":
    months = datastore.get_available_months()
    now = datetime.utcnow()
    default_key = f"{now.year}-{now.month:02d}"
    if default_key not in months and months:
        default_key = months[-1]

    # Month selector
    col_sel, col_spacer = st.columns([2, 3])
    with col_sel:
        if months:
            selected = st.selectbox(
                "View month",
                options=months,
                index=months.index(default_key) if default_key in months else len(months) - 1,
                format_func=_month_label,
                label_visibility="collapsed",
            )
        else:
            selected = default_key
            st.info("No spending data yet.")

    # Big month banner
    st.markdown(
        f'<div class="month-banner"><span>{_month_label(selected)}</span></div>',
        unsafe_allow_html=True,
    )

    selected_data = datastore.get_month_data(selected)
    prev_key = _prev_month_key(selected)
    prev_data = datastore.get_month_data(prev_key)
    receipts_in_month = datastore.get_receipts_in_month(selected)
    n_trips = len(receipts_in_month)
    avg_trip = (_amt(selected_data) / n_trips) if n_trips else 0.0
    all_receipts = datastore.load_receipts()

    # KPI row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Month spend", f"${_amt(selected_data):.2f}",
              delta=f"${_amt(selected_data) - _amt(prev_data):.2f} vs prior")
    c2.metric("Trips this month", str(n_trips))
    c3.metric("Avg per trip", f"${avg_trip:.2f}")
    c4.metric("All-time receipts", str(len(all_receipts)))

    st.markdown("---")
    view = st.radio("View", ["Weekly", "Monthly"], horizontal=True, index=0)

    weekly = datastore.get_weekly_spending(max_weeks=8)
    spending = datastore.get_monthly_spending()

    if not spending and not weekly:
        st.info("No spending data yet. Upload receipts on the Upload page.")
    elif view == "Weekly":
        if not weekly:
            st.info("No weekly data yet.")
        else:
            labels = [weekly[w].get("label", w) for w in weekly]
            totals = [weekly[w].get("total", 0) for w in weekly]
            fig = px.bar(
                x=labels,
                y=totals,
                labels={"x": "", "y": "Spend ($)"},
                title="Weekly spend (last 8 weeks)",
                text=[f"${v:.2f}" for v in totals],
            )
            fig.update_traces(
                textposition="outside",
                marker_color=BAR_COLOR,
                marker_line_width=0,
            )
            fig.update_layout(
                **PLOTLY_LAYOUT,
                yaxis_title="USD",
                xaxis_title="",
                height=340,
                title_font_color="#e8edf2",
            )
            fig.update_xaxes(gridcolor="#1a2332", zeroline=False)
            fig.update_yaxes(gridcolor="#1a2332", zeroline=False)
            st.plotly_chart(fig, use_container_width=True, config=CHART_CONFIG)

            # Small pie per week
            st.markdown(
                '<div class="section-label">Spend by store · each week</div>',
                unsafe_allow_html=True,
            )
            st.markdown(_store_legend_html(), unsafe_allow_html=True)
            week_keys = list(weekly.keys())
            n = len(week_keys)
            # Up to 8 compact pies per row
            for row_start in range(0, n, 8):
                row_keys = week_keys[row_start : row_start + 8]
                cols = st.columns(len(row_keys))
                for col, wk in zip(cols, row_keys):
                    payload = weekly[wk]
                    by_store = payload.get("by_store") or {}
                    with col:
                        if by_store:
                            names = [
                                datastore.display_store(s) for s in by_store.keys()
                            ]
                            vals = list(by_store.values())
                            colors = _pie_colors_for(by_store)
                        else:
                            # Placeholder so empty weeks stay aligned with bars
                            names, vals, colors = ["—"], [1], ["#1a2332"]
                        fig_p = go.Figure(
                            data=[
                                go.Pie(
                                    labels=names,
                                    values=vals,
                                    hole=0.5,
                                    marker=dict(colors=colors),
                                    textinfo="none",
                                    hovertemplate=(
                                        "%{label}: $%{value:.2f}<extra></extra>"
                                        if by_store
                                        else "No trips<extra></extra>"
                                    ),
                                )
                            ]
                        )
                        fig_p.update_layout(
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            font=dict(
                                family="IBM Plex Sans, sans-serif",
                                color="#c8d0da",
                                size=10,
                            ),
                            height=110,
                            margin=dict(t=22, b=4, l=4, r=4),
                            title=dict(
                                text=payload.get("label", wk),
                                font=dict(size=10, color="#8a9aaa"),
                            ),
                            showlegend=False,
                        )
                        st.plotly_chart(
                            fig_p, use_container_width=True, config=CHART_CONFIG
                        )

    else:
        # Calendar-month bars
        months_list = list(spending.keys())
        totals = [_amt(spending[m]) for m in months_list]
        display_months = [_month_label(m) for m in months_list]
        fig = px.bar(
            x=display_months,
            y=totals,
            labels={"x": "Month", "y": "Spend ($)"},
            title="Monthly spend (calendar months)",
            text=[f"${v:.2f}" for v in totals],
        )
        fig.update_traces(textposition="outside", marker_color=BAR_COLOR)
        fig.update_layout(
            **PLOTLY_LAYOUT,
            yaxis_title="USD",
            xaxis_title="",
            height=340,
            title_font_color="#e8edf2",
        )
        fig.update_xaxes(gridcolor="#1a2332")
        fig.update_yaxes(gridcolor="#1a2332")
        st.plotly_chart(fig, use_container_width=True, config=CHART_CONFIG)

        # Small pie per calendar month (Aldi vs Kroger)
        st.markdown(
            '<div class="section-label">Spend by store · each month</div>',
            unsafe_allow_html=True,
        )
        st.markdown(_store_legend_html(), unsafe_allow_html=True)
        for row_start in range(0, len(months_list), 6):
            row_months = months_list[row_start : row_start + 6]
            cols = st.columns(len(row_months))
            for col, mk in zip(cols, row_months):
                payload = spending[mk]
                by_store = payload.get("by_store") or {}
                if not by_store:
                    continue
                with col:
                    names = [datastore.display_store(s) for s in by_store.keys()]
                    vals = list(by_store.values())
                    fig_p = go.Figure(
                        data=[
                            go.Pie(
                                labels=names,
                                values=vals,
                                hole=0.5,
                                marker=dict(colors=_pie_colors_for(by_store)),
                                textinfo="none",
                                hovertemplate="%{label}: $%{value:.2f}<extra></extra>",
                            )
                        ]
                    )
                    fig_p.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(
                            family="IBM Plex Sans, sans-serif",
                            color="#c8d0da",
                            size=10,
                        ),
                        height=110,
                        margin=dict(t=22, b=4, l=4, r=4),
                        title=dict(
                            text=_month_label(mk),
                            font=dict(size=10, color="#8a9aaa"),
                        ),
                        showlegend=False,
                    )
                    st.plotly_chart(
                        fig_p, use_container_width=True, config=CHART_CONFIG
                    )

    # ---- Extra series: trips, basket, cumulative, sparklines ----
    st.markdown("---")
    st.markdown(
        '<div class="section-label">Trips · basket size · cumulative</div>',
        unsafe_allow_html=True,
    )

    if view == "Weekly":
        series = datastore.get_trip_and_basket_series(max_weeks=8)
        if series:
            labels = [v["label"] for v in series.values()]
            trips = [v["trips"] for v in series.values()]
            baskets = [v["avg_basket"] for v in series.values()]

            c_left, c_right = st.columns(2)
            with c_left:
                fig_t = px.bar(
                    x=labels,
                    y=trips,
                    title="Trips per week",
                    text=trips,
                    labels={"x": "", "y": "Trips"},
                )
                fig_t.update_traces(
                    textposition="outside", marker_color="#5b8def", texttemplate="%{y}"
                )
                fig_t.update_layout(
                    **PLOTLY_LAYOUT, height=260, title_font_color="#e8edf2",
                    yaxis_title="Trips", xaxis_title="",
                )
                fig_t.update_xaxes(gridcolor="#1a2332")
                fig_t.update_yaxes(gridcolor="#1a2332", dtick=1)
                st.plotly_chart(fig_t, use_container_width=True, config=CHART_CONFIG)
            with c_right:
                fig_b = px.bar(
                    x=labels,
                    y=baskets,
                    title="Avg $/trip (basket)",
                    text=[f"${v:.0f}" for v in baskets],
                    labels={"x": "", "y": "USD"},
                )
                fig_b.update_traces(
                    textposition="outside", marker_color="#3dd68c"
                )
                fig_b.update_layout(
                    **PLOTLY_LAYOUT, height=260, title_font_color="#e8edf2",
                    yaxis_title="USD", xaxis_title="",
                )
                fig_b.update_xaxes(gridcolor="#1a2332")
                fig_b.update_yaxes(gridcolor="#1a2332")
                st.plotly_chart(fig_b, use_container_width=True, config=CHART_CONFIG)
    else:
        series = datastore.get_monthly_trip_and_basket()
        if series:
            labels = [v["label"] for v in series.values()]
            trips = [v["trips"] for v in series.values()]
            baskets = [v["avg_basket"] for v in series.values()]

            c_left, c_right = st.columns(2)
            with c_left:
                fig_t = px.bar(
                    x=labels,
                    y=trips,
                    title="Trips per month",
                    text=trips,
                    labels={"x": "", "y": "Trips"},
                )
                fig_t.update_traces(
                    textposition="outside", marker_color="#5b8def", texttemplate="%{y}"
                )
                fig_t.update_layout(
                    **PLOTLY_LAYOUT, height=260, title_font_color="#e8edf2",
                    yaxis_title="Trips", xaxis_title="",
                )
                fig_t.update_xaxes(gridcolor="#1a2332")
                fig_t.update_yaxes(gridcolor="#1a2332", dtick=1)
                st.plotly_chart(fig_t, use_container_width=True, config=CHART_CONFIG)
            with c_right:
                fig_b = px.bar(
                    x=labels,
                    y=baskets,
                    title="Avg $/trip (basket)",
                    text=[f"${v:.0f}" for v in baskets],
                    labels={"x": "", "y": "USD"},
                )
                fig_b.update_traces(
                    textposition="outside", marker_color="#3dd68c"
                )
                fig_b.update_layout(
                    **PLOTLY_LAYOUT, height=260, title_font_color="#e8edf2",
                    yaxis_title="USD", xaxis_title="",
                )
                fig_b.update_xaxes(gridcolor="#1a2332")
                fig_b.update_yaxes(gridcolor="#1a2332")
                st.plotly_chart(fig_b, use_container_width=True, config=CHART_CONFIG)

    # Cumulative spend: previous month + selected month side by side
    prev_month_for_cum = _prev_month_key(selected)
    cum_prev = datastore.get_cumulative_month_spend(prev_month_for_cum)
    cum_cur = datastore.get_cumulative_month_spend(selected)
    if cum_prev or cum_cur:
        c_prev, c_cur = st.columns(2)

        def _cum_fig(rows, title, color):
            fig = go.Figure()
            if rows:
                fig.add_trace(
                    go.Scatter(
                        x=[r["date"] for r in rows],
                        y=[r["cumulative"] for r in rows],
                        mode="lines+markers",
                        line=dict(color=color, width=2),
                        marker=dict(size=5, color=color),
                        fill="tozeroy",
                        fillcolor="rgba(0,200,255,0.08)"
                        if color == "#00c8ff"
                        else "rgba(91,141,239,0.08)",
                        hovertemplate="%{x}<br>Cumulative: $%{y:.2f}<extra></extra>",
                    )
                )
            fig.update_layout(
                **PLOTLY_LAYOUT,
                height=240,
                title=dict(text=title, font=dict(color="#e8edf2", size=13)),
                yaxis_title="USD",
                xaxis_title="",
                margin=dict(t=40, b=32, l=40, r=16),
            )
            fig.update_xaxes(gridcolor="#1a2332", tickfont=dict(size=9))
            fig.update_yaxes(gridcolor="#1a2332")
            return fig

        with c_prev:
            st.plotly_chart(
                _cum_fig(
                    cum_prev,
                    f"Cumulative · {_month_label(prev_month_for_cum)}",
                    "#5b8def",
                ),
                use_container_width=True,
                config=CHART_CONFIG,
            )
        with c_cur:
            st.plotly_chart(
                _cum_fig(
                    cum_cur,
                    f"Cumulative · {_month_label(selected)}",
                    "#00c8ff",
                ),
                use_container_width=True,
                config=CHART_CONFIG,
            )

    # Top-N item sparklines (last 8 weeks)
    st.markdown(
        '<div class="section-label">Top items · weekly spend trend</div>',
        unsafe_allow_html=True,
    )
    sparks = datastore.get_top_item_sparklines(top_n=6, max_weeks=8)
    if sparks:
        spark_cols = st.columns(3)
        for i, item in enumerate(sparks):
            with spark_cols[i % 3]:
                fig_s = go.Figure()
                fig_s.add_trace(
                    go.Scatter(
                        x=item["weeks"],
                        y=item["series"],
                        mode="lines+markers",
                        line=dict(color="#00c8ff", width=1.5),
                        marker=dict(size=4),
                        fill="tozeroy",
                        fillcolor="rgba(0,200,255,0.1)",
                        hovertemplate="%{x}: $%{y:.2f}<extra></extra>",
                    )
                )
                # Show a few x labels (first, mid, last) so time is readable
                n = len(item["weeks"])
                tick_idx = sorted(set([0, n // 2, n - 1])) if n else []
                tick_vals = [item["weeks"][j] for j in tick_idx if j < n]
                fig_s.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(
                        family="IBM Plex Sans, sans-serif", color="#c8d0da", size=10
                    ),
                    height=150,
                    margin=dict(t=36, b=32, l=28, r=8),
                    title=dict(
                        text=(
                            f"{item['name']}  ·  "
                            f"${item.get('avg_per_week', 0):.2f}/wk  ·  "
                            f"${item['total_spend']:.2f} All Time"
                        ),
                        font=dict(size=11, color="#a8b8c8"),
                    ),
                    showlegend=False,
                    xaxis=dict(
                        showgrid=False,
                        zeroline=False,
                        tickmode="array",
                        tickvals=tick_vals,
                        tickfont=dict(size=9, color="#6a7a8a"),
                    ),
                    yaxis=dict(
                        showgrid=True,
                        gridcolor="#1a2332",
                        zeroline=False,
                        tickprefix="$",
                        tickfont=dict(size=9),
                    ),
                )
                st.plotly_chart(fig_s, use_container_width=True, config=CHART_CONFIG)
    else:
        st.caption("Not enough item history for sparklines yet.")

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
                    "Stores": ", ".join(datastore.display_store(s) for s in i["stores"]),
                    "Last bought": i["last_bought"],
                }
                for i in items
            ],
            use_container_width=True,
            hide_index=True,
        )

    # ---- Weekly item rankings (between frequency list and all frequencies) ----
    st.markdown("---")
    st.markdown(
        """
<div style="background:linear-gradient(145deg,#0f1a28 0%,#0c1520 100%);
border:1px solid #1e3a5f;border-radius:8px;padding:1rem 1.1rem 0.75rem 1.1rem;
margin-bottom:0.5rem;">
<div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.14em;
color:#00c8ff;font-weight:600;margin-bottom:0.25rem;">Poll · weekly rankings</div>
<div style="font-size:1.35rem;font-weight:700;color:#f0f4f8;letter-spacing:-0.02em;">
Top items by week</div>
<div style="font-size:0.8rem;color:#7a8a9a;margin-top:0.2rem;">
Ranked by <strong style="color:#c8d0da;">spend that week</strong> (highest $ = #1).
Trend vs the last week you bought that item — not empty gap weeks.
</div>
</div>
""",
        unsafe_allow_html=True,
    )

    week_keys = datastore.get_available_weeks()
    if not week_keys:
        st.caption("No weekly data yet.")
    else:
        default_idx = len(week_keys) - 1
        selected_week = st.selectbox(
            "Week",
            options=week_keys,
            index=default_idx,
            format_func=lambda k: datastore.get_weekly_spending(
                max_weeks=None, fill_gaps=False
            )
            .get(k, {})
            .get("label", k),
            key="rank_week_select",
        )
        ranking_payload = datastore.get_weekly_item_rankings(selected_week, top_n=15)
        ranks = ranking_payload["rankings"]
        prev_lbl = ranking_payload.get("previous_label")
        if prev_lbl:
            st.caption(f"Prior shopping week in poll: {prev_lbl}")

        if not ranks:
            st.caption("No items in this week.")
        else:
            table = []
            for m in ranks:
                rd = m.get("rank_delta")
                pr = m.get("previous_rank")
                if pr is None:
                    trend = "NEW"
                elif rd is None or rd == 0:
                    trend = "—"
                elif rd > 0:
                    trend = f"↑{rd}"
                else:
                    trend = f"↓{abs(rd)}"

                pd = m.get("price_delta")
                if pd is None:
                    price_move = "—"
                elif pd > 0:
                    price_move = f"↑${pd:.2f}"
                elif pd < 0:
                    price_move = f"↓${abs(pd):.2f}"
                else:
                    price_move = "—"

                unit = m.get("avg_unit_price")
                table.append({
                    "#": m.get("current_rank"),
                    "Trend": trend,
                    "Item": m.get("name"),
                    "Week $": f"${m.get('week_spend', 0):.2f}",
                    "Month $": f"${m.get('month_spend', 0):.2f}",
                    "Trips": m.get("times_bought", 0),
                    "Avg qty/trip": m.get("avg_qty_per_trip", 0),
                    "Unit $": f"${unit:.2f}" if unit is not None else "—",
                    "Price Δ": price_move,
                    "Last": m.get("last_bought") or "—",
                })
            st.dataframe(table, use_container_width=True, hide_index=True)

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
                    "Stores": ", ".join(datastore.display_store(s) for s in i["stores"]),
                    "Last bought": i["last_bought"],
                    "Food": "Yes" if i.get("is_food", True) else "No",
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
        for i, r in enumerate(sorted(receipts, key=lambda x: x.get("date", ""), reverse=True)):
            store_disp = datastore.display_store(r.get("store", ""))
            trip_key = r.get("trip_id") or r.get("filename") or str(i)
            with st.expander(
                f"{r.get('date', '?')} · {store_disp} · "
                f"${float(r.get('total') or 0):.2f} · `{trip_key}`"
            ):
                rows = []
                for item in r.get("items", []):
                    rows.append({
                        "Item": item.get("name"),
                        "Qty": item.get("qty"),
                        "Line total": f"${float(item.get('line_total') or 0):.2f}",
                        "Food": "Yes" if item.get("is_food", True) else "No",
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
                st.caption(
                    f"Subtotal ${float(r.get('subtotal') or 0):.2f} · "
                    f"Tax ${float(r.get('tax') or 0):.2f} · "
                    f"Total ${float(r.get('total') or 0):.2f} · "
                    f"Reconciled: {r.get('reconciled')}"
                )
                del_key = f"del_{trip_key}_{i}"
                if st.button("Delete this receipt", key=del_key, type="secondary"):
                    if datastore.delete_receipt(str(trip_key)):
                        st.success(f"Deleted {trip_key}.")
                        st.rerun()
                    else:
                        st.error("Could not find that receipt to delete.")

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
elif page == "Upload":
    import ocr as ocr_mod

    st.title("Upload receipt")
    st.caption(
        "Photo, PDF scan, or text. Images/PDFs are OCR'd with Gemini, then parsed. "
        "Review the text before storing."
    )

    store = st.selectbox(
        "Store",
        ["aldi", "kroger"],
        format_func=lambda s: datastore.display_store(s),
    )
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
                st.warning(
                    f"Filename suggests store={datastore.display_store(detected)}; "
                    f"you selected {datastore.display_store(store)}."
                )
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
                    "You can still store it, or edit the text and try again."
                )
                choice = st.radio(
                    "What do you want to do?",
                    ["Include anyway", "Discard"],
                    horizontal=True,
                    key="reconcile_choice",
                )
            else:
                st.success("Reconciles — line sum matches printed subtotal.")
                choice = "Include anyway"

            if choice == "Include anyway" and st.button("Store receipt", type="primary"):
                try:
                    datastore.add_receipt(preview)
                    st.success(
                        f"Stored {datastore.display_store(store)} receipt "
                        f"({preview['date']}) — ${preview['total']:.2f}"
                    )
                    for k in ("ocr_text", "ocr_filename"):
                        st.session_state.pop(k, None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Parse/store failed: {e}")
                    st.exception(e)

# ---------------------------------------------------------------------------
# Backup
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
        mode = st.radio(
            "Mode",
            ["replace", "merge"],
            horizontal=True,
            help="replace = wipe current data first; merge = add only new trip_ids",
        )
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
            food = sum(
                i["line_total"] for i in r.get("items", []) if i.get("is_food", True)
            )
            st.write(
                f"- **{r.get('date')}** · {datastore.display_store(r.get('store'))} · "
                f"food ${food:.2f} · `{r.get('filename')}`"
            )
