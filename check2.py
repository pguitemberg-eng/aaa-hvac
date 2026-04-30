import psycopg2
from dotenv import load_dotenv
import os
load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
with conn.cursor() as cur:
    cur.execute("SELECT id, username FROM clients")
    print("CLIENTS:", cur.fetchall())
    cur.execute("SELECT id, client_id, name FROM leads")
    print("LEADS:", cur.fetchall())
conn.close()
