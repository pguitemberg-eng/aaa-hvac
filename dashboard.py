"""
AAA HVAC - STREAMLIT DASHBOARD
Run: streamlit run dashboard.py
"""

import os
import sqlite3
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("SQLITE_DB_PATH", "memory/hvac_leads.db")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "HVAC Pro")

st.set_page_config(
    page_title=f"{BUSINESS_NAME} - AI Command Center",
    page_icon="*",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=DM+Mono&display=swap');
  html, body, [data-testid="stAppViewContainer"] {
    background: #0a1628; color: #e0ecf8;
    font-family: 'Inter', sans-serif;
  }
  [data-testid="stSidebar"] { background: #1e2d42; border-right: 1px solid #2a3f5a; }
  .metric-card {
    background: linear-gradient(135deg, #1e2d42, #152438);
    border: 1px solid #2a3f5a; border-radius: 12px;
    padding: 20px 24px; margin-bottom: 12px;
  }
  .metric-label { font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.1em; color: #8ca0b8; margin-bottom: 6px; }
  .metric-value { font-size: 32px; font-weight: 800; color: #fff; line-height: 1; }
  .metric-sub { font-size: 12px; color: #8ca0b8; margin-top: 4px; }
  .lead-row {
    background: #1a2840; border: 1px solid #2a3f5a;
    border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
  }
  .stButton > button {
    background: linear-gradient(135deg, #1a7fd4, #0f5ba8);
    color: white; border: none; border-radius: 8px;
    font-weight: 700; padding: 8px 20px;
  }
</style>
""", unsafe_allow_html=True)


def get_conn():
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def query(sql, params=()):
    try:
        conn = get_conn()
        if not conn:
            return pd.DataFrame()
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


def get_leads(limit=200):
    return query(
        "SELECT id, name, phone, email, address, service_type, urgency, outcome, "
        "booking_url, followup_count, created_at, updated_at "
        "FROM leads ORDER BY created_at DESC LIMIT ?", (limit,)
    )


def get_stats():
    df = get_leads(1000)
    if df.empty:
        return {"total": 0, "booked": 0, "escalated": 0, "disqualified": 0, "rate": 0.0, "emergency": 0}
    total = len(df)
    booked = (df["outcome"] == "booked").sum()
    escalated = (df["outcome"] == "escalated").sum()
    disqualified = (df["outcome"] == "disqualified").sum()
    emergency = (df["urgency"] == "emergency").sum()
    rate = round(booked / total * 100, 1) if total else 0
    return dict(total=total, booked=booked, escalated=escalated,
                disqualified=disqualified, rate=rate, emergency=emergency)


def check_status():
    return {
        "Anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "Twilio": bool(os.getenv("TWILIO_ACCOUNT_SID")),
        "SendGrid": bool(os.getenv("SENDGRID_API_KEY")),
        "Calendly": bool(os.getenv("CALENDLY_API_KEY")),
        "HubSpot": bool(os.getenv("HUBSPOT_ACCESS_TOKEN")),
        "Google Calendar": bool(os.getenv("GOOGLE_CALENDAR_ID")),
        "Gmail": bool(os.getenv("GMAIL_SENDER_EMAIL")),
        "Vapi.ai": bool(os.getenv("VAPI_API_KEY")),
    }


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## {BUSINESS_NAME}")
    st.markdown("**AI COMMAND CENTER**")
    page = st.radio("Navigation", [
        "Pipeline", "Leads", "Voice Calls", "System Status", "Inject Lead"
    ])
    st.markdown("---")
    if st.button("Refresh"):
        st.rerun()


# ── PIPELINE ─────────────────────────────────────────────────────────────────
if page == "Pipeline":
    st.title("Live Pipeline")
    stats = get_stats()

    c1, c2, c3, c4, c5 = st.columns(5)
    for col, label, val, sub in [
        (c1, "Total Leads", stats["total"], "all time"),
        (c2, "Booked", stats["booked"], f"{stats['rate']}% rate"),
        (c3, "Escalated", stats["escalated"], "need call"),
        (c4, "Disqualified", stats["disqualified"], "not a fit"),
        (c5, "Emergency", stats["emergency"], "high urgency"),
    ]:
        with col:
            st.markdown(f"""
            <div class="metric-card">
              <div class="metric-label">{label}</div>
              <div class="metric-value">{val}</div>
              <div class="metric-sub">{sub}</div>
            </div>""", unsafe_allow_html=True)

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Conversion Funnel")
        total = max(stats["total"], 1)
        for label, n in [
            ("Leads Received", stats["total"]),
            ("Qualified", stats["booked"] + stats["escalated"]),
            ("Booking Link Sent",stats["booked"] + stats["escalated"] // 2),
            ("Confirmed Booked", stats["booked"]),
        ]:
            pct = int(n / total * 100)
            st.markdown(f"""
            <div class="lead-row">
              <div style="display:flex;justify-content:space-between;">
                <span style="color:#fff;">{label}</span>
                <span style="color:#00d4aa;font-weight:700;">{n} ({pct}%)</span>
              </div>
              <div style="height:4px;background:linear-gradient(90deg,#1a7fd4,#00d4aa);
                          width:{pct}%;border-radius:2px;margin-top:6px;"></div>
            </div>""", unsafe_allow_html=True)

    with col_right:
        st.subheader("Outcomes")
        df = get_leads()
        if not df.empty and "outcome" in df.columns:
            counts = df["outcome"].value_counts().reset_index()
            counts.columns = ["Outcome", "Count"]
            fig = px.pie(counts, names="Outcome", values="Count",
                         color_discrete_sequence=["#00d4aa","#f59e0b","#8ca0b8","#1a7fd4"],
                         hole=0.55)
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                              plot_bgcolor="rgba(0,0,0,0)",
                              font=dict(color="#8ca0b8"), height=280,
                              margin=dict(t=10,b=10,l=10,r=10))
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Recent Activity")
    df_recent = get_leads(10)
    if not df_recent.empty:
        for _, row in df_recent.iterrows():
            st.markdown(f"""
            <div class="lead-row">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                  <strong style="color:#fff;">{row.get('name','—')}</strong>
                  <span style="color:#8ca0b8;font-size:12px;margin-left:8px;">{row.get('phone','—')}</span>
                </div>
                <div>
                  <span style="background:#1a7fd420;color:#60b8f0;padding:2px 10px;
                               border-radius:20px;font-size:11px;margin-right:4px;">
                    {row.get('urgency','—')}
                  </span>
                  <span style="background:#00d4aa20;color:#00d4aa;padding:2px 10px;
                               border-radius:20px;font-size:11px;">
                    {row.get('outcome','pending')}
                  </span>
                </div>
              </div>
              <div style="color:#8ca0b8;font-size:12px;margin-top:4px;">
                {row.get('service_type','—')}
              </div>
            </div>""", unsafe_allow_html=True)
    else:
        st.info("No leads yet. Inject one below or connect Twilio.")


# ── LEADS ─────────────────────────────────────────────────────────────────────
elif page == "Leads":
    st.title("Lead Database")
    df = get_leads(500)
    if df.empty:
        st.info("No leads in database.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1: search = st.text_input("Search name/phone", "")
        with c2: outcome_f = st.selectbox("Outcome", ["All","booked","escalated","disqualified"])
        with c3: urgency_f = st.selectbox("Urgency", ["All","emergency","urgent","routine"])

        if search:
            df = df[df["name"].str.contains(search, case=False, na=False) |
                    df["phone"].str.contains(search, case=False, na=False)]
        if outcome_f != "All": df = df[df["outcome"] == outcome_f]
        if urgency_f != "All": df = df[df["urgency"] == urgency_f]

        st.caption(f"{len(df)} records")
        st.dataframe(df[["name","phone","email","service_type","urgency","outcome",
                          "followup_count","created_at"]], use_container_width=True, height=520)

    df_fu = query("SELECT lead_id, followup_num, channel, tone_used, message, sent_at "
                  "FROM followup_log ORDER BY sent_at DESC LIMIT 100")
    st.subheader("Follow-up Log")
    if not df_fu.empty:
        st.dataframe(df_fu, use_container_width=True, height=280)
    else:
        st.info("No follow-ups logged yet.")


# ── VOICE CALLS ───────────────────────────────────────────────────────────────
elif page == "Voice Calls":
    st.title("Voice AI - Vapi.ai Call Log")
    df_calls = query("SELECT call_id, lead_name, phone, direction, duration_sec, "
                     "outcome, transcript_preview, created_at FROM voice_calls "
                     "ORDER BY created_at DESC LIMIT 50")
    if df_calls.empty:
        st.info("No voice calls logged yet. Configure Vapi.ai webhook to start.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1: st.metric("Total Calls", len(df_calls))
        with c2: st.metric("Avg Duration", f"{int(df_calls['duration_sec'].mean())}s")
        with c3: st.metric("Booked", (df_calls["outcome"] == "booked").sum())
        st.dataframe(df_calls, use_container_width=True, height=400)


# ── SYSTEM STATUS ─────────────────────────────────────────────────────────────
elif page == "System Status":
    st.title("Integration Status")
    status = check_status()
    col_a, col_b = st.columns(2)
    items = list(status.items())
    for col, section in [(col_a, items[:4]), (col_b, items[4:])]:
        with col:
            for service, ok in section:
                color = "#00d4aa" if ok else "#ff6b35"
                label = "Connected" if ok else "Missing env var"
                st.markdown(f"""
                <div class="lead-row">
                  <div style="display:flex;justify-content:space-between;align-items:center;">
                    <strong style="color:#fff;">{service}</strong>
                    <span style="color:{color};font-size:12px;">{label}</span>
                  </div>
                </div>""", unsafe_allow_html=True)

    st.subheader("API Endpoints")
    st.code("""
GET http://localhost:8000/health
POST http://localhost:8000/twilio/inbound
POST http://localhost:8000/twilio/missed-call
POST http://localhost:8000/twilio/sms-reply
POST http://localhost:8000/booking/calendly-webhook
POST http://localhost:8000/lead/web-form
POST http://localhost:8000/lead/sms-inbound
POST http://localhost:8000/lead/manual
POST http://localhost:8000/vapi/webhook
POST http://localhost:8000/vapi/outbound
    """)


# ── INJECT LEAD ───────────────────────────────────────────────────────────────
elif page == "Inject Lead":
    st.title("Inject a Test Lead")
    st.caption("Push a lead directly into the agent pipeline without a real phone call.")

    with st.form("inject_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Full Name *", "John Smith")
            phone = st.text_input("Phone *", "+15165550123")
            email = st.text_input("Email", "john@example.com")
            address = st.text_input("Address", "123 Main St, Hempstead NY")
        with c2:
            service = st.selectbox("Service Type", [
                "AC not cooling", "Heater not working", "Emergency HVAC repair",
                "Annual maintenance", "Duct cleaning", "New installation",
            ])
            urgency = st.selectbox("Urgency", ["routine", "urgent", "emergency"])
            budget = st.text_input("Budget", "$200-500")
            message = st.text_area("Customer Message",
                                   "My AC stopped working and it is very hot inside.",
                                   height=80)
        submitted = st.form_submit_button("Run Agent Pipeline")

    if submitted:
        if not name or not phone:
            st.error("Name and phone are required.")
        else:
            try:
                import sys
                sys.path.insert(0, ".")
                from langchain_core.messages import HumanMessage
                from agent.graph import build_graph

                lead_state = {
                    "messages": [HumanMessage(content=message)],
                    "lead_name": name,
                    "lead_phone": phone,
                    "lead_email": email,
                    "lead_address": address,
                    "lead_service_type": service,
                    "lead_urgency": urgency,
                    "lead_budget": budget,
                    "followup_count": 0,
                    "followup_max": 3,
                    "booking_confirmed": False,
                    "source": "dashboard_inject",
                    "outcome": "",
                    "error": "",
                }
                with st.spinner("Running agent pipeline..."):
                    graph = build_graph()
                    result = graph.invoke(lead_state)

                st.success(
                    f"Pipeline complete | "
                    f"Outcome: **{result.get('outcome', 'unknown')}** | "
                    f"Qualified: **{result.get('is_qualified', False)}**"
                )
                with st.expander("Full state"):
                    st.json({k: v for k, v in result.items() if k != "messages"})
            except Exception as e:
                st.error(f"Agent error: {e}")