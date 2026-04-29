from dotenv import load_dotenv
load_dotenv('D:/agents/projects/AAA-HVAC-AI/.env')
from db.postgres import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'appointments' ORDER BY ordinal_position")
        print('APPOINTMENTS:', [r[0] for r in cur.fetchall()])
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'leads' ORDER BY ordinal_position")
        print('LEADS:', [r[0] for r in cur.fetchall()])