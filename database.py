import os
import time
import datetime
import hashlib
import secrets
import json
from contextlib import contextmanager
import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL")

@contextmanager
def get_conn():
    # Fallback ke SQLite lokal kalau dites di PC
    if not DATABASE_URL:
        import sqlite3
        conn = sqlite3.connect(os.getenv("SQLITE_DB_PATH", "bot.db"), timeout=30, isolation_level=None)
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
            # Additive migration only: keep existing orders and user data intact.
            cursor.execute("ALTER TABLE otp_orders ADD COLUMN IF NOT EXISTS otp_history TEXT DEFAULT '[]'")
        else:
            conn = ctx
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '', saldo INTEGER DEFAULT 0, trial_kopken INTEGER DEFAULT 2, created_at INTEGER DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, amount INTEGER NOT NULL, description TEXT DEFAULT '', created_at INTEGER DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS otp_orders (order_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, token TEXT, phone TEXT, service_name TEXT, price INTEGER, status TEXT DEFAULT 'waiting', otp TEXT, msg_id INTEGER, created_at INTEGER DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS voc_codes (code TEXT PRIMARY KEY, quota INTEGER NOT NULL, active INTEGER DEFAULT 1, created_by INTEGER, created_at INTEGER DEFAULT (strftime('%s','now')), used_by INTEGER, used_at INTEGER);
            """)
            otp_columns = {row[1] for row in conn.execute("PRAGMA table_info(otp_orders)").fetchall()}
            if "otp_history" not in otp_columns:
                conn.execute("ALTER TABLE otp_orders ADD COLUMN otp_history TEXT DEFAULT '[]'")
    init_whatsapp_invites()

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
            if cur.rowcount == 0:
                ctx.rollback()
                return False
            ctx.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)", (user_id, 'IN', amount, desc))
            ctx.commit()
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
            if cur.rowcount == 0:
                ctx.rollback()
                return False
            ctx.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)", (user_id, 'OUT', amount, desc))
            ctx.commit()
            return True

def create_otp_order(user_id, order_id, token, phone, service_name, price):
    order_id = str(order_id)
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("INSERT INTO otp_orders(order_id,user_id,token,phone,service_name,price,status) VALUES(%s,%s,%s,%s,%s,%s,'waiting') ON CONFLICT (order_id) DO UPDATE SET status='waiting'", (order_id, user_id, token, phone, service_name, price))
        else:
            ctx.execute("INSERT OR REPLACE INTO otp_orders(order_id,user_id,token,phone,service_name,price,status) VALUES(?,?,?,?,?,?,'waiting')", (order_id, user_id, token, phone, service_name, price))

def get_otp_order(order_id):
    order_id = str(order_id)
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT * FROM otp_orders WHERE order_id=%s", (order_id,))
            return cur.fetchone()
        else:
            return ctx.execute("SELECT * FROM otp_orders WHERE order_id=?", (order_id,)).fetchone()

def set_order_msg_id(order_id, msg_id):
    order_id = str(order_id)
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE otp_orders SET msg_id=%s WHERE order_id=%s", (msg_id, order_id))
        else:
            ctx.execute("UPDATE otp_orders SET msg_id=? WHERE order_id=?", (msg_id, order_id))

def mark_success_if_waiting(order_id, otp):
    order_id = str(order_id)
    history = json.dumps([str(otp)])
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE otp_orders SET status='success', otp=%s, otp_history=%s WHERE order_id=%s AND status='waiting'", (otp, history, order_id))
            return cur.rowcount == 1
        else:
            cur = ctx.execute("UPDATE otp_orders SET status='success', otp=?, otp_history=? WHERE order_id=? AND status='waiting'", (otp, history, order_id))
            return cur.rowcount == 1

def get_otp_history(order_id):
    order = get_otp_order(order_id)
    if not order:
        return []
    raw = order['otp_history'] if 'otp_history' in order.keys() else None
    try:
        history = json.loads(raw or '[]')
        if isinstance(history, list):
            history = [str(code) for code in history if code is not None]
            if history:
                return history
    except (TypeError, ValueError):
        pass
    otp = order['otp'] if 'otp' in order.keys() else None
    return [str(otp)] if otp else []

def append_otp_history(order_id, otp):
    """Append a distinct OTP without losing earlier codes for this order."""
    order_id = str(order_id)
    otp = str(otp)
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("SELECT otp_history, otp FROM otp_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row:
                return []
            try:
                history = json.loads(row['otp_history'] or '[]')
            except (TypeError, ValueError):
                history = []
            if not isinstance(history, list):
                history = []
            history = [str(code) for code in history if code is not None]
            if not history and row['otp']:
                history.append(str(row['otp']))
            if otp not in history:
                history.append(otp)
            cur.execute("UPDATE otp_orders SET otp_history=%s WHERE order_id=%s", (json.dumps(history), order_id))
            return history
        else:
            ctx.execute("BEGIN IMMEDIATE")
            row = ctx.execute("SELECT otp_history, otp FROM otp_orders WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                ctx.commit()
                return []
            try:
                history = json.loads(row['otp_history'] or '[]')
            except (TypeError, ValueError):
                history = []
            if not isinstance(history, list):
                history = []
            history = [str(code) for code in history if code is not None]
            if not history and row['otp']:
                history.append(str(row['otp']))
            if otp not in history:
                history.append(otp)
            ctx.execute("UPDATE otp_orders SET otp_history=? WHERE order_id=?", (json.dumps(history), order_id))
            ctx.commit()
            return history

def mark_status_if_waiting(order_id, new_status):
    order_id = str(order_id)
    with get_conn() as ctx:
        if DATABASE_URL:
            conn, cur = ctx
            cur.execute("UPDATE otp_orders SET status=%s WHERE order_id=%s AND status='waiting'", (new_status, order_id))
            return cur.rowcount == 1
        else:
            cur = ctx.execute("UPDATE otp_orders SET status=? WHERE order_id=? AND status='waiting'", (new_status, order_id))
            return cur.rowcount == 1

def update_otp_order_status(order_id, status, otp=None):
    order_id = str(order_id)
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

def finish_otp_order_and_refund(order_id, user_id, new_status, description):
    """Transition one waiting order and credit its refund exactly once."""
    order_id = str(order_id)
    if DATABASE_URL:
        with get_conn() as ctx:
            conn, cur = ctx
            cur.execute(
                "UPDATE otp_orders SET status=%s "
                "WHERE order_id=%s AND user_id=%s AND status='waiting' RETURNING price",
                (new_status, order_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return False, 0
            amount = max(0, int(row['price'] or 0))
            if amount:
                cur.execute("UPDATE users SET saldo = saldo + %s WHERE user_id=%s", (amount, user_id))
                if cur.rowcount != 1:
                    raise RuntimeError(f"User {user_id} tidak ditemukan saat refund order {order_id}")
                cur.execute(
                    "INSERT INTO transactions(user_id,type,amount,description) VALUES(%s,%s,%s,%s)",
                    (user_id, 'IN', amount, description),
                )
            return True, amount

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT price FROM otp_orders WHERE order_id=? AND user_id=? AND status='waiting'",
                (order_id, user_id),
            ).fetchone()
            if not row:
                conn.rollback()
                return False, 0

            cur = conn.execute(
                "UPDATE otp_orders SET status=? "
                "WHERE order_id=? AND user_id=? AND status='waiting'",
                (new_status, order_id, user_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False, 0

            amount = max(0, int(row['price'] or 0))
            if amount:
                cur = conn.execute(
                    "UPDATE users SET saldo=saldo+? WHERE user_id=?", (amount, user_id)
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"User {user_id} tidak ditemukan saat refund order {order_id}")
                conn.execute(
                    "INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)",
                    (user_id, 'IN', amount, description),
                )
            conn.commit()
            return True, amount
        except Exception:
            conn.rollback()
            raise


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
            ctx.commit()
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

# WhatsApp access codes are separate from balance vouchers. Only SHA-256 hashes
# of high-entropy, single-use codes are persisted; plaintext is returned once.
def init_whatsapp_invites():
    with get_conn() as ctx:
        cur = ctx[1] if DATABASE_URL else ctx.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS whatsapp_invites (
            code_hash TEXT PRIMARY KEY,
            created_by BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            used_by BIGINT,
            used_at BIGINT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS whatsapp_access (
            user_id BIGINT PRIMARY KEY,
            granted_at BIGINT NOT NULL,
            invite_hash TEXT NOT NULL UNIQUE
        )""")


