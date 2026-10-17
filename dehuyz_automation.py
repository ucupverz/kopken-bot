import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STR = os.getenv("TG_SESSION_STR", "")

DEHUYZ_BOT = "@dehuyzwaotpbot"

async def request_qris(amount: int):
    """Hanya minta QRIS ke bot dehuyz dan return gambar (bytes)."""
    if not API_ID or not API_HASH:
        return "error", "TG_API_ID / TG_API_HASH belum di-set!"

    client = TelegramClient(StringSession(TG_SESSION_STR), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        await client.disconnect()
        return "error", "Session expired."

    try:
        await client.send_message(DEHUYZ_BOT, "/topup")
        await asyncio.sleep(3) 
        
        messages = await client.get_messages(DEHUYZ_BOT, limit=1)
        last_msg = messages[0]
        found_custom = False
        
        if last_msg.buttons:
            for row in last_msg.buttons:
                for btn in row:
                    if "custom" in btn.text.lower() or "nominal" in btn.text.lower():
                        await btn.click()
                        found_custom = True
                        break
                if found_custom: break
        
        if not found_custom:
            return "error", "Tombol Custom tidak ditemukan."
            
        await asyncio.sleep(2)
        await client.send_message(DEHUYZ_BOT, str(amount))
        
        # Loop tunggu gambar (Max 60s)
        qris_msg = None
        for _ in range(15):
            await asyncio.sleep(4)
            messages = await client.get_messages(DEHUYZ_BOT, limit=1)
            msg = messages[0]
            if msg.photo:
                qris_msg = msg
                break
                
        if not qris_msg:
            return "error", "QRIS tidak muncul."
            
        qris_bytes = await client.download_media(qris_msg, bytes)
        return "success", qris_bytes
        
    except Exception as e:
        return "error", str(e)
    finally:
        await client.disconnect()

async def wait_for_payment(amount: int):
    """Polling cek status pembayaran setelah QRIS dikirim."""
    client = TelegramClient(StringSession(TG_SESSION_STR), API_ID, API_HASH)
    await client.connect()
    try:
        max_wait = 300 # 5 menit
        start_time = asyncio.get_event_loop().time()
        
        # Ambil pesan terakhir buat starting point
        msgs = await client.get_messages(DEHUYZ_BOT, limit=1)
        last_id = msgs[0].id if msgs else 0
        
        while asyncio.get_event_loop().time() - start_time < max_wait:
            await asyncio.sleep(5)
            new_msgs = await client.get_messages(DEHUYZ_BOT, limit=2)
            
            for msg in new_msgs:
                if msg.id > last_id:
                    text = (msg.text or "").lower()
                    if "berhasil" in text and "saldo" in text:
                        return True
                    last_id = msg.id
        return False
    finally:
        await client.disconnect()