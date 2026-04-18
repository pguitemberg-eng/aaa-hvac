import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


@contextmanager
def get_conn(row_factory=None):
    conn = psycopg.connect(get_database_url(), row_factory=row_factory)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_dict_conn():
    with get_conn(row_factory=dict_row) as conn:
        yield conn
