import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

conn = psycopg2.connect(os.getenv("DATABASE_URL"))

with conn.cursor() as cur:
    cur.execute('''
        INSERT INTO leads (client_id, name, phone, email, status, source)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (1, 'Test Customer', '+15165550001', 'test@gmail.com', 'new', 'Voice AI'))
    conn.commit()
    print("Done! Lead insere nan Neon database.")

conn.close()
