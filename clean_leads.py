import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
with conn.cursor() as cur:
    cur.execute("DELETE FROM leads WHERE name = 'Test Customer'")
    conn.commit()
    print("Test leads efase!")
conn.close()
