with open('api/main.py', 'a') as f:
    f.write("""

@app.get("/leads")
async def get_leads():
    try:
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, phone, status, created_at, name FROM leads ORDER BY created_at DESC")
                rows = cursor.fetchall()
                return {"leads": [{"id":r[0],"phone":r[1],"status":r[2],"created_at":str(r[3]),"name":r[4]} for r in rows]}
    except Exception as e:
        return {"leads":[], "error":str(e)}
""")
print("Done!")