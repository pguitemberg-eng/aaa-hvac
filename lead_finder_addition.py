# ── Lead Finder Page ──────────────────────────────────────────────────────────

def render_lead_finder_page():
    st.title("🔍 Lead Finder")
    st.caption("Find HVAC businesses in any zip code and add them to your outreach pipeline.")

    tab1, tab2 = st.tabs(["🔎 Search Businesses", "📋 My Prospects"])

    with tab1:
        st.subheader("Search HVAC Businesses by Zip Code")
        col1, col2, col3 = st.columns(3)
        with col1:
            zip_code = st.text_input("Zip Code", placeholder="e.g. 11501")
        with col2:
            radius = st.selectbox("Radius", ["5 miles", "10 miles", "25 miles"])
        with col3:
            max_results = st.selectbox("Max Results", [10, 20, 50])

        search_clicked = st.button("🔍 Search", type="primary")

        if search_clicked and zip_code:
            api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
            if not api_key:
                st.warning("⚠️ Google Places API key missing.")
                st.info("**Demo mode** — showing sample data")
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
                    st.success("✅ Updated.")
                    st.rerun()

        csv = filtered_p.to_csv(index=False)
        st.download_button("📥 Export CSV", csv, "prospects.csv", "text/csv")


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
        pain = "🔥 High" if rating < 4.0 else "🟡 Medium" if rating < 4.5 else "🟢 Low"
        with st.expander(f"**{biz['name']}** — ⭐{rating} ({reviews} reviews) — Opportunity: {pain}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"📍 {biz['address']}")
                st.write(f"📞 {biz['phone'] or 'N/A'}")
                st.write(f"🌐 {biz['website'] or 'N/A'}")
            with col2:
                st.write(f"⭐ {rating}/5 — {reviews} reviews")
                if rating < 4.0:
                    st.markdown("🔥 **Good prospect!**")
            if st.button("➕ Add to Prospects", key=f"add_{i}"):
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
        st.success(f"✅ {biz['name']} added!")
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