import os
import time
import datetime
from contextlib import contextmanager
import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL")

@contextmanager
def get_conn():
    # Fallback ke SQLite lokal kalau dites di PC
    if not DATABASE_URL:
        import sqlite3
        conn = sqlite3.connect("bot.db", timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()
    else:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            yield conn, cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

def init_db():
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cursor = ctx
            queries = [
                """CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    saldo INTEGER DEFAULT 0,
                    trial_kopken INTEGER DEFAULT 2,
                    created_at INTEGER DEFAULT extract(epoch from now())
                );""",
                """CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    description TEXT DEFAULT '',
                    created_at INTEGER DEFAULT extract(epoch from now())
                );""",
                """CREATE TABLE IF NOT EXISTS otp_orders (
                    order_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    token TEXT,
                    phone TEXT,
                    service_name TEXT,
                    price INTEGER,
                    status TEXT DEFAULT 'waiting',
                    otp TEXT,
                    msg_id BIGINT,
                    created_at INTEGER DEFAULT extract(epoch from now())
                );""",
                """CREATE TABLE IF NOT EXISTS voc_codes (
                    code TEXT PRIMARY KEY,
                    quota INTEGER NOT NULL,
                    active INTEGER DEFAULT 1,
                    created_by BIGINT,
                    created_at INTEGER DEFAULT extract(epoch from now()),
                    used_by BIGINT,
                    used_at INTEGER
                );"""
            ]
            for q in queries:
                cursor.execute(q)
        else:
            conn = ctx
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '', saldo INTEGER DEFAULT 0, trial_kopken INTEGER DEFAULT 2, created_at INTEGER DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, amount INTEGER NOT NULL, description TEXT DEFAULT '', created_at INTEGER DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS otp_orders (order_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, token TEXT, phone TEXT, service_name TEXT, price INTEGER, status TEXT DEFAULT 'waiting', otp TEXT, msg_id INTEGER, created_at INTEGER DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS voc_codes (code TEXT PRIMARY KEY, quota INTEGER NOT NULL, active INTEGER DEFAULT 1, created_by INTEGER, created_at INTEGER DEFAULT (strftime('%s','now')), used_by INTEGER, used_at INTEGER);
            """)

def create_user(user_id, username, first_name):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("INSERT INTO users(user_id, username, first_name) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (user_id, username, first_name))
        else:
            ctx.execute("INSERT OR IGNORE INTO users(user_id, username, first_name) VALUES(?,?,?)", (user_id, username, first_name))

def get_user(user_id):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            return cur.fetchone()
        else:
            return ctx.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def add_saldo(user_id, amount, desc=""):
    if amount <= 0: return False
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT saldo FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
            if not cur.fetchone(): return False
            cur.execute("UPDATE users SET saldo = saldo + %s WHERE user_id=%s", (amount, user_id))
            cur.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(%s,%s,%s,%s)", (user_id, 'IN', amount, desc))
            return True
        else:
            ctx.execute("BEGIN IMMEDIATE")
            cur = ctx.execute("UPDATE users SET saldo = saldo + ? WHERE user_id=?", (amount, user_id))
            if cur.rowcount == 0: return False
            ctx.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)", (user_id, 'IN', amount, desc))
            return True

def deduct_saldo(user_id, amount, desc=""):
    if amount <= 0: return False
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT saldo FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
            row = cur.fetchone()
            if not row or row['saldo'] < amount: return False
            cur.execute("UPDATE users SET saldo = saldo - %s WHERE user_id=%s", (amount, user_id))
            cur.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(%s,%s,%s,%s)", (user_id, 'OUT', amount, desc))
            return True
        else:
            ctx.execute("BEGIN IMMEDIATE")
            cur = ctx.execute("UPDATE users SET saldo = saldo - ? WHERE user_id=? AND saldo >= ?", (amount, user_id, amount))
            if cur.rowcount == 0: return False
            ctx.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)", (user_id, 'OUT', amount, desc))
            return True

def create_otp_order(user_id, order_id, token, phone, service_name, price):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("INSERT INTO otp_orders(order_id,user_id,token,phone,service_name,price,status) VALUES(%s,%s,%s,%s,%s,%s,'waiting') ON CONFLICT (order_id) DO UPDATE SET status='waiting'", (order_id, user_id, token, phone, service_name, price))
        else:
            ctx.execute("INSERT OR REPLACE INTO otp_orders(order_id,user_id,token,phone,service_name,price,status) VALUES(?,?,?,?,?,?,'waiting')", (order_id, user_id, token, phone, service_name, price))

def get_otp_order(order_id):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM otp_orders WHERE order_id=%s", (order_id,))
            return cur.fetchone()
        else:
            return ctx.execute("SELECT * FROM otp_orders WHERE order_id=?", (order_id,)).fetchone()

def set_order_msg_id(order_id, msg_id):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE otp_orders SET msg_id=%s WHERE order_id=%s", (msg_id, order_id))
        else:
            ctx.execute("UPDATE otp_orders SET msg_id=? WHERE order_id=?", (msg_id, order_id))

def mark_success_if_waiting(order_id, otp):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE otp_orders SET status='success', otp=%s WHERE order_id=%s AND status='waiting'", (otp, order_id))
            return cur.rowcount == 1
        else:
            cur = ctx.execute("UPDATE otp_orders SET status='success', otp=? WHERE order_id=? AND status='waiting'", (otp, order_id))
            return cur.rowcount == 1

def mark_status_if_waiting(order_id, new_status):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE otp_orders SET status=%s WHERE order_id=%s AND status='waiting'", (new_status, order_id))
            return cur.rowcount == 1
        else:
            cur = ctx.execute("UPDATE otp_orders SET status=? WHERE order_id=? AND status='waiting'", (new_status, order_id))
            return cur.rowcount == 1

def update_otp_order_status(order_id, status, otp=None):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            if otp:
                cur.execute("UPDATE otp_orders SET status=%s, otp=%s WHERE order_id=%s", (status, otp, order_id))
            else:
                cur.execute("UPDATE otp_orders SET status=%s WHERE order_id=%s", (status, order_id))
        else:
            if otp:
                ctx.execute("UPDATE otp_orders SET status=?, otp=? WHERE order_id=?", (status, otp, order_id))
            else:
                ctx.execute("UPDATE otp_orders SET status=? WHERE order_id=?", (status, order_id))

def get_user_transactions(user_id, limit=10):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT %s", (user_id, limit))
            return cur.fetchall()
        else:
            return ctx.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()

def get_stuck_waiting_orders(minutes=20):
    with get_conn() as ctx:
        limit = int(time.time()) - (minutes * 60)
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM otp_orders WHERE status='waiting' AND created_at <= %s", (limit,))
            return cur.fetchall()
        else:
            return ctx.execute("SELECT * FROM otp_orders WHERE status='waiting' AND created_at <= ?", (limit,)).fetchall()

def get_trial_kopken(user_id):
    u = get_user(user_id)
    return u['trial_kopken'] if u else 0

def set_trial_kopken(user_id, n):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE users SET trial_kopken=%s WHERE user_id=%s", (n, user_id))
        else:
            ctx.execute("UPDATE users SET trial_kopken=? WHERE user_id=?", (n, user_id))

def create_voc(code, quota, created_by):
    with get_conn() as ctx:
        try:
            if DATABASE_URL:
                conn, cur = ctx
                cur.execute("INSERT INTO voc_codes(code,quota,created_by) VALUES(%s,%s,%s)", (code, quota, created_by))
            else:
                ctx.execute("INSERT INTO voc_codes(code,quota,created_by) VALUES(?,?,?)", (code, quota, created_by))
            return True
        except Exception:
            return False

def get_voc(code):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM voc_codes WHERE code=%s AND active=1", (code,))
            return cur.fetchone()
        else:
            return ctx.execute("SELECT * FROM voc_codes WHERE code=? AND active=1", (code,)).fetchone()

def use_voc(code, user_id):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT quota FROM voc_codes WHERE code=%s AND active=1 FOR UPDATE", (code,))
            row = cur.fetchone()
            if not row: return False, "Kode invalid", 0
            cur.execute("UPDATE voc_codes SET active=0, used_by=%s, used_at=extract(epoch from now()) WHERE code=%s", (user_id, code))
            cur.execute("UPDATE users SET saldo = saldo + %s WHERE user_id=%s", (row['quota'], user_id))
            cur.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(%s,%s,%s,%s)", (user_id, 'IN', row['quota'], f"Redeem {code}"))
            return True, f"Berhasil", row['quota']
        else:
            ctx.execute("BEGIN IMMEDIATE")
            row = ctx.execute("SELECT quota FROM voc_codes WHERE code=? AND active=1", (code,)).fetchone()
            if not row: return False, "Kode invalid", 0
            ctx.execute("UPDATE voc_codes SET active=0, used_by=?, used_at=strftime('%s','now') WHERE code=?", (user_id, code))
            ctx.execute("UPDATE users SET saldo = saldo + ? WHERE user_id=?", (row['quota'], user_id))
            ctx.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)", (user_id, 'IN', row['quota'], f"Redeem {code}"))
            return True, f"Berhasil", row['quota']

def list_voc_codes(limit=20):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM voc_codes ORDER BY created_at DESC LIMIT %s", (limit,))
            return cur.fetchall()
        else:
            return ctx.execute("SELECT * FROM voc_codes ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

def revoke_voc(code):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE voc_codes SET active=0 WHERE code=%s", (code,))
        else:
            ctx.execute("UPDATE voc_codes SET active=0 WHERE code=?", (code,))

def get_voc_detail(code):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM voc_codes WHERE code=%s", (code,))
            voc = cur.fetchone()
            if not voc: return None, None
            u = None
            if voc['used_by']:
                cur.execute("SELECT * FROM users WHERE user_id=%s", (voc['used_by'],))
                u = cur.fetchone()
            return voc, u
        else:
            voc = ctx.execute("SELECT * FROM voc_codes WHERE code=?", (code,)).fetchone()
            if not voc: return None, None
            u = None
            if voc['used_by']:
                u = ctx.execute("SELECT * FROM users WHERE user_id=?", (voc['used_by'],)).fetchone()
            return voc, u

def get_stats():
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT COUNT(*) FROM users")
            users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM transactions WHERE description LIKE 'Cek%'")
            cek = cur.fetchone()[0]
        else:
            users = ctx.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            cek = ctx.execute("SELECT COUNT(*) FROM transactions WHERE description LIKE 'Cek%'").fetchone()[0]
        return {"total_users": users, "total_cek": cek}

def list_all_users(limit=50):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT %s", (limit,))
            return cur.fetchall()
        else:
            return ctx.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

def get_user_detail(user_id):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            u = cur.fetchone()
            if not u: return None, []
            cur.execute("SELECT * FROM voc_codes WHERE used_by=%s", (user_id,))
            v = cur.fetchall()
            return u, v
        else:
            u = ctx.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not u: return None, []
            v = ctx.execute("SELECT * FROM voc_codes WHERE used_by=?", (user_id,)).fetchall()
            return u, v

def get_recent_topups(limit=10):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM transactions WHERE type='IN' AND description LIKE 'Topup%' ORDER BY id DESC LIMIT %s", (limit,))
            return cur.fetchall()
        else:
            return ctx.execute("SELECT * FROM transactions WHERE type='IN' AND description LIKE 'Topup%' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

def get_recent_users(limit=10):
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT %s", (limit,))
            return cur.fetchall()
        else:
            return ctx.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()