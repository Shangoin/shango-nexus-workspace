"""
nexus-dashboard/dashboard.py
Shango Nexus — Streamlit command centre.
6 pages: Overview · Aurora · Janus · Evolution · Events · Revenue
Runs on port 8501 (Render deployment).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────
BACKEND_URL = os.environ.get("NEXUS_BACKEND_URL", "http://localhost:8000")
REFRESH_INTERVAL = 30  # seconds

st.set_page_config(
    page_title="Shango Nexus HQ",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  [data-testid="stSidebar"] { background: #0f0f1a; }
  .nexus-metric { font-size: 2rem; font-weight: 700; color: #7c3aed; }
  .pod-card { border: 1px solid #7c3aed; border-radius: 8px; padding: 12px; margin: 4px; }
  .status-ok { color: #10b981; }
  .status-warn { color: #f59e0b; }
</style>
""", unsafe_allow_html=True)


# ── Data fetchers ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=REFRESH_INTERVAL)
def fetch(path: str) -> dict:
    try:
        r = httpx.get(f"{BACKEND_URL}{path}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


def get_health() -> dict:
    return fetch("/health")


def get_kpis() -> dict:
    return fetch("/api/nexus/kpis")


def get_pods() -> list[dict]:
    data = fetch("/api/nexus/pods")
    return data.get("pods", [])


def get_aurora_stats() -> dict:
    return fetch("/api/aurora/stats")


def get_aurora_calls(limit: int = 100) -> list[dict]:
    return fetch(f"/api/aurora/calls?limit={limit}").get("calls", [])


def get_evolution_history(limit: int = 200) -> list[dict]:
    return fetch(f"/api/evolution/history?limit={limit}").get("evolutions", [])


def get_events(limit: int = 100) -> list[dict]:
    return fetch(f"/api/nexus/events?limit={limit}").get("events", [])


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://shango.in/logo.png", width=120) if False else st.title("🧠 Shango Nexus")
    st.caption("Alien Intelligence HQ")
    st.divider()
    page = st.radio("Navigate", ["Overview", "Aurora", "Janus", "Evolution", "Events", "Revenue"])
    st.divider()
    health = get_health()
    if "error" not in health:
        st.success(f"✅ Backend online\nUptime: {health.get('uptime_seconds', 0)//3600}h {(health.get('uptime_seconds', 0)%3600)//60}m")
    else:
        st.error("❌ Backend offline")
    st.caption(f"Auto-refresh: {REFRESH_INTERVAL}s")
    if st.button("⚡ Refresh now"):
        st.cache_data.clear()
        st.rerun()


# ── Page: Overview ────────────────────────────────────────────────────────────

if page == "Overview":
    st.title("🚀 Nexus Overview")
    kpis = get_kpis()
    pods = get_pods()

    if "error" not in kpis:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Active Pods", kpis.get("total_pods", 0))
        c2.metric("Aurora Calls", kpis.get("aurora", {}).get("total_calls", 0))
        c3.metric("Evolution Cycles", kpis.get("evolution", {}).get("total_cycles", 0))
        c4.metric("Est. MRR (USD)", f"${kpis.get('estimated_mrr_usd', 0):,}")
    else:
        st.warning("Could not load KPIs — is the backend running?")

    st.subheader("Prometheus Organs")
    cols = st.columns(4)
    for i, pod in enumerate(pods):
        with cols[i % 4]:
            completion = pod.get("completion", 0)
            color = "#10b981" if completion >= 80 else "#f59e0b" if completion >= 50 else "#ef4444"
            st.markdown(f"""
            <div class="pod-card">
              <b>{pod['label']}</b><br>
              <small>{pod['role']}</small><br>
              <div style="background:#1f1f30;border-radius:4px;margin-top:4px">
                <div style="background:{color};width:{completion}%;height:6px;border-radius:4px"></div>
              </div>
              <small>{completion}% done</small>
            </div>
            """, unsafe_allow_html=True)


# ── Page: Aurora ──────────────────────────────────────────────────────────────

elif page == "Aurora":
    st.title("🎙️ Aurora — Sales Organ")
    stats = get_aurora_stats()
    calls = get_aurora_calls()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Calls", stats.get("total_calls", 0))
    c2.metric("Avg Score", stats.get("avg_score", 0))
    c3.metric("Target", "20% booking rate")

    if calls:
        df = pd.DataFrame(calls)
        if "overall_score" in df.columns:
            fig = px.histogram(df, x="overall_score", title="Call Score Distribution", color_discrete_sequence=["#7c3aed"])
            st.plotly_chart(fig, use_container_width=True)
        if "created_at" in df.columns:
            df["date"] = pd.to_datetime(df["created_at"]).dt.date
            daily = df.groupby("date").size().reset_index(name="calls")
            fig2 = px.line(daily, x="date", y="calls", title="Calls per Day")
            st.plotly_chart(fig2, use_container_width=True)
        st.dataframe(df[["created_at", "overall_score", "geo_region"] if "geo_region" in df.columns else df.columns[:5]].head(20))
    else:
        st.info("No call data yet. Deploy Aurora and start routing leads.")

    # ── Sprint 7: Champion vs Challenger A/B Analytics ────────────────────────
    st.divider()
    st.subheader("🏆 Champion vs Challenger — Script Element Performance")

    @st.cache_data(ttl=30)
    def get_variant_stats_aurora() -> list:
        return fetch("/api/nexus/variant-stats?pod=aurora").get("variants", [])

    variant_stats = get_variant_stats_aurora()
    ELEMENTS = ["opener", "objection_reframe", "closing_ask", "follow_up_subject"]
    ELEMENT_LABELS = {
        "opener": "🎙️ Opening Line",
        "objection_reframe": "🛡️ Objection Reframe",
        "closing_ask": "🤝 Closing Ask",
        "follow_up_subject": "📧 Follow-up Subject",
    }
    if not variant_stats:
        st.info("No variant stats yet. Aurora A/B engine seeds on first MARS cycle.")
    else:
        for element in ELEMENTS:
            element_variants = [
                v for v in variant_stats
                if v.get("element") == element and not v.get("retired", False)
            ]
            if not element_variants:
                continue
            sorted_variants = sorted(element_variants, key=lambda x: x.get("win_rate", 0), reverse=True)
            champion = sorted_variants[0] if sorted_variants else None
            challenger = sorted_variants[1] if len(sorted_variants) > 1 else None
            with st.expander(ELEMENT_LABELS.get(element, element), expanded=True):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**🏆 Current Champion**")
                    if champion:
                        st.metric("Win Rate", f"{champion.get('win_rate', 0):.0%}")
                        st.metric("Total Calls", champion.get("calls", 0))
                        if champion.get("promoted"):
                            st.success("✅ Live in Vapi — Auto-promoted")
                        st.caption(f'"{str(champion.get("variant_text", ""))[:120]}..."')
                with col2:
                    st.markdown("**⚔️ Current Challenger**")
                    if challenger:
                        c_win_rate = challenger.get("win_rate", 0)
                        st.metric("Win Rate", f"{c_win_rate:.0%}")
                        st.metric("Total Calls", challenger.get("calls", 0))
                        gap = (champion.get("win_rate", 0) - c_win_rate) * 100 if champion else 0
                        st.caption(f"Gap to champion: {gap:.1f}pp")
                    else:
                        st.info("Need more variants")
                if len(sorted_variants) > 0:
                    fig = go.Figure(go.Bar(
                        x=[v.get("win_rate", 0) * 100 for v in sorted_variants[:5]],
                        y=[f"v{i+1} ({v.get('calls', 0)} calls)" for i, v in enumerate(sorted_variants[:5])],
                        orientation="h",
                        marker_color=["#FFD700" if i == 0 else "#4D96FF" for i in range(min(5, len(sorted_variants)))]
                    ))
                    fig.update_layout(
                        xaxis_title="Win Rate (%)",
                        paper_bgcolor="#07070E",
                        plot_bgcolor="#07070E",
                        font=dict(color="white"),
                        height=200,
                        margin=dict(l=10, r=10, t=10, b=10),
                    )
                    st.plotly_chart(fig, use_container_width=True)


# ── Page: Janus ───────────────────────────────────────────────────────────────

elif page == "Janus":
    st.title("📈 Janus — Trading Brain")
    st.info("Regime detection and MCTS trade signals. Connect Polygon/Alpaca for live data.")
    with st.form("regime_form"):
        symbol = st.text_input("Symbol", "NIFTY50")
        submitted = st.form_submit_button("Detect Regime")
    if submitted:
        with st.spinner("Running MCTS regime detection..."):
            try:
                r = httpx.post(f"{BACKEND_URL}/api/janus/regime", json={"symbol": symbol, "lookback_days": 30}, timeout=30)
                result = r.json()
                st.success(f"Regime: **{result.get('regime', 'unknown')}** (confidence: {result.get('confidence', 0):.1%})")
            except Exception as exc:
                st.error(str(exc))


# ── Page: Evolution ───────────────────────────────────────────────────────────

elif page == "Evolution":
    st.title("🧬 DEAP Genetic Evolution")
    evolutions = get_evolution_history()
    if evolutions:
        df = pd.DataFrame(evolutions)
        if "best_score" in df.columns and "pod" in df.columns:
            fig = px.line(df, x=df.index, y="best_score", color="pod", title="Evolution Best Score per Cycle")
            st.plotly_chart(fig, use_container_width=True)
        if "pod" in df.columns:
            pod_summary = df.groupby("pod")["best_score"].agg(["max", "mean", "count"]).reset_index()
            pod_summary.columns = ["Pod", "Best Score", "Avg Score", "Cycles"]
            st.dataframe(pod_summary)
        st.dataframe(df.sort_values("timestamp", ascending=False).head(20))

        # S5-06: Genome fitness heatmap
        st.subheader("🧬 Genome Fitness Heatmap")
        import numpy as np

        GENE_NAMES = [
            "Temperature", "Follow-up\nCadence", "Opener\nStyle",
            "Objection\nDepth", "Closing\nUrgency", "Tone\nFormality",
            "Content\nDensity", "Personalization",
        ]

        pods_list = list(set(e.get("pod", "unknown") for e in evolutions))
        heatmap_data = []

        for pod_name in pods_list:
            pod_evols = [e for e in evolutions if e.get("pod") == pod_name]
            best_genomes = [
                e.get("best_genome", [])
                for e in pod_evols[-10:]
                if isinstance(e.get("best_genome"), list)
            ]
            if best_genomes:
                padded = [g[:8] + [0.5] * max(0, 8 - len(g)) for g in best_genomes]
                avg_genes = np.mean(padded, axis=0).tolist()
            else:
                avg_genes = [0.5] * 8
            heatmap_data.append(avg_genes[:8])

        if heatmap_data:
            fig_heat = go.Figure(data=go.Heatmap(
                z=heatmap_data,
                x=GENE_NAMES,
                y=pods_list,
                colorscale="Plasma",
                zmin=0,
                zmax=1,
                text=[[f"{v:.2f}" for v in row] for row in heatmap_data],
                texttemplate="%{text}",
                hovertemplate="Pod: %{y}<br>Gene: %{x}<br>Value: %{z:.2f}<extra></extra>",
            ))
            fig_heat.update_layout(
                title="🧬 Genome Fitness Heatmap — Which Genes Drive Performance",
                paper_bgcolor="#07070E",
                plot_bgcolor="#07070E",
                font=dict(color="white"),
                height=400,
            )
            st.plotly_chart(fig_heat, use_container_width=True)
            with st.expander("🔍 Gene Decoder"):
                gene_df = pd.DataFrame({
                    "Gene": GENE_NAMES,
                    "0.0 = ...": ["Conservative", "Aggressive (1 day)", "Empathy", "Brief",
                                  "Soft close", "Casual", "Sparse", "Generic"],
                    "1.0 = ...": ["Creative", "Gentle (7 days)", "Question", "Deep dive",
                                  "Hard close", "Formal", "Rich detail", "Hyper-personal"],
                })
                st.dataframe(gene_df, use_container_width=True)
        else:
            st.info("No genome data in evolution history yet.")

        # S6-05: Per-pod gene fitness drilldown
        st.subheader("🔬 Pod Gene Fitness Drilldown")
        drilldown_pods = sorted(set(e.get("pod", "unknown") for e in evolutions))
        if drilldown_pods:
            selected_pod = st.selectbox("Select pod to inspect gene trends:", drilldown_pods, key="gene_drilldown")

            GENE_NAMES_FULL = [
                "Temperature", "Follow-up Cadence", "Opener Style",
                "Objection Depth", "Closing Urgency", "Tone Formality",
                "Content Density", "Personalization",
            ]
            GENE_COLORS = [
                "#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF",
                "#FF922B", "#CC5DE8", "#20C997", "#F06595",
            ]

            pod_evolutions = sorted(
                [e for e in evolutions if e.get("pod") == selected_pod],
                key=lambda x: x.get("timestamp", ""),
            )

            if pod_evolutions:
                cycles = list(range(1, len(pod_evolutions) + 1))
                fig_drill = go.Figure()

                for gene_idx, (gene_name, color) in enumerate(zip(GENE_NAMES_FULL, GENE_COLORS)):
                    gene_values = [
                        (e.get("best_genome") or [0.5] * 8)[gene_idx]
                        if isinstance(e.get("best_genome"), list) and len(e.get("best_genome", [])) > gene_idx
                        else 0.5
                        for e in pod_evolutions
                    ]
                    fig_drill.add_trace(go.Scatter(
                        x=cycles,
                        y=gene_values,
                        name=gene_name,
                        line=dict(color=color, width=2),
                        mode="lines+markers",
                        hovertemplate=f"Gene: {gene_name}<br>Cycle: %{{x}}<br>Value: %{{y:.3f}}<extra></extra>",
                    ))

                fig_drill.update_layout(
                    title=f"🧬 {selected_pod.upper()} — Gene Fitness Over {len(cycles)} Evolution Cycles",
                    xaxis_title="Evolution Cycle",
                    yaxis_title="Gene Value (0.0 – 1.0)",
                    yaxis=dict(range=[0, 1]),
                    paper_bgcolor="#07070E",
                    plot_bgcolor="#07070E",
                    font=dict(color="white"),
                    legend=dict(bgcolor="#07070E"),
                    height=450,
                )
                st.plotly_chart(fig_drill, use_container_width=True)
            else:
                st.info(f"No evolution data yet for pod '{selected_pod}'.")
    else:
        st.info("No evolution data yet. Trigger `/api/evolution/trigger-all` to run first cycle.")


# ── Page: Events ──────────────────────────────────────────────────────────────

elif page == "Events":
    st.title("⚡ Live Event Stream")

    # Controls row (S5-05)
    col_hdr, col_pod, col_toggle = st.columns([3, 2, 1])
    with col_pod:
        filter_pod = st.selectbox(
            "Filter Pod",
            ["all", "aurora", "janus", "dan", "syntropy", "ralph", "nexus"],
        )
    with col_toggle:
        auto_refresh = st.toggle("Auto (3s)", value=False)
    if col_hdr.button("⚡ Refresh Now"):
        st.cache_data.clear()
        st.rerun()

    # Fetch events — 3s TTL, optional pod filter
    @st.cache_data(ttl=3)
    def get_live_events(limit: int = 100, pod: str = "all") -> list[dict]:
        import requests as _req
        url = f"{BACKEND_URL}/api/nexus/events?limit={limit}"
        if pod != "all":
            url += f"&pod={pod}"
        try:
            r = _req.get(url, timeout=3)
            return r.json().get("events", [])
        except Exception:
            return []

    events = get_live_events(100, filter_pod)

    col_hdr.caption(
        f"Showing {len(events)} events · pod=**{filter_pod}** · "
        f"auto-refresh {'ON ✅' if auto_refresh else 'OFF'}"
    )

    # Metrics row
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", len(events))
    m2.metric("Aurora", sum(1 for e in events if e.get("pod_name", e.get("pod")) == "aurora"))
    m3.metric("Payments", sum(1 for e in events if "payment" in e.get("event_type", "")))
    m4.metric("Violations", sum(1 for e in events if "violation" in e.get("event_type", "")))

    POD_EMOJI = {
        "aurora": "🔵", "janus": "🟡", "dan": "🟣",
        "syntropy": "🟢", "ralph": "🟠", "sentinel_prime": "🔴",
        "shango_automation": "⚪", "syntropy_war_room": "💚", "nexus": "⚪",
    }

    st.subheader("📡 Live Feed")
    for event in events[:50]:
        pod = event.get("pod_name", event.get("pod", "nexus"))
        emoji = POD_EMOJI.get(pod, "🔘")
        ts = str(event.get("created_at", event.get("timestamp", "")))[:19].replace("T", " ")
        etype = event.get("event_type", "unknown")

        if "payment" in etype:
            st.success(f"{emoji} **{pod}** › `{etype}` — {ts}")
        elif "violation" in etype or "error" in etype or "fail" in etype:
            st.error(f"{emoji} **{pod}** › `{etype}` — {ts}")
        elif "evolved" in etype or "genome" in etype or "retired" in etype:
            st.info(f"{emoji} **{pod}** › `{etype}` — {ts}")
        elif "scout" in etype or "prospect" in etype:
            st.warning(f"{emoji} **{pod}** › `{etype}` — {ts}")
        else:
            st.write(f"{emoji} **{pod}** › `{etype}` — {ts}")

    if events:
        st.divider()
        df = pd.DataFrame(events)
        chart_col1, chart_col2 = st.columns(2)

        pod_col = "pod_name" if "pod_name" in df.columns else ("pod" if "pod" in df.columns else None)
        if pod_col:
            pod_counts = df[pod_col].value_counts().reset_index()
            pod_counts.columns = ["Pod", "Events"]
            fig = px.bar(pod_counts, x="Pod", y="Events", title="Events by Pod", color="Pod")
            chart_col1.plotly_chart(fig, use_container_width=True)

        if "event_type" in df.columns:
            type_counts = df["event_type"].value_counts().head(10).reset_index()
            type_counts.columns = ["Event Type", "Count"]
            fig2 = px.bar(type_counts, x="Event Type", y="Count", title="Top 10 Event Types")
            chart_col2.plotly_chart(fig2, use_container_width=True)

        with st.expander("🔍 Raw event table"):
            sort_col = next(
                (c for c in ["created_at", "timestamp"] if c in df.columns),
                df.columns[0],
            )
            st.dataframe(df.sort_values(sort_col, ascending=False).head(100), use_container_width=True)
    else:
        st.info("No events yet. Start making API calls to see the event stream.")

    # Auto-refresh loop — 3s (S5-05)
    if auto_refresh:
        import time as _time
        _time.sleep(3)
        st.rerun()


# ── Page: Revenue ─────────────────────────────────────────────────────────────

elif page == "Revenue":
    st.title("💰 Revenue Dashboard")
    pods = get_pods()
    revenue_pods = [p for p in pods if p.get("mrr", 0) > 0]

    col1, col2 = st.columns(2)
    total_mrr = sum(p["mrr"] for p in pods)
    col1.metric("Total Addressable MRR", f"${total_mrr}/mo")
    col2.metric("Target Week 4", "$1,000 MRR")

    df = pd.DataFrame([{"Pod": p["label"], "MRR ($)": p["mrr"], "Role": p["role"]} for p in revenue_pods])
    fig = px.bar(df, x="Pod", y="MRR ($)", title="Revenue by Pod (at full activation)", color="Pod")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Pricing")
    pricing = [
        {"Product": "Aurora Pro", "Price": "$99/mo", "Status": "🟢 Live"},
        {"Product": "DAN Pro", "Price": "$49/mo", "Status": "🟡 Beta"},
        {"Product": "Sentinel Prime", "Price": "$199/mo", "Status": "🟡 Beta"},
        {"Product": "Shango Automation", "Price": "$19/mo", "Status": "🟢 Live"},
        {"Product": "Syntropy Pack", "Price": "$29/pack", "Status": "🟢 Live"},
        {"Product": "Nexus Pro Bundle", "Price": "$299/mo", "Status": "🔴 Planned"},
    ]
    st.dataframe(pd.DataFrame(pricing))

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(f"Shango Nexus v2.0 · Sprint 5 · team@shango.in · shango.in · {datetime.now().strftime('%Y-%m-%d %H:%M')} IST")
