from dotenv import load_dotenv
load_dotenv('D:/agents/projects/AAA-HVAC-AI/.env')
from db.postgres import get_conn
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('ALTER TABLE clients ADD COLUMN IF NOT EXISTS phone_number VARCHAR(50)')
    conn.commit()
print('Done!')
