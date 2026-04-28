from db.postgres import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT username, password_hash FROM clients")
        rows = cur.fetchall()
        for row in rows:
            print(f"Username: {row[0]} | Password: {row[1]}")
            Set-Content -Path "check_pw.py" -Encoding UTF8 -Value "from dotenv import load_dotenv
load_dotenv()
from db.postgres import get_conn
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('SELECT username, password_hash FROM clients')
        print(cur.fetchall())"