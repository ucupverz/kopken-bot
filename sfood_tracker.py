import asyncio
import time
from datetime import datetime, timedelta
from telegram.constants import ParseMode

import database as db

async def tracker_worker(bot):
    print("[SFOOD] 🚀 Tracker worker started")
    while True:
        try:
            # Ambil semua orderan yang masih active
            orders = db.sfood_get_active_orders()
            
            for order in orders:
                # 1. Cek dulu, kalau orderan udah gaada chat_id-nya, skip
                chat_id = order['chat_id'] if 'chat_id' in order.keys() else order['user_id']
                if not chat_id:
                    continue

                # 2. Simulasi ngecek resi / tracking (karena lu ga ngasih kodingan aslinya, gua bikin simulasi selesai)
                # Di kodingan asli lu, ini bagian manggil API ShopeeFood
                is_finished = True 
                
                if is_finished:
                    # Kirim pesan bukti pengiriman
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ <b>PESANAN SELESAI</b>\n\nTracking dihentikan otomatis.",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        pass

                    # 3. KUNCI PERUBAHAN: Set status 'done' DAN hapus chat_id/user_id biar ga di-loop lagi
                    with db.get_conn() as conn:
                        conn.execute("UPDATE sfood_orders SET status = 'done', user_id = NULL WHERE id = ?", (order['id'],))
                        print(f"[SFOOD] Pesanan {order['id']} selesai & dihapus dari antrean.")
            
            # Jeda 30 detik sebelum ngecek lagi
            await asyncio.sleep(30)
            
        except Exception as e:
            print(f"[SFOOD] ❌ Worker error: {e}")
            await asyncio.sleep(10)