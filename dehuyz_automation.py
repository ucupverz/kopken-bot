import asyncio
import os
import re
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STR = os.getenv("TG_SESSION_STR", "")

DEHUYZ_BOT = "@dehuyzwaotpbot"

QRIS_REQUEST_TIMEOUT = 80
TELEGRAM_CONNECT_TIMEOUT = 15
TELEGRAM_CALL_TIMEOUT = 10
DEHUYZ_REPLY_TIMEOUT = 20
DEHUYZ_QRIS_TIMEOUT = 45
DEHUYZ_POLL_INTERVAL = 1
QRIS_POLL_INTERVAL = 3
TELEGRAM_DISCONNECT_TIMEOUT = 3
_QRIS_REQUEST_LOCK = asyncio.Lock()

def _parse_amounts(text):
    out = set()
    for raw in re.findall(r"\d[\d.,]*", text):
        cleaned = raw.replace(".", "").replace(",", "")
        try:
            out.add(int(cleaned))
        except ValueError:
            continue
    return out

async def request_qris(amount: int):
    # Dehuyz has one shared chat state for this account. Serialize the
    # /topup -> custom button -> amount -> QRIS exchange across users.
    async with _QRIS_REQUEST_LOCK:
        return await _request_qris(amount)


async def _request_qris(amount: int):
    if not API_ID or not API_HASH:
        logger.error("TG_API_ID atau TG_API_HASH kosong!")
        return "error", "TG_API_ID / TG_API_HASH belum di-set!"
    
    logger.info("Memulai request_qris untuk amount: %s", amount)
    client = TelegramClient(StringSession(TG_SESSION_STR), API_ID, API_HASH)
    loop = asyncio.get_running_loop()
    request_deadline = loop.time() + QRIS_REQUEST_TIMEOUT

    async def call_with_timeout(awaitable, timeout, detail):
        remaining = request_deadline - loop.time()
        budget = min(timeout, remaining)
        if budget <= 0:
            raise asyncio.TimeoutError(detail)
        try:
            return await asyncio.wait_for(awaitable, timeout=budget)
        except asyncio.TimeoutError as exc:
            logger.error("Timeout: %s", detail)
            raise asyncio.TimeoutError(detail) from exc

    async def messages_after(message_id, detail):
        return await call_with_timeout(
            client.get_messages(DEHUYZ_BOT, min_id=message_id, limit=20, reverse=True),
            TELEGRAM_CALL_TIMEOUT, detail
        )

    def find_custom_button(messages):
        for message in reversed(list(messages or [])):
            if getattr(message, "out", False): continue
            for row in (getattr(message, "buttons", None) or []):
                for button in row:
                    label = (getattr(button, "text", "") or "").lower()
                    if "custom" in label or "nominal" in label:
                        return button
        return None

    try:
        logger.info("Menghubungkan ke Telegram...")
        await call_with_timeout(client.connect(), TELEGRAM_CONNECT_TIMEOUT, "Koneksi Telegram timeout.")
        
        logger.info("Memeriksa otorisasi sesi...")
        is_auth = await call_with_timeout(client.is_user_authorized(), TELEGRAM_CALL_TIMEOUT, "Pemeriksaan sesi timeout.")
        if not is_auth:
            logger.error("Session tidak valid / expired!")
            return "error", "Session expired."

        logger.info("Mengirim perintah /topup ke %s...", DEHUYZ_BOT)
        command = await call_with_timeout(client.send_message(DEHUYZ_BOT, "/topup"), TELEGRAM_CALL_TIMEOUT, "Gagal mengirim perintah /topup.")
        command_id = getattr(command, "id", None)
        if command_id is None:
            return "error", "Telegram tidak mengembalikan ID pesan /topup."

        logger.info("Menunggu balasan tombol Custom/Nominal...")
        reply_deadline = min(request_deadline, loop.time() + DEHUYZ_REPLY_TIMEOUT)
        custom_button = None
        while loop.time() < reply_deadline:
            messages = await messages_after(command_id, "Telegram tidak merespons saat membaca balasan /topup.")
            custom_button = find_custom_button(messages)
            if custom_button is not None:
                break
            await asyncio.sleep(min(DEHUYZ_POLL_INTERVAL, max(0, reply_deadline - loop.time())))
        
        if custom_button is None:
            logger.error("Tombol Custom/Nominal tidak ditemukan (timeout).")
            return "error", "Dehuyz tidak menampilkan tombol Custom/Nominal (timeout 20 detik)."

        logger.info("Menekan tombol Custom/Nominal...")
        await call_with_timeout(custom_button.click(), TELEGRAM_CALL_TIMEOUT, "Klik tombol Custom timeout.")
        await asyncio.sleep(min(2, max(0, request_deadline - loop.time())))
        
        logger.info("Mengirim nominal %s...", amount)
        amount_message = await call_with_timeout(client.send_message(DEHUYZ_BOT, str(amount)), TELEGRAM_CALL_TIMEOUT, "Gagal mengirim nominal.")
        amount_message_id = getattr(amount_message, "id", None)
        if amount_message_id is None:
            return "error", "Telegram tidak mengembalikan ID pesan nominal."

        logger.info("Menunggu gambar QRIS...")
        qris_msg = None
        photo_deadline = min(request_deadline, loop.time() + DEHUYZ_QRIS_TIMEOUT)
        while loop.time() < photo_deadline:
            messages = await messages_after(amount_message_id, "Telegram tidak merespons saat menunggu QRIS.")
            for msg in reversed(list(messages or [])):
                if not getattr(msg, "out", False) and getattr(msg, "photo", None):
                    qris_msg = msg
                    break
            if qris_msg is not None:
                break
            await asyncio.sleep(min(QRIS_POLL_INTERVAL, max(0, photo_deadline - loop.time())))
        
        if not qris_msg:
            logger.error("QRIS tidak muncul dalam batas waktu.")
            return "error", "QRIS tidak muncul dari Dehuyz dalam 45 detik."

        logger.info("Mengunduh gambar QRIS...")
        qris_bytes = await call_with_timeout(client.download_media(qris_msg, bytes), TELEGRAM_CALL_TIMEOUT, "Unduhan gambar timeout.")
        if not qris_bytes:
            return "error", "Gagal mengunduh gambar QRIS."
        
        caption_text = qris_msg.text or ""
        logger.info("QRIS berhasil didapatkan!")
        return "success", {"image": qris_bytes, "caption": caption_text, "msg_id": qris_msg.id}
    
    except Exception as e:
        logger.exception("Error saat request_qris: %s", str(e))
        detail = str(e) or e.__class__.__name__
        for secret in (TG_SESSION_STR, API_HASH):
            if secret and len(secret) >= 8:
                detail = detail.replace(secret, "[redacted]")
        return "error", detail
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=TELEGRAM_DISCONNECT_TIMEOUT)
        except Exception:
            pass