def _invite_hash(code):
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def has_whatsapp_access(user_id):
    with get_conn() as ctx:
        cur = ctx[1] if DATABASE_URL else ctx.cursor()
        marker = "%s" if DATABASE_URL else "?"
        result = cur.execute(f"SELECT user_id FROM whatsapp_access WHERE user_id={marker}", (user_id,))
        return (cur.fetchone() if DATABASE_URL else result.fetchone()) is not None


def create_whatsapp_invites(created_by, count=1):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
        raise ValueError("Jumlah kode harus 1 sampai 20.")
    codes = ["WA-" + secrets.token_hex(16).upper() for _ in range(count)]
    with get_conn() as ctx:
        cur = ctx[1] if DATABASE_URL else ctx.cursor()
        markers = "%s,%s,%s" if DATABASE_URL else "?,?,?"
        if not DATABASE_URL:
            cur.execute("BEGIN IMMEDIATE")
        try:
            for code in codes:
                cur.execute(f"INSERT INTO whatsapp_invites(code_hash, created_by, created_at) VALUES({markers})",
                            (_invite_hash(code), created_by, int(time.time())))
            if not DATABASE_URL:
                ctx.commit()
        except Exception:
            if not DATABASE_URL:
                ctx.rollback()
            raise
    return codes


def redeem_whatsapp_invite(code, user_id):
    """Return granted/already_allowed/invalid/used. Claim and grant are atomic.

    SQLite serializes writers; PostgreSQL locks per user then conditionally claims
    the code row. This also prevents one user burning two codes concurrently.
    """
    digest = _invite_hash(code)
    with get_conn() as ctx:
        cur = ctx[1] if DATABASE_URL else ctx.cursor()
        marker = "%s" if DATABASE_URL else "?"
        if DATABASE_URL:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (int(user_id),))
        else:
            cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(f"SELECT user_id FROM whatsapp_access WHERE user_id={marker}", (user_id,))
            if cur.fetchone() is not None:
                result = "already_allowed"
            else:
                now = int(time.time())
                cur.execute(f"UPDATE whatsapp_invites SET used_by={marker}, used_at={marker} "
                            f"WHERE code_hash={marker} AND used_by IS NULL", (user_id, now, digest))
                if cur.rowcount == 1:
                    cur.execute(f"INSERT INTO whatsapp_access(user_id, granted_at, invite_hash) "
                                f"VALUES({marker},{marker},{marker})", (user_id, now, digest))
                    result = "granted"
                else:
                    cur.execute(f"SELECT used_by FROM whatsapp_invites WHERE code_hash={marker}", (digest,))
                    result = "used" if cur.fetchone() is not None else "invalid"
            if not DATABASE_URL:
                ctx.commit()
            return result
        except Exception:
            if not DATABASE_URL:
                ctx.rollback()
            raise
