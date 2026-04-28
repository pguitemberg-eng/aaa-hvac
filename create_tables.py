from dotenv import load_dotenv
load_dotenv('D:/agents/projects/AAA-HVAC-AI/.env')
from db.postgres import get_conn
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS appointments (
                id SERIAL PRIMARY KEY,
                client_id INTEGER REFERENCES clients(id),
                name VARCHAR(255),
                address VARCHAR(500),
                appointment_time VARCHAR(50),
                appointment_date VARCHAR(50),
                service_type VARCHAR(100),
                status VARCHAR(50) DEFAULT 'scheduled',
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS voice_calls (
                id SERIAL PRIMARY KEY,
                client_id INTEGER REFERENCES clients(id),
                caller_name VARCHAR(255),
                phone VARCHAR(50),
                call_type VARCHAR(20),
                duration VARCHAR(20),
                status VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')
    conn.commit()
print('Tables created!')
