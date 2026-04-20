"""AAA HVAC multi-client Streamlit dashboard.

Run: streamlit run dashboard.py
"""

import hashlib
import hmac
import os
import sys

import bcrypt
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from agent.graph import build_graph
from db.postgres import get_conn as pg_get_conn

load_dotenv()

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "HVAC Pro")
ADMIN_USER = os.getenv("DASHBOARD_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("DASHBOARD_ADMIN_PASS", "")

# -- Dynamic base URL (Railway vs local) --
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
BASE_URL = f"https://{RAILWAY_URL}" if RAILWAY_URL else "http://localhost:8000"

st.set_page_config(
    page_title=f"{BUSINESS_NAME} - AI Command Center",
    page_icon="❄",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
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
  .status-ok  { color: #00d4aa; font-weight: 600; }
  .status-bad { color: #f87171; font-weight: 600; }
</style>
""",
    unsafe_allow_html=True,
)


# -- Password helpers (bcrypt) --

def hash_password(password: str) -> str:
    """Return bcrypt hash. Falls back to SHA-256 for legacy rows."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify against bcrypt hash; fall back to legacy SHA-256 comparison."""
    try:
        if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
            return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy, stored_hash)
    except Exception:
        return False


# -- DB helpers --

def query_df(sql: str, params=()) -> pd.DataFrame:
    try:
        with pg_get_conn() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description] if cur.description else []
            return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    except Exception:
        return pd.DataFrame()


def execute(sql: str, params=()) -> bool:
    try:
        with pg_get_conn() as conn:
            conn.execute(sql, params)
            conn.commit()
        return True
    except Exception:
        return False


# -- Schema - cached so it only runs ONCE per app startup --

@st.cache_resource(show_spinner=False)
def ensure_schema():
    execute(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            company_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            phone_number TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS client_id INTEGER")
    execute("ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS client_id INTEGER")

    for sql in [
        """
        ALTER TABLE leads
        ADD CONSTRAINT leads_client_id_fkey
        FOREIGN KEY (client_id) REFERENCES clients(id)
        """,
        """
        ALTER TABLE voice_calls
        ADD CONSTRAINT voice_calls_client_id_fkey
        FOREIGN KEY (client_id) REFERENCES clients(id)
        """,
    ]:
        try:
            with pg_get_conn() as conn:
                conn.execute(sql)
                conn.commit()
        except Exception:
            pass

    execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            client_id INTEGER REFERENCES clients(id),
            lead_name TEXT,
            phone TEXT,
            email TEXT,
            service_type TEXT,
            scheduled_at TIMESTAMPTZ,
            calendly_event_uri TEXT,
            status TEXT DEFAULT 'scheduled',
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


# -- Scope helpers --

def get_client_scope_clause():
    user = st.session_state.get("auth_user")
    if not user:
        return "1=0", ()
    if user["role"] == "admin":
        return "1=1", ()
    return "client_id = %s", (user["client_id"],)


def get_leads(limit=200):
    where_sql, where_params = get_client_scope_clause()
    return query_df(
        f"SELECT id, client_id, name, phone, email, address, service_type, urgency, outcome, "
        f"booking_url, followup_count, created_at, updated_at "
        f"FROM leads WHERE {where_sql} ORDER BY created_at DESC LIMIT %s",
        (*where_params, limit),
    )


def get_voice_calls(limit=100):
    where_sql, where_params = get_client_scope_clause()
    return query_df(
        f"SELECT id, client_id, call_id, lead_name, phone, direction, duration_sec, outcome, "
        f"transcript_preview, created_at FROM voice_calls "
        f"WHERE {where_sql} ORDER BY created_at DESC LIMIT %s",
        (*where_params, limit),
    )


def get_appointments(limit=200):
    where_sql, where_params = get_client_scope_clause()
    return query_df(
        f"SELECT id, client_id, lead_name, phone, email, service_type, scheduled_at, "
        f"status, notes, calendly_event_uri, created_at "
        f"FROM appointments WHERE {where_sql} ORDER BY scheduled_at DESC LIMIT %s",
        (*where_params, limit),
    )


def get_stats():
    df = get_leads(1000)
    if df.empty:
        return {"total": 0, "booked": 0, "escalated": 0, "disqualified": 0, "rate": 0.0, "emergency": 0}
    total = len(df)
    booked = int((df["outcome"] == "booked").sum())
    escalated = int((df["outcome"] == "escalated").sum())
    disqualified = int((df["outcome"] == "disqualified").sum())
    emergency = int((df["urgency"] == "emergency").sum())
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


# -- Auth --

def authenticate(username: str, password: str):
    if username == ADMIN_USER and ADMIN_PASS and hmac.compare_digest(password, ADMIN_PASS):
        return {"role": "admin", "username": username,
                "company_name": "Platform Admin", "client_id": None}

    df = query_df(
        "SELECT id, company_name, username, password_hash, active FROM clients WHERE username = %s LIMIT 1",
        (username,),
    )
    if df.empty:
        return None

    row = df.iloc[0]
    if not row["active"]:
        return None

    if verify_password(password, str(row["password_hash"])):
        return {
            "role": "client",
            "username": row["username"],
            "company_name": row["company_name"],
            "client_id": int(row["id"]),
        }
    return None


# -- Pages --

def render_login():
    st.title(f"{BUSINESS_NAME} Dashboard")
    st.caption("Sign in as admin or a client user.")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
    if submitted:
        auth_user = authenticate(username.strip(), password)
        if auth_user:
            st.session_state["auth_user"] = auth_user
            st.success("Login successful.")
            st.rerun()
        else:
            st.error("Invalid username/password or inactive account.")


def render_manage_clients_page():
    st.title("Manage Clients")
    with st.form("create_client"):
        st.subheader("Create New Client")
        c1, c2 = st.columns(2)
        with c1:
            company_name = st.text_input("Company Name *")
            username = st.text_input("Username *")
            phone_number = st.text_input("Phone Number")
        with c2:
            password = st.text_input("Password *", type="password")
            active = st.checkbox("Active", value=True)
        created = st.form_submit_button("Create Client")

    if created:
        if not company_name or not username or not password:
            st.error("Company name, username, and password are required.")
        else:
            ok = execute(
                "INSERT INTO clients (company_name, username, password_hash, phone_number, active) VALUES (%s,%s,%s,%s,%s)",
                (company_name.strip(), username.strip(), hash_password(password), phone_number.strip(), active),
            )
            if ok:
                st.success("Client created.")
                st.rerun()
            else:
                st.error("Could not create client (username may already exist).")

    st.subheader("Existing Clients")
    clients = query_df(
        "SELECT id, company_name, username, phone_number, active FROM clients ORDER BY company_name"
    )
    if clients.empty:
        st.info("No clients yet.")
        return

    st.dataframe(clients, use_container_width=True, height=300)
    selection = st.selectbox(
        "Select client to update", clients["id"].tolist(),
        format_func=lambda cid: f"{cid} - {clients[clients['id'] == cid].iloc[0]['company_name']}"
    )
    selected = clients[clients["id"] == selection].iloc[0]
    new_status = st.checkbox("Client Active", value=bool(selected["active"]))
    if st.button("Save Client Status"):
        ok = execute("UPDATE clients SET active = %s WHERE id = %s", (new_status, int(selection)))
        if ok:
            st.success("Client status updated.")
            st.rerun()
        else:
            st.error("Failed to update client.")


def render_pipeline_page():
    st.title("Live Pipeline")
    stats = get_stats()

    c1, c2, c3, c4, c5 = st.columns(5)
    for col, label, val, sub in [
        (c1, "Total Leads", stats["total"], "all time"),
        (c2, "Booked", stats["booked"], f"{stats['rate']}% rate"),
        (c3, "Escalated", stats["escalated"], "needs call"),
        (c4, "Disqualified", stats["disqualified"], "not a fit"),
        (c5, "Emergency", stats["emergency"], "high urgency"),
    ]:
        with col:
            st.markdown(
                f'<div class="metric-card"><div class="metric-label">{label}</div>'
                f'<div class="metric-value">{val}</div><div class="metric-sub">{sub}</div></div>',
                unsafe_allow_html=True,
            )

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Conversion Funnel")
        total = max(stats["total"], 1)
        for label, n in [
            ("Leads Received", stats["total"]),
            ("Qualified", stats["booked"] + stats["escalated"]),
            ("Booking Link Sent", stats["booked"] + stats["escalated"] // 2),
            ("Confirmed Booked", stats["booked"]),
        ]:
            pct = int(n / total * 100) if total else 0
            st.progress(min(max(pct, 0), 100), text=f"{label}: {n} ({pct}%)")

    with col_right:
        st.subheader("Outcomes")
        df = get_leads()
        if not df.empty and "outcome" in df.columns:
            counts = df["outcome"].value_counts().reset_index()
            counts.columns = ["Outcome", "Count"]
            fig = px.pie(
                counts, names="Outcome", values="Count",
                color_discrete_sequence=["#00d4aa", "#f59e0b", "#8ca0b8", "#1a7fd4"],
                hole=0.55,
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#8ca0b8"), height=280,
                margin=dict(t=10, b=10, l=10, r=10),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No outcome data yet.")


def render_leads_page():
    st.title("Lead Database")
    df = get_leads(500)
    if df.empty:
        st.info("No leads in database.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        search = st.text_input("Search name/phone", "")
    with c2:
        outcome_f = st.selectbox("Outcome", ["All", "booked", "escalated", "disqualified"])
    with c3:
        urgency_f = st.selectbox("Urgency", ["All", "emergency", "urgent", "routine"])

    if search:
        df = df[
            df["name"].str.contains(search, case=False, na=False) |
            df["phone"].str.contains(search, case=False, na=False)
        ]
    if outcome_f != "All":
        df = df[df["outcome"] == outcome_f]
    if urgency_f != "All":
        df = df[df["urgency"] == urgency_f]

    show_cols = ["name", "phone", "email", "service_type", "urgency", "outcome", "followup_count", "created_at"]
    if st.session_state["auth_user"]["role"] == "admin":
        show_cols = ["client_id"] + show_cols

    st.caption(f"{len(df)} records")
    st.dataframe(df[show_cols], use_container_width=True, height=520)

    df_fu = query_df(
        "SELECT lead_id, followup_num, channel, tone_used, message, sent_at FROM followup_log ORDER BY sent_at DESC LIMIT 100"
    )
    st.subheader("Follow-up Log")
    if not df_fu.empty:
        st.dataframe(df_fu, use_container_width=True, height=280)
    else:
        st.info("No follow-ups logged yet.")


def render_calendar_page():
    st.title("Appointments & Calendar")

    df = get_appointments(200)

    c1, c2, c3, c4 = st.columns(4)
    total_appts = len(df)
    scheduled = int((df["status"] == "scheduled").sum()) if not df.empty else 0
    completed = int((df["status"] == "completed").sum()) if not df.empty else 0
    cancelled = int((df["status"] == "cancelled").sum()) if not df.empty else 0

    for col, label, val, color in [
        (c1, "Total Appointments", total_appts, "#1a7fd4"),
        (c2, "Scheduled", scheduled, "#00d4aa"),
        (c3, "Completed", completed, "#f59e0b"),
        (c4, "Cancelled", cancelled, "#f87171"),
    ]:
        with col:
            st.markdown(
                f'<div class="metric-card"><div class="metric-label">{label}</div>'
                f'<div class="metric-value" style="color:{color}">{val}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    if df.empty:
        st.info("No appointments yet. They will appear here once leads book via Calendly.")
        return

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        search = st.text_input("Search name/phone", "")
    with col_f2:
        status_f = st.selectbox("Status", ["All", "scheduled", "completed", "cancelled", "no_show"])
    with col_f3:
        service_f = st.selectbox("Service Type", ["All"] + sorted(df["service_type"].dropna().unique().tolist()))

    filtered = df.copy()
    if search:
        filtered = filtered[
            filtered["lead_name"].str.contains(search, case=False, na=False) |
            filtered["phone"].str.contains(search, case=False, na=False)
        ]
    if status_f != "All":
        filtered = filtered[filtered["status"] == status_f]
    if service_f != "All":
        filtered = filtered[filtered["service_type"] == service_f]

    st.caption(f"{len(filtered)} appointments")

    show_cols = ["lead_name", "phone", "email", "service_type", "scheduled_at", "status", "notes"]
    if st.session_state["auth_user"]["role"] == "admin":
        show_cols = ["client_id"] + show_cols

    st.dataframe(filtered[show_cols], use_container_width=True, height=460)

    st.markdown("---")
    st.subheader("Update Appointment Status")
    if not filtered.empty:
        appt_options = filtered["id"].tolist()
        selected_id = st.selectbox(
            "Select Appointment",
            appt_options,
            format_func=lambda i: f"#{i} - {filtered[filtered['id'] == i].iloc[0]['lead_name']} ({filtered[filtered['id'] == i].iloc[0]['scheduled_at']})"
        )
        new_status = st.selectbox("New Status", ["scheduled", "completed", "cancelled", "no_show"])
        notes_input = st.text_area("Notes (optional)")
        if st.button("Update Appointment"):
            ok = execute(
                "UPDATE appointments SET status = %s, notes = %s WHERE id = %s",
                (new_status, notes_input, int(selected_id))
            )
            if ok:
                st.success("Appointment updated.")
                st.rerun()
            else:
                st.error("Failed to update appointment.")

    st.markdown("---")
    st.subheader("Upcoming This Week")
    upcoming = query_df(
        """
        SELECT lead_name, phone, service_type, scheduled_at, status
        FROM appointments
        WHERE scheduled_at BETWEEN NOW() AND NOW() + INTERVAL '7 days'
          AND status = 'scheduled'
        ORDER BY scheduled_at ASC
        LIMIT 20
        """
    )
    if upcoming.empty:
        st.info("No upcoming appointments this week.")
    else:
        st.dataframe(upcoming, use_container_width=True, height=280)


def render_voice_calls_page():
    st.title("Voice AI - Vapi.ai Call Log")
    df_calls = get_voice_calls(50)
    if df_calls.empty:
        st.info("No voice calls logged yet.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total Calls", len(df_calls))
    with c2:
        avg_dur = int(df_calls["duration_sec"].mean()) if "duration_sec" in df_calls.columns and not df_calls["duration_sec"].isna().all() else 0
        st.metric("Avg Duration", f"{avg_dur}s")
    with c3:
        st.metric("Booked", int((df_calls["outcome"] == "booked").sum()))

    st.dataframe(df_calls, use_container_width=True, height=420)


def render_system_status_page():
    st.title("Integration Status")

    st.subheader("Live Server URL")
    st.code(BASE_URL)

    status = check_status()
    col_a, col_b = st.columns(2)
    items = list(status.items())
    for col, section in [(col_a, items[:4]), (col_b, items[4:])]:
        with col:
            for service, ok in section:
                icon = "[OK]" if ok else "[X]"
                label = "OK" if ok else "MISSING"
                css = "status-ok" if ok else "status-bad"
                st.markdown(
                    f'{icon} <span class="{css}">{label}</span> - {service}',
                    unsafe_allow_html=True,
                )

    st.subheader("API Endpoints")
    st.code(
        f"""
GET  {BASE_URL}/health
POST {BASE_URL}/twilio/inbound
POST {BASE_URL}/twilio/missed-call
POST {BASE_URL}/twilio/sms-reply
POST {BASE_URL}/booking/calendly-webhook
POST {BASE_URL}/lead/web-form
POST {BASE_URL}/lead/sms-inbound
POST {BASE_URL}/lead/manual
POST {BASE_URL}/vapi/webhook
POST {BASE_URL}/vapi/outbound
    """
    )


def render_inject_lead_page():
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
                "Annual maintenance", "Duct cleaning", "New installation"
            ])
            urgency = st.selectbox("Urgency", ["routine", "urgent", "emergency"])
            budget = st.text_input("Budget", "$200-500")
            message = st.text_area("Customer Message", "My AC stopped working and it is very hot inside.", height=80)
        submitted = st.form_submit_button("Run Agent Pipeline")

    if submitted:
        if not name or not phone:
            st.error("Name and phone are required.")
            return

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

        if st.session_state["auth_user"]["role"] == "client":
            lead_state["client_id"] = st.session_state["auth_user"]["client_id"]

        try:
            sys.path.insert(0, ".")
            with st.spinner("Running agent pipeline..."):
                graph = build_graph()
                result = graph.invoke(lead_state)
            st.success(
                f"Pipeline complete | Outcome: **{result.get('outcome', 'unknown')}** | "
                f"Qualified: **{result.get('is_qualified', False)}**"
            )
            with st.expander("Full state"):
                st.json({k: v for k, v in result.items() if k != "messages"})
        except Exception as exc:
            st.error(f"Agent error: {exc}")


# -- Lead Finder Page --

def render_lead_finder_page():
    st.title("Lead Finder")
    st.caption("Find HVAC businesses in any zip code and add them to your outreach pipeline.")

    tab1, tab2 = st.tabs(["Search Businesses", "My Prospects"])

    with tab1:
        st.subheader("Search HVAC Businesses by Zip Code")
        col1, col2, col3 = st.columns(3)
        with col1:
            zip_code = st.text_input("Zip Code", placeholder="e.g. 11501")
        with col2:
            radius = st.selectbox("Radius", ["5 miles", "10 miles", "25 miles"])
        with col3:
            max_results = st.selectbox("Max Results", [10, 20, 50])

        search_clicked = st.button("Search", type="primary")

        if search_clicked and zip_code:
            api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
            if not api_key:
                st.warning("Google Places API key missing.")
                st.info("Demo mode - showing sample data")
                _show_demo_results(zip_code)
            else:
                with st.spinner(f"Searching near {zip_code}..."):
                    results = _search_hvac_businesses(zip_code, api_key, max_results)
                if results:
                    _display_search_results(results)
                else:
                    st.info("No results found.")
        elif search_clicked:
            st.error("Please enter a zip code.")

    with tab2:
        st.subheader("My Prospect List")
        prospects = query_df(
            "SELECT * FROM lead_finder_prospects ORDER BY created_at DESC LIMIT 200"
        )
        if prospects.empty:
            st.info("No prospects yet.")
            return

        c1, c2, c3, c4 = st.columns(4)
        total_p = len(prospects)
        contacted = int((prospects["status"] == "contacted").sum()) if "status" in prospects.columns else 0
        interested = int((prospects["status"] == "interested").sum()) if "status" in prospects.columns else 0
        closed = int((prospects["status"] == "closed").sum()) if "status" in prospects.columns else 0

        for col, label, val, color in [
            (c1, "Total", total_p, "#1a7fd4"),
            (c2, "Contacted", contacted, "#f59e0b"),
            (c3, "Interested", interested, "#00d4aa"),
            (c4, "Closed", closed, "#a855f7"),
        ]:
            with col:
                st.markdown(
                    f'<div class="metric-card"><div class="metric-label">{label}</div>'
                    f'<div class="metric-value" style="color:{color}">{val}</div></div>',
                    unsafe_allow_html=True,
                )

        st.markdown("---")
        status_filter = st.selectbox("Filter", ["All", "new", "contacted", "interested", "not_interested", "closed"])
        filtered_p = prospects if status_filter == "All" else prospects[prospects["status"] == status_filter]
        st.dataframe(
            filtered_p[["company_name", "phone", "address", "rating", "review_count", "status", "notes", "created_at"]],
            use_container_width=True,
            height=400
        )

        st.markdown("---")
        st.subheader("Update Prospect")
        if not filtered_p.empty:
            prospect_id = st.selectbox(
                "Select",
                filtered_p["id"].tolist(),
                format_func=lambda i: f"{filtered_p[filtered_p['id']==i].iloc[0]['company_name']}"
            )
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                new_status = st.selectbox("Status", ["new", "contacted", "interested", "not_interested", "closed"])
            with col_s2:
                notes = st.text_input("Notes")
            if st.button("Update"):
                ok = execute(
                    "UPDATE lead_finder_prospects SET status=%s, notes=%s WHERE id=%s",
                    (new_status, notes, int(prospect_id))
                )
                if ok:
                    st.success("Updated.")
                    st.rerun()

        csv = filtered_p.to_csv(index=False)
        st.download_button("Export CSV", csv, "prospects.csv", "text/csv")


def _search_hvac_businesses(zip_code: str, api_key: str, max_results: int) -> list:
    import urllib.request
    import json
    try:
        geo_url = f"https://maps.googleapis.com/maps/api/geocode/json?address={zip_code}&key={api_key}"
        with urllib.request.urlopen(geo_url) as resp:
            geo_data = json.loads(resp.read())
        if not geo_data.get("results"):
            return []
        location = geo_data["results"][0]["geometry"]["location"]
        lat, lng = location["lat"], location["lng"]
        places_url = (
            f"https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            f"?location={lat},{lng}&radius=16000&keyword=HVAC+contractor&key={api_key}"
        )
        with urllib.request.urlopen(places_url) as resp:
            places_data = json.loads(resp.read())
        results = []
        for place in places_data.get("results", [])[:max_results]:
            place_id = place.get("place_id", "")
            phone = ""
            website = ""
            if place_id:
                details_url = (
                    f"https://maps.googleapis.com/maps/api/place/details/json"
                    f"?place_id={place_id}&fields=formatted_phone_number,website&key={api_key}"
                )
                try:
                    with urllib.request.urlopen(details_url) as dresp:
                        details = json.loads(dresp.read())
                    phone = details.get("result", {}).get("formatted_phone_number", "")
                    website = details.get("result", {}).get("website", "")
                except Exception:
                    pass
            results.append({
                "name": place.get("name", ""),
                "address": place.get("vicinity", ""),
                "rating": place.get("rating", 0),
                "review_count": place.get("user_ratings_total", 0),
                "phone": phone,
                "website": website,
                "place_id": place_id,
            })
        return results
    except Exception as exc:
        st.error(f"Search error: {exc}")
        return []


def _display_search_results(results: list):
    st.success(f"Found {len(results)} HVAC businesses!")
    for i, biz in enumerate(results):
        rating = biz.get("rating", 0)
        reviews = biz.get("review_count", 0)
        pain = "High opportunity" if rating < 4.0 else "Medium opportunity" if rating < 4.5 else "Low opportunity"
        with st.expander(f"**{biz['name']}** - {rating}/5 ({reviews} reviews) - {pain}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"Address: {biz['address']}")
                st.write(f"Phone: {biz['phone'] or 'N/A'}")
                st.write(f"Website: {biz['website'] or 'N/A'}")
            with col2:
                st.write(f"Rating: {rating}/5 - {reviews} reviews")
                if rating < 4.0:
                    st.markdown("**Good prospect!**")
            if st.button("Add to Prospects", key=f"add_{i}"):
                _add_to_prospects(biz)


def _add_to_prospects(biz: dict):
    execute("""
        CREATE TABLE IF NOT EXISTS lead_finder_prospects (
            id SERIAL PRIMARY KEY,
            company_name TEXT,
            phone TEXT,
            address TEXT,
            rating FLOAT,
            review_count INTEGER,
            website TEXT,
            place_id TEXT UNIQUE,
            status TEXT DEFAULT 'new',
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    ok = execute(
        """
        INSERT INTO lead_finder_prospects
            (company_name, phone, address, rating, review_count, website, place_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (place_id) DO NOTHING
        """,
        (biz["name"], biz["phone"], biz["address"],
         biz["rating"], biz["review_count"],
         biz["website"], biz["place_id"])
    )
    if ok:
        st.success(f"{biz['name']} added!")
    else:
        st.info("Already in list.")


def _show_demo_results(zip_code: str):
    demo_data = [
        {"name": "Long Island HVAC Pro", "address": f"123 Main St, {zip_code}", "rating": 3.8, "review_count": 47, "phone": "516-555-0001", "website": "", "place_id": "demo1"},
        {"name": "Queens Air Systems", "address": f"456 Broadway, {zip_code}", "rating": 4.1, "review_count": 23, "phone": "718-555-0002", "website": "", "place_id": "demo2"},
        {"name": "NYC Cool Air LLC", "address": f"789 Park Ave, {zip_code}", "rating": 3.5, "review_count": 12, "phone": "212-555-0003", "website": "", "place_id": "demo3"},
        {"name": "Island Comfort HVAC", "address": f"321 Ocean Blvd, {zip_code}", "rating": 4.4, "review_count": 89, "phone": "631-555-0004", "website": "", "place_id": "demo4"},
        {"name": "Metro HVAC Services", "address": f"654 Queens Blvd, {zip_code}", "rating": 3.9, "review_count": 34, "phone": "347-555-0005", "website": "", "place_id": "demo5"},
    ]
    _display_search_results(demo_data)


# -- Main --

def main():
    ensure_schema()

    if "auth_user" not in st.session_state:
        st.session_state["auth_user"] = None

    if not st.session_state["auth_user"]:
        render_login()
        return

    auth_user = st.session_state["auth_user"]
    with st.sidebar:
        st.markdown(f"## {BUSINESS_NAME}")
        st.markdown(f"**Signed in:** `{auth_user['username']}` ({auth_user['role']})")
        if auth_user["role"] == "client":
            st.caption(auth_user["company_name"])

        pages = ["Pipeline", "Leads", "Appointments", "Voice Calls", "Lead Finder", "System Status", "Inject Lead"]
        if auth_user["role"] == "admin":
            pages.insert(0, "Manage Clients")

        page = st.radio("Navigation", pages)
        st.markdown("---")
        if st.button("Refresh"):
            st.rerun()
        if st.button("Logout"):
            st.session_state["auth_user"] = None
            st.rerun()

    dispatch = {
        "Manage Clients": render_manage_clients_page,
        "Pipeline": render_pipeline_page,
        "Leads": render_leads_page,
        "Appointments": render_calendar_page,
        "Voice Calls": render_voice_calls_page,
        "Lead Finder": render_lead_finder_page,
        "System Status": render_system_status_page,
        "Inject Lead": render_inject_lead_page,
    }
    dispatch.get(page, render_pipeline_page)()


if __name__ == "__main__":
    main()