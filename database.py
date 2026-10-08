import sqlite3
import os
import time
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "kopken.db")

def init_db():
    """Inisialisasi tabel."""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                total_topup INTEGER DEFAULT 0,
                total_cek INTEGER DEFAULT 0,
                created_at INTEGER,
                banned INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount INTEGER,
                status TEXT,
                note TEXT,
                timestamp INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_topups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                photo_file_id TEXT,
                status TEXT DEFAULT 'pending',
                timestamp INTEGER,
                approved_at INTEGER,
                approved_by INTEGER
            )
        """)
        conn.commit()

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def get_user(user_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return c.fetchone()

def create_user(user_id, username, first_name):
    now = int(time.time())
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, balance, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (user_id, username, first_name, now))
        conn.commit()

def update_balance(user_id, delta):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
        conn.commit()

def add_topup(user_id, amount):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + ?, total_topup = total_topup + ? WHERE user_id = ?",
                  (amount, amount, user_id))
        conn.commit()

def add_cek_count(user_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET total_cek = total_cek + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def add_transaction(user_id, ttype, amount, status, note=""):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO transactions (user_id, type, amount, status, note, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, ttype, amount, status, note, int(time.time())))
        conn.commit()

def create_pending_topup(user_id, amount, photo_file_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO pending_topups (user_id, amount, photo_file_id, status, timestamp)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user_id, amount, photo_file_id, int(time.time())))
        conn.commit()
        return c.lastrowid

def get_pending_topups():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM pending_topups WHERE status = 'pending' ORDER BY timestamp ASC")
        return c.fetchall()

def approve_topup(topup_id, admin_id):
    now = int(time.time())
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM pending_topups WHERE id = ?", (topup_id,))
        row = c.fetchone()
        if not row or row["status"] != "pending":
            return None
        c.execute("UPDATE pending_topups SET status = 'approved', approved_at = ?, approved_by = ? WHERE id = ?",
                  (now, admin_id, topup_id))
        conn.commit()
        return row

def reject_topup(topup_id, admin_id, reason=""):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE pending_topups SET status = 'rejected', approved_at = ?, approved_by = ? WHERE id = ?",
                  (int(time.time()), admin_id, topup_id))
        conn.commit()

def get_user_transactions(user_id, limit=10):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                  (user_id, limit))
        return c.fetchall()

def get_all_user_ids():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE banned = 0")
        return [r["user_id"] for r in c.fetchall()]

def get_stats():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as total FROM users")
        total = c.fetchone()["total"]
        c.execute("SELECT COALESCE(SUM(balance), 0) as s FROM users")
        total_balance = c.fetchone()["s"]
        c.execute("SELECT COALESCE(SUM(amount), 0) as s FROM transactions WHERE type = 'topup' AND status = 'approved'")
        total_topup = c.fetchone()["s"]
        c.execute("SELECT COUNT(*) as n FROM transactions WHERE type = 'cek'")
        total_cek = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) as n FROM pending_topups WHERE status = 'pending'")
        pending = c.fetchone()["n"]
        return {
            "total_users": total,
            "total_balance": total_balance,
            "total_topup": total_topup,
            "total_cek": total_cek,
            "pending": pending
        }