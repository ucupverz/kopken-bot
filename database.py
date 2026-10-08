import sqlite3
import os
import time
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "kopken.db")

# Auto-create folder kalau belum ada
_db_dir = os.path.dirname(DB_PATH)
if _db_dir and not os.path.exists(_db_dir):
    try:
        os.makedirs(_db_dir, exist_ok=True)
        print(f"✅ Folder database dibuat: {_db_dir}")
    except Exception as e:
        print(f"⚠️ Gagal bikin folder {_db_dir}: {e}, fallback ke ./kopken.db")
        DB_PATH = "kopken.db"


def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        # Tabel users
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                quota INTEGER DEFAULT 0,
                total_cek INTEGER DEFAULT 0,
                created_at INTEGER,
                banned INTEGER DEFAULT 0
            )
        """)
        # Tabel VOC codes
        c.execute("""
            CREATE TABLE IF NOT EXISTS voc_codes (
                code TEXT PRIMARY KEY,
                quota INTEGER DEFAULT 3,
                used INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at INTEGER,
                used_by INTEGER,
                used_at INTEGER,
                active INTEGER DEFAULT 1
            )
        """)
        # Tabel transactions (riwayat cek)
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
        conn.commit()
    print(f"✅ Database initialized at: {DB_PATH}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ----------------------------------------------------------------------
# USER
# ----------------------------------------------------------------------

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
            INSERT OR IGNORE INTO users (user_id, username, first_name, quota, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (user_id, username, first_name, now))
        conn.commit()


def add_quota(user_id, amount):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET quota = quota + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()


def use_quota(user_id):
    """Kurangi kuota 1. Return True kalau berhasil, False kalau kuota habis."""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT quota FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row or row["quota"] <= 0:
            return False
        c.execute("UPDATE users SET quota = quota - 1, total_cek = total_cek + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        return True


def add_transaction(user_id, ttype, amount, status, note=""):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO transactions (user_id, type, amount, status, note, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, ttype, amount, status, note, int(time.time())))
        conn.commit()


def get_user_transactions(user_id, limit=10):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                  (user_id, limit))
        return c.fetchall()


# ----------------------------------------------------------------------
# VOC CODES
# ----------------------------------------------------------------------

def create_voc(code, quota, created_by):
    """Buat kode VOC baru. Return True kalau berhasil, False kalau kode sudah ada."""
    now = int(time.time())
    with get_conn() as conn:
        c = conn.cursor()
        try:
            c.execute("""
                INSERT INTO voc_codes (code, quota, used, created_by, created_at, active)
                VALUES (?, ?, 0, ?, ?, 1)
            """, (code, quota, created_by, now))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # kode sudah ada


def get_voc(code):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM voc_codes WHERE code = ?", (code,))
        return c.fetchone()


def use_voc(code, user_id):
    """Pakai kode VOC: cek valid, kunci ke user_id, kasih kuota ke user.
    Return (success, message, quota_amount)."""
    now = int(time.time())
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM voc_codes WHERE code = ?", (code,))
        row = c.fetchone()
        if not row:
            return False, "Kode tidak ditemukan.", 0
        if not row["active"]:
            return False, "Kode sudah tidak aktif.", 0
        if row["used_by"] is not None and row["used_by"] != user_id:
            return False, "Kode sudah dipakai user lain.", 0
        if row["used_by"] == user_id:
            return False, "Kode ini sudah kamu pakai.", 0
        # Kunci kode ke user dan kasih kuota
        c.execute("""
            UPDATE voc_codes SET used_by = ?, used_at = ?, active = 0 WHERE code = ?
        """, (user_id, now, code))
        c.execute("UPDATE users SET quota = quota + ? WHERE user_id = ?",
                  (row["quota"], user_id))
        conn.commit()
        return True, f"Kode berhasil dipakai! Kuota +{row['quota']}x cek.", row["quota"]


def list_voc_codes(limit=20):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM voc_codes ORDER BY created_at DESC LIMIT ?", (limit,))
        return c.fetchall()


def revoke_voc(code):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE voc_codes SET active = 0 WHERE code = ?", (code,))
        conn.commit()


def get_stats():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as total FROM users")
        total = c.fetchone()["total"]
        c.execute("SELECT COUNT(*) as n FROM transactions WHERE type = 'cek'")
        total_cek = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) as n FROM voc_codes WHERE active = 1")
        active_voc = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) as n FROM voc_codes")
        total_voc = c.fetchone()["n"]
        return {
            "total_users": total,
            "total_cek": total_cek,
            "active_voc": active_voc,
            "total_voc": total_voc
        }


def get_all_user_ids():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE banned = 0")
        return [r["user_id"] for r in c.fetchall()]