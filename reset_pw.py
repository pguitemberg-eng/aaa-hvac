from dotenv import load_dotenv
load_dotenv('D:/agents/projects/AAA-HVAC-AI/.env')
import hashlib
from db.postgres import get_conn
new_hash = hashlib.sha256('abc123'.encode()).hexdigest()
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('UPDATE clients SET password_hash = %s WHERE username = %s', (new_hash, 'abc-cooling'))
    conn.commit()
print('Password reset done!')
