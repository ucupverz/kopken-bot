import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STR = os.getenv("TG_SESSION_STR", "")

DEHUYZ_BOT = "@dehuyzwaotpbot"

async def get_qris_and_wait_payment(amount: int):
    if not API_ID or not API_HASH:
        return "error", "TG_API_ID / TG_API_HASH belum di-set di Environment!"

    client = TelegramClient(StringSession(TG_SESSION_STR), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        await client.disconnect()
        return "error", "Session Telegram expired. Silakan bikin ulang StringSession."

    try:
        # 1. Kirim /topup
        await client.send_message(DEHUYZ_BOT, "/topup")
        await asyncio.sleep(3) 
        
        # 2. Ambil pesan terakhir
        messages = await client.get_messages(DEHUYZ_BOT, limit=1)
        last_msg = messages[0]
        found_custom = False
        
        # 3. Cari tombol "Nominal Custom"
        if last_msg.buttons:
            for row in last_msg.buttons:
                for btn in row:
                    if "custom" in btn.text.lower() or "nominal" in btn.text.lower():
                        await btn.click()
                        found_custom = True
                        break
                if found_custom: break
        
        if not found_custom:
            return "error", "Tombol 'Nominal Custom' tidak ditemukan."
            
        await asyncio.sleep(2)
        
        # 4. Kirim nominal
        await client.send_message(DEHUYZ_BOT, str(amount))
        
        # 5. LOOPING TUNGGU QRIS MUNCUL (Maksimal 60 detik)
        qris_msg = None
        for _ in range(15): # 15 x 4 detik = 60 detik
            await asyncio.sleep(4)
            messages = await client.get_messages(DEHUYZ_BOT, limit=1)
            msg = messages[0]
            
            if msg.photo: # Kalau udah ada gambar
                qris_msg = msg
                break
                
        if not qris_msg:
            return "error", "QRIS gambar tidak muncul (Timeout 60s)."
            
        # Download gambar QRIS
        qris_bytes = await client.download_media(qris_msg, bytes)
        print(f"[DEHUYZ] QRIS didapatkan untuk nominal {amount}")
        
        # 6. Polling status pembayaran (Maks 5 menit)
        max_wait = 300 
        start_time = asyncio.get_event_loop().time()
        last_id = qris_msg.id
        
        while asyncio.get_event_loop().time() - start_time < max_wait:
            await asyncio.sleep(5)
            new_msgs = await client.get_messages(DEHUYZ_BOT, limit=2)
            
            for msg in new_msgs:
                if msg.id > last_id:
                    text = (msg.text or "").lower()
                    if "berhasil" in text and "saldo" in text:
                        print(f"[DEHUYZ] Pembayaran Sukses untuk Rp{amount}")
                        return "success", qris_bytes
                    last_id = msg.id
                    
        return "timeout", qris_bytes
        
    except Exception as e:
        print(f"[DEHUYZ] Error: {e}")
        return "error", str(e)
    finally:
        await client.disconnect()