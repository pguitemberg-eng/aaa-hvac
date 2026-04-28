with open('api/main.py', 'a') as f:
    f.write("""

@app.post("/clients")
async def create_client(data: dict):
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO clients (company_name, username, password_hash, phone_number, active) VALUES (%s, %s, %s, %s, %s)",
                    (data['company_name'], data['username'], data['password'], data.get('phone_number',''), True)
                )
                conn.commit()
                return {"ok": True}
    except Exception as e:
        return {"ok": False, "detail": str(e)}

@app.get("/clients")
async def get_clients():
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, company_name, username, phone_number, active FROM clients ORDER BY company_name")
                rows = cursor.fetchall()
                return {"clients": [{"id":r[0],"company_name":r[1],"username":r[2],"phone_number":r[3],"active":r[4]} for r in rows]}
    except Exception as e:
        return {"clients": [], "error": str(e)}
""")
print("Done!")