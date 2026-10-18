import database as db

def fix_loop():
    print("Menginisialisasi database...")
    db.init_db() # <--- TAMBAHIN INI BIAR TABELNYA DIBUAT KALO BELOM ADA
    
    with db.get_conn() as conn:
        try:
            conn.execute("UPDATE sfood_orders SET status = 'done' WHERE status = 'active'")
            print("✅ Semua orderan nyangkut udah di-set selesai.")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    fix_loop()