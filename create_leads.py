from dotenv import load_dotenv
load_dotenv('D:/agents/projects/AAA-HVAC-AI/.env')
from db.postgres import get_conn
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                client_id INTEGER REFERENCES clients(id),
                name VARCHAR(255),
                phone VARCHAR(50),
                email VARCHAR(255),
                status VARCHAR(50) DEFAULT 'new',
                source VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')
    conn.commit()
print('Table leads created!')
