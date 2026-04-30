from dotenv import load_dotenv
load_dotenv('D:/agents/projects/AAA-HVAC-AI/.env')
from db.postgres import get_conn
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO leads (client_id, name, phone, email, status, source) VALUES (%s, %s, %s, %s, %s, %s)''', (1, 'Test Customer', '+15165550001', 'test@gmail.com', 'new', 'Voice AI'))
    conn.commit()
print('Done!')
