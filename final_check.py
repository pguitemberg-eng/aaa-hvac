import psycopg2, hashlib
from dotenv import load_dotenv
import os
load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
with conn.cursor() as cur:
    hashed = hashlib.sha256('midvio123'.encode()).hexdigest()
    cur.execute("SELECT id, company_name, username FROM clients WHERE username='abc-cooling' AND password_hash=%s", (hashed,))
    print("LOGIN RESULT:", cur.fetchone())
    cur.execute("SELECT id, client_id, name FROM leads")
    print("ALL LEADS:", cur.fetchall())
conn.close()