async def wait_for_payment(amount: int, qris_msg_id: int):
    if not API_ID or not API_HASH:
        return "error"
    client = TelegramClient(StringSession(TG_SESSION_STR), API_ID, API_HASH)
    loop = asyncio.get_event_loop()
    
    PAYMENT_WAIT_TIMEOUT = 300
    PAYMENT_POLL_INTERVAL = 5
    TELEGRAM_CALL_TIMEOUT = 10

    async def call_with_timeout(awaitable, timeout, detail):
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise asyncio.TimeoutError(detail) from exc

    try:
        logger.info("Memantau pembayaran untuk amount %s...", amount)
        await call_with_timeout(client.connect(), TELEGRAM_CONNECT_TIMEOUT, "Koneksi timeout.")
        if not await call_with_timeout(client.is_user_authorized(), TELEGRAM_CALL_TIMEOUT, "Cek sesi timeout."):
            return "error"

        deadline = loop.time() + PAYMENT_WAIT_TIMEOUT
        cursor = qris_msg_id
        
        while loop.time() < deadline:
            await asyncio.sleep(PAYMENT_POLL_INTERVAL)
            try:
                # Some providers edit the QRIS message itself on payment,
                # which a min_id-only query would never return.
                original = await call_with_timeout(
                    client.get_messages(DEHUYZ_BOT, ids=qris_msg_id),
                    TELEGRAM_CALL_TIMEOUT, "Telegram timeout saat membaca status QRIS."
                )
                if isinstance(original, (list, tuple)):
                    original = original[0] if original else None
                if original is not None:
                    text = (getattr(original, "text", "") or "").lower()
                    if "berhasil" in text and "saldo" in text and amount in _parse_amounts(text):
                        logger.info("Pembayaran sukses terdeteksi pada pesan QRIS %s", qris_msg_id)
                        return "success", int(original.id)

                msgs = await call_with_timeout(
                    client.get_messages(DEHUYZ_BOT, min_id=cursor, limit=10, reverse=True),
                    TELEGRAM_CALL_TIMEOUT, "Telegram timeout saat cek pembayaran."
                )
            except Exception:
                continue

            for msg in msgs or []:
                if msg.id > cursor:
                    cursor = msg.id
                text = (msg.text or "").lower()
                
                if "berhasil" in text and "saldo" in text:
                    if amount in _parse_amounts(text):
                        logger.info("Pembayaran sukses dideteksi untuk amount %s", amount)
                        return "success", int(msg.id)
        return "timeout"
    except Exception:
        logger.exception("Error wait_for_payment")
        return "error"
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:
            pass