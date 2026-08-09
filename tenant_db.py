import os
import sqlite3

BASE_DIR = os.path.dirname(__file__)


def tenant_dir(tenant):
    path = os.path.join(BASE_DIR, 'tenant_data', tenant)
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, 'paycheck_files'), exist_ok=True)
    return path


def db_path(tenant):
    return os.path.join(tenant_dir(tenant), 'data.db')


def paycheck_folder(tenant):
    return os.path.join(tenant_dir(tenant), 'paycheck_files')


def get_db(tenant):
    conn = sqlite3.connect(db_path(tenant))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(tenant):
    conn = sqlite3.connect(db_path(tenant))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submitted_at TEXT,
            region TEXT,
            org TEXT,
            addr TEXT,
            manager TEXT,
            tel TEXT,
            room_count TEXT,
            room_info TEXT,
            device TEXT,
            target TEXT,
            headcount TEXT,
            course TEXT,
            recruit TEXT,
            recruit_channel TEXT,
            period TEXT,
            day TEXT,
            time TEXT,
            edubus TEXT,
            edubus_addr TEXT,
            edubus_date TEXT,
            etc TEXT,
            confirmed INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS instructor_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submitted_at TEXT,
            name TEXT,
            tel TEXT,
            role TEXT,
            choices TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS paycheck_consents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            consented_at TEXT,
            name TEXT,
            birth TEXT,
            tel_last4 TEXT,
            target_month TEXT,
            total_pay INTEGER,
            ip_address TEXT,
            snapshot_json TEXT,
            bank_name TEXT,
            account_number TEXT,
            insurance_type TEXT
        )
    ''')
    conn.commit()
    conn.close()
