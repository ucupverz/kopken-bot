import sqlite3
import os
import datetime

DB_PATH = os.getenv("DB_PATH", "kopken.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None) 
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        # Users
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                saldo INTEGER DEFAULT 0,
                trial_kopken INTEGER DEFAULT 2,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # OTP Orders
        conn.execute("""
            CREATE TABLE IF NOT EXISTS otp_orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                token TEXT,
                phone TEXT,
                service_name TEXT,
                price INTEGER,
                status TEXT DEFAULT 'waiting',
                otp TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # VOC
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voc_codes (
                code TEXT PRIMARY KEY,
                quota INTEGER,
                active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_by INTEGER,
                used_at TIMESTAMP
            )
        """)
        
        # MUTASI SALDO
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saldo_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                description TEXT,
                final_saldo INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ Database initialized.")

# === FUNGSI SALDO (ANTI RACE-CONDITION) ===
def add_saldo(user_id, amount, type="system", description="Saldo Masuk"):
    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("SELECT saldo FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return False

            new_saldo = row['saldo'] + amount
            conn.execute("UPDATE users SET saldo = ? WHERE user_id = ?", (new_saldo, user_id))
            conn.execute(
                "INSERT INTO saldo_history (user_id, amount, type, description, final_saldo) VALUES (?, ?, ?, ?, ?)",
                (user_id, amount, type, description, new_saldo)
            )
            conn.execute("COMMIT")
            return True
        except Exception as e:
            conn.execute("ROLLBACK")
            return False

def deduct_saldo(user_id, amount, description="Pembelian"):
    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("SELECT saldo FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row or row['saldo'] < amount:
                conn.execute("ROLLBACK")
                return False
            
            new_saldo = row['saldo'] - amount
            conn.execute("UPDATE users SET saldo = ? WHERE user_id = ?", (new_saldo, user_id))
            conn.execute(
                "INSERT INTO saldo_history (user_id, amount, type, description, final_saldo) VALUES (?, ?, ?, ?, ?)",
                (user_id, -amount, "deduct", description, new_saldo)
            )
            conn.execute("COMMIT")
            return True
        except Exception as e:
            conn.execute("ROLLBACK")
            return False

def get_saldo_history(user_id, limit=5):
    with get_conn() as conn:
        cursor = conn.execute(
            "SELECT * FROM saldo_history WHERE user_id = ? ORDER BY id DESC LIMIT ?", 
            (user_id, limit)
        )
        return cursor.fetchall()

def get_user(user_id):
    with get_conn() as conn:
        cursor = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()

def create_user(user_id, username, first_name):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user_id, username, first_name)
        )

def get_trial_kopken(user_id):
    with get_conn() as conn:
        cursor = conn.execute("SELECT trial_kopken FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row and row['trial_kopken'] is not None:
            return row['trial_kopken']
        return 2

def set_trial_kopken(user_id, sisa):
    with get_conn() as conn:
        conn.execute("UPDATE users SET trial_kopken = ? WHERE user_id = ?", (sisa, user_id))

def create_otp_order(user_id, order_id, token, phone, service_name, price):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO otp_orders (order_id, user_id, token, phone, service_name, price) VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, user_id, token, phone, service_name, price)
        )

def update_otp_order_status(order_id, status, otp=None):
    with get_conn() as conn:
        if otp:
            conn.execute("UPDATE otp_orders SET status = ?, otp = ? WHERE order_id = ?", (status, otp, order_id))
        else:
            conn.execute("UPDATE otp_orders SET status = ? WHERE order_id = ?", (status, order_id))

def get_otp_order(order_id):
    with get_conn() as conn:
        cursor = conn.execute("SELECT * FROM otp_orders WHERE order_id = ?", (order_id,))
        return cursor.fetchone()

# FUNGSI VOC
def create_voc(code, quota, created_by):
    with get_conn() as conn:
        try:
            conn.execute("INSERT INTO voc_codes (code, quota, created_by) VALUES (?, ?, ?)", (code, quota, created_by))
            return True
        except sqlite3.IntegrityError:
            return False

def get_voc(code):
    with get_conn() as conn:
        cursor = conn.execute("SELECT * FROM voc_codes WHERE code = ?", (code,))
        return cursor.fetchone()

def use_voc(code, user_id):
    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("SELECT * FROM voc_codes WHERE code = ? AND active = 1", (code,))
            voc = cursor.fetchone()
            if not voc:
                conn.execute("ROLLBACK")
                return False, "Kode tidak valid atau sudah terpakai.", 0
            
            conn.execute("UPDATE voc_codes SET active = 0, used_by = ?, used_at = CURRENT_TIMESTAMP WHERE code = ?", (user_id, code))
            
            cursor2 = conn.execute("SELECT saldo FROM users WHERE user_id = ?", (user_id,))
            row2 = cursor2.fetchone()
            new_saldo = row2['saldo'] + voc['quota']
            
            conn.execute("UPDATE users SET saldo = ? WHERE user_id = ?", (new_saldo, user_id))
            conn.execute(
                "INSERT INTO saldo_history (user_id, amount, type, description, final_saldo) VALUES (?, ?, ?, ?, ?)",
                (user_id, voc['quota'], "voc", f"Klaim VOC {code}", new_saldo)
            )
            
            conn.execute("COMMIT")
            return True, f"Berhasil klaim Rp {voc['quota']:,}", voc['quota']
        except Exception as e:
            conn.execute("ROLLBACK")
            return False, f"Error: {e}", 0