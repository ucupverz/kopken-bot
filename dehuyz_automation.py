import asyncio
import os
import re
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STR = os.getenv("TG_SESSION_STR", "")

DEHUYZ_BOT = "@dehuyzwaotpbot"

def _parse_amounts(text):
    """Ambil semua kandidat angka dari teks, normalisasi 10.000 / 10,000 -> 10000."""
    out = set()
    for raw in re.findall(r"\d[\d.,]*", text):
        cleaned = raw.replace(".", "").replace(",", "")
        try:
            out.add(int(cleaned))
        except ValueError:
            continue
    return out

async def request_qris(amount: int):
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
                    t = btn.text.lower()
                    if "custom" in t or "nominal" in t:
                        await btn.click()
                        found_custom = True
                        break
                if found_custom:
                    break
        if not found_custom:
            return "error", "Tombol Custom tidak ditemukan."

        await asyncio.sleep(2)
        await client.send_message(DEHUYZ_BOT, str(amount))

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
        caption_text = qris_msg.text or ""
        return "success", {"image": qris_bytes, "caption": caption_text, "msg_id": qris_msg.id}
    except Exception as e:
        return "error", str(e)
    finally:
        await client.disconnect()

async def wait_for_payment(amount: int, qris_msg_id: int):
    if not API_ID or not API_HASH:
        return "error"
    client = TelegramClient(StringSession(TG_SESSION_STR), API_ID, API_HASH)
    await client.connect()
    try:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 300
        cursor = qris_msg_id
        while loop.time() < deadline:
            await asyncio.sleep(5)
            try:
                msgs = await client.get_messages(
                    DEHUYZ_BOT, min_id=cursor, limit=10, reverse=True
                )
            except Exception:
                continue
            for msg in msgs:
                if msg.id > cursor:
                    cursor = msg.id
                text = (msg.text or "").lower()
                if "berhasil" in text and "saldo" in text:
                    if amount in _parse_amounts(text):
                        return "success"
        return "timeout"
    finally:
        await client.disconnect()