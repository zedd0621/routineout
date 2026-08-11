import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), 'leads.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submitted_at TEXT,
            name TEXT,
            phone TEXT,
            message TEXT
        )
    ''')
    conn.commit()
    conn.close()
