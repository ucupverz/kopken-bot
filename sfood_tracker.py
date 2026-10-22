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
                chat_id = order['user_id'] if 'user_id' in order.keys() else None
                if not chat_id:
                    continue

                # Simulasi ngecek resi / tracking
                is_finished = True 
                
                if is_finished:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ <b>PESANAN SELESAI</b>\n\nTracking dihentikan otomatis.",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        pass

                    # SET status 600 (SELESAI) BIAR GA DI-LOOP LAGI
                    db.sfood_mark_order_finished(order['order_id'])
                    print(f"[SFOOD] Pesanan {order['order_id']} selesai & dihapus dari antrean.")
            
            await asyncio.sleep(30)
            
        except Exception as e:
            print(f"[SFOOD] ❌ Worker error: {e}")
            await asyncio.sleep(10)