import psycopg2, hashlib
from dotenv import load_dotenv
import os
load_dotenv()
new_pass = "midvio123"
hashed = hashlib.sha256(new_pass.encode()).hexdigest()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
with conn.cursor() as cur:
    cur.execute("UPDATE clients SET password_hash=%s WHERE username='abc-cooling'", (hashed,))
    conn.commit()
    print("Password reset a: midvio123")
conn.close()
