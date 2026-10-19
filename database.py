import sqlite3
import time
from contextlib import contextmanager

DB_PATH = "bot.db"

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            saldo INTEGER DEFAULT 0,
            trial_kopken INTEGER DEFAULT 2,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT DEFAULT '',
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS otp_orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token TEXT,
            phone TEXT,
            service_name TEXT,
            price INTEGER,
            status TEXT DEFAULT 'waiting',
            otp TEXT,
            msg_id INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS voc_codes (
            code TEXT PRIMARY KEY,
            quota INTEGER NOT NULL,
            active INTEGER DEFAULT 1,
            created_by INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            used_by INTEGER,
            used_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_orders_status ON otp_orders(status);
        CREATE INDEX IF NOT EXISTS idx_orders_user ON otp_orders(user_id);
        CREATE INDEX IF NOT EXISTS idx_trx_user ON transactions(user_id);
        """)

def create_user(user_id, username, first_name):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, username, first_name) VALUES(?,?,?)",
            (user_id, username or "", first_name or "")
        )

def get_user(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def add_saldo(user_id, amount, desc=""):
    if amount <= 0:
        return False
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE users SET saldo = saldo + ? WHERE user_id=?",
                (amount, user_id)
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)",
                (user_id, "IN", amount, desc)
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            return False

def deduct_saldo(user_id, amount, desc=""):
    if amount <= 0:
        return False
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE users SET saldo = saldo - ? WHERE user_id=? AND saldo >= ?",
                (amount, user_id, amount)
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)",
                (user_id, "OUT", amount, desc)
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            return False

def get_user_transactions(user_id, limit=10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()

def create_otp_order(user_id, order_id, token, phone, service_name, price):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO otp_orders(order_id,user_id,token,phone,service_name,price,status) "
            "VALUES(?,?,?,?,?,?, 'waiting')",
            (str(order_id), user_id, token, phone, service_name, price)
        )

def get_otp_order(order_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM otp_orders WHERE order_id=?", (str(order_id),)
        ).fetchone()

def set_order_msg_id(order_id, msg_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE otp_orders SET msg_id=? WHERE order_id=?",
            (msg_id, str(order_id))
        )

def mark_success_if_waiting(order_id, otp):
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE otp_orders SET status='success', otp=? "
            "WHERE order_id=? AND status='waiting'",
            (otp, str(order_id))
        )
        return cur.rowcount == 1

def mark_status_if_waiting(order_id, new_status):
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE otp_orders SET status=? WHERE order_id=? AND status='waiting'",
            (new_status, str(order_id))
        )
        return cur.rowcount == 1

def update_otp_order_status(order_id, status, otp=None):
    with get_conn() as conn:
        if otp is not None:
            conn.execute(
                "UPDATE otp_orders SET status=?, otp=? WHERE order_id=?",
                (status, otp, str(order_id))
            )
        else:
            conn.execute(
                "UPDATE otp_orders SET status=? WHERE order_id=?",
                (status, str(order_id))
            )

def get_stuck_waiting_orders(minutes=20):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM otp_orders WHERE status='waiting' "
            "AND created_at <= strftime('%s','now') - ?",
            (minutes * 60,)
        ).fetchall()

def get_trial_kopken(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT trial_kopken FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return row['trial_kopken'] if row else 0

def set_trial_kopken(user_id, n):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET trial_kopken=? WHERE user_id=?", (n, user_id)
        )

def create_voc(code, quota, created_by):
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO voc_codes(code,quota,created_by) VALUES(?,?,?)",
                (code, quota, created_by)
            )
            return True
        except sqlite3.IntegrityError:
            return False

def get_voc(code):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM voc_codes WHERE code=? AND active=1", (code,)
        ).fetchone()

def use_voc(code, user_id):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM voc_codes WHERE code=? AND active=1", (code,)
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return False, "Kode tidak valid atau sudah dipakai.", 0
            quota = row['quota']
            cur = conn.execute(
                "UPDATE voc_codes SET active=0, used_by=?, used_at=strftime('%s','now') "
                "WHERE code=? AND active=1",
                (user_id, code)
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return False, "Kode sudah dipakai.", 0
            conn.execute("UPDATE users SET saldo = saldo + ? WHERE user_id=?", (quota, user_id))
            conn.execute(
                "INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)",
                (user_id, "IN", quota, f"Redeem VOC {code}")
            )
            conn.execute("COMMIT")
            return True, f"Kode {code} berhasil. Balance +Rp {quota:,}", quota
        except Exception:
            conn.execute("ROLLBACK")
            return False, "Gagal redeem.", 0

def list_voc_codes(limit=20):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM voc_codes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

def revoke_voc(code):
    with get_conn() as conn:
        conn.execute("UPDATE voc_codes SET active=0 WHERE code=?", (code,))

def get_voc_detail(code):
    with get_conn() as conn:
        voc = conn.execute("SELECT * FROM voc_codes WHERE code=?", (code,)).fetchone()
        if not voc:
            return None, None
        user = None
        if voc['used_by']:
            user = conn.execute(
                "SELECT * FROM users WHERE user_id=?", (voc['used_by'],)
            ).fetchone()
        return voc, user

def get_stats():
    with get_conn() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
        cek = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE description LIKE 'Cek%'"
        ).fetchone()['c']
        return {"total_users": users, "total_cek": cek}

def list_all_users(limit=50):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

def get_user_detail(user_id):
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            return None, []
        vocs = conn.execute(
            "SELECT * FROM voc_codes WHERE used_by=? ORDER BY used_at DESC", (user_id,)
        ).fetchall()
        return user, vocs

def get_recent_topups(limit=10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM transactions WHERE type='IN' AND description LIKE 'Topup%' "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

def get_recent_users(limit=10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()