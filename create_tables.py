import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
with conn.cursor() as cur:
    cur.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id SERIAL PRIMARY KEY,
            client_id INTEGER,
            name VARCHAR(255),
            phone VARCHAR(50),
            email VARCHAR(255),
            status VARCHAR(50) DEFAULT 'new',
            source VARCHAR(100),
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            company_name VARCHAR(255),
            username VARCHAR(100) UNIQUE,
            password_hash VARCHAR(255),
            phone_number VARCHAR(50),
            active BOOLEAN DEFAULT TRUE
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            client_id INTEGER,
            lead_name VARCHAR(255),
            phone VARCHAR(50),
            service_type VARCHAR(100),
            scheduled_at TIMESTAMP,
            status VARCHAR(50),
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS voice_calls (
            id SERIAL PRIMARY KEY,
            client_id INTEGER,
            caller_name VARCHAR(255),
            phone VARCHAR(50),
            call_type VARCHAR(50),
            duration VARCHAR(20),
            status VARCHAR(50),
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    conn.commit()
    print("Tout tables kreye!")
conn.close()
