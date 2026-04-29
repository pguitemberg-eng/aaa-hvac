from dotenv import load_dotenv
load_dotenv('D:/agents/projects/AAA-HVAC-AI/.env')
from db.postgres import get_conn
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('UPDATE clients SET phone_number = %s WHERE username = %s', ('+16312065719', 'abc-cooling'))
    conn.commit()
print('Done!')
