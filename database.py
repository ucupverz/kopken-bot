import sqlite3
import os
import time
import datetime
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
        # Tabel transactions
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
        # Tabel ShopeeFood orders
        c.execute("""
            CREATE TABLE IF NOT EXISTS sfood_orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                code TEXT,
                label TEXT,
                last_status INTEGER,
                last_status_text TEXT,
                last_eta TEXT,
                last_driver TEXT,
                last_driver_phone TEXT,
                last_flag TEXT,
                active INTEGER DEFAULT 1,
                created_at INTEGER,
                updated_at INTEGER
            )
        """)
        # Tabel ShopeeFood status log
        c.execute("""
            CREATE TABLE IF NOT EXISTS sfood_status_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                status_code INTEGER,
                status_text TEXT,
                timestamp INTEGER
            )
        """)
        # Tabel Tarif ShopeeFood
        c.execute("""
            CREATE TABLE IF NOT EXISTS tarif_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                biaya INTEGER,
                jam INTEGER,
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


def list_all_users(limit=50):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT user_id, username, first_name, quota, total_cek, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        return c.fetchall()


def get_user_detail(user_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        if not user:
            return None, []
        c.execute("""
            SELECT code, quota, used_at
            FROM voc_codes
            WHERE used_by = ?
            ORDER BY used_at DESC
        """, (user_id,))
        vocs = c.fetchall()
        return user, vocs


def get_all_user_ids():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE banned = 0")
        return [r["user_id"] for r in c.fetchall()]


# ----------------------------------------------------------------------
# VOC CODES
# ----------------------------------------------------------------------

def create_voc(code, quota, created_by):
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
            return False


def get_voc(code):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM voc_codes WHERE code = ?", (code,))
        return c.fetchone()


def use_voc(code, user_id):
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


def get_voc_detail(code):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM voc_codes WHERE code = ?", (code,))
        voc = c.fetchone()
        if not voc:
            return None, None
        if voc["used_by"]:
            c.execute("SELECT user_id, username, first_name FROM users WHERE user_id = ?",
                      (voc["used_by"],))
            user = c.fetchone()
            return voc, user
        return voc, None


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


# ----------------------------------------------------------------------
# SHOPEEFOOD TRACKER
# ----------------------------------------------------------------------

def sfood_add_order(user_id, order_id, code, label, last_status,
                    last_status_text, last_eta, last_driver, last_driver_phone):
    now = int(time.time())
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO sfood_orders
            (order_id, user_id, code, label, last_status, last_status_text,
             last_eta, last_driver, last_driver_phone, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (order_id, user_id, code, label, last_status, last_status_text,
              last_eta, last_driver, last_driver_phone, now, now))
        conn.commit()


def sfood_order_exists(order_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM sfood_orders WHERE order_id = ? AND active = 1", (order_id,))
        return c.fetchone() is not None


def sfood_get_active_orders():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM sfood_orders WHERE active = 1")
        return c.fetchall()


def sfood_get_orders_by_user(user_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM sfood_orders WHERE user_id = ? AND active = 1 ORDER BY created_at DESC", (user_id,))
        return c.fetchall()


def sfood_update_status(order_id, status_code, eta, driver, driver_phone):
    now = int(time.time())
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            UPDATE sfood_orders
            SET last_status = ?, last_eta = ?, last_driver = ?, last_driver_phone = ?, updated_at = ?
            WHERE order_id = ?
        """, (status_code, eta, driver, driver_phone, now, order_id))
        conn.commit()


def sfood_update_flag(order_id, flag):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE sfood_orders SET last_flag = ? WHERE order_id = ?", (flag, order_id))
        conn.commit()


def sfood_add_log(order_id, status_code, status_text):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO sfood_status_log (order_id, status_code, status_text, timestamp)
            VALUES (?, ?, ?, ?)
        """, (order_id, status_code, status_text, int(time.time())))
        conn.commit()


def sfood_stop_order(order_id, user_id=None):
    with get_conn() as conn:
        c = conn.cursor()
        if user_id:
            c.execute("UPDATE sfood_orders SET active = 0 WHERE order_id = ? AND user_id = ?", (order_id, user_id))
        else:
            c.execute("UPDATE sfood_orders SET active = 0 WHERE order_id = ?", (order_id,))
        conn.commit()
        return c.rowcount > 0


def sfood_stop_all_user(user_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE sfood_orders SET active = 0 WHERE user_id = ? AND active = 1", (user_id,))
        conn.commit()
        return c.rowcount


# ----------------------------------------------------------------------
# TARIF SHOPEEFOOD
# ----------------------------------------------------------------------

def tarif_add(user_id, biaya):
    """Simpan input tarif. jam diambil dari timestamp sekarang."""
    now_ts = int(time.time())
    jam = datetime.datetime.now().hour
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO tarif_logs (user_id, biaya, jam, timestamp)
            VALUES (?, ?, ?, ?)
        """, (user_id, biaya, jam, now_ts))
        conn.commit()


def tarif_get_latest(user_id=None):
    """Ambil input tarif terakhir. Kalau user_id None, ambil global."""
    with get_conn() as conn:
        c = conn.cursor()
        if user_id:
            c.execute("SELECT * FROM tarif_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", (user_id,))
        else:
            c.execute("SELECT * FROM tarif_logs ORDER BY timestamp DESC LIMIT 1")
        return c.fetchone()


def tarif_get_avg_for_hour(jam):
    """Rata-rata tarif untuk jam tertentu. Return (avg, count)."""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT AVG(biaya) as avg_b, COUNT(*) as cnt FROM tarif_logs WHERE jam = ?", (jam,))
        row = c.fetchone()
        if not row or row["cnt"] == 0 or row["avg_b"] is None:
            return None, 0
        return int(row["avg_b"]), row["cnt"]


def tarif_get_stats():
    """Statistik lengkap."""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as total, MIN(biaya) as min_b, MAX(biaya) as max_b, AVG(biaya) as avg_b FROM tarif_logs")
        row = c.fetchone()
        total = row["total"] or 0
        min_b = row["min_b"]
        max_b = row["max_b"]
        avg_b = int(row["avg_b"]) if row["avg_b"] else 0

        # Jam termurah
        c.execute("""
            SELECT jam, AVG(biaya) as avg_b, COUNT(*) as cnt
            FROM tarif_logs
            GROUP BY jam
            HAVING cnt >= 1
            ORDER BY avg_b ASC
            LIMIT 3
        """)
        cheapest_hours = c.fetchall()

        # Jam termahal
        c.execute("""
            SELECT jam, AVG(biaya) as avg_b, COUNT(*) as cnt
            FROM tarif_logs
            GROUP BY jam
            HAVING cnt >= 1
            ORDER BY avg_b DESC
            LIMIT 3
        """)
        expensive_hours = c.fetchall()

        return {
            "total": total,
            "min": min_b,
            "max": max_b,
            "avg": avg_b,
            "cheapest": cheapest_hours,
            "expensive": expensive_hours,
        }