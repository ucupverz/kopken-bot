import asyncio
import os
import re
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STR = os.getenv("TG_SESSION_STR", "")

DEHUYZ_BOT = "@dehuyzwaotpbot"

# Keep this whole provider flow shorter than process_qris_payment's 90 second
# safety net, including time spent closing a stalled Telethon connection.
QRIS_REQUEST_TIMEOUT = 80
TELEGRAM_CONNECT_TIMEOUT = 15
TELEGRAM_CALL_TIMEOUT = 10
DEHUYZ_REPLY_TIMEOUT = 20
DEHUYZ_QRIS_TIMEOUT = 45
DEHUYZ_POLL_INTERVAL = 1
QRIS_POLL_INTERVAL = 3
TELEGRAM_DISCONNECT_TIMEOUT = 3

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
            raise asyncio.TimeoutError(detail) from exc

    async def messages_after(message_id, detail):
        return await call_with_timeout(
            client.get_messages(
                DEHUYZ_BOT, min_id=message_id, limit=20, reverse=True
            ),
            TELEGRAM_CALL_TIMEOUT,
            detail,
        )

    def find_custom_button(messages):
        # Telegram's history is not guaranteed to have the command response at
        # index zero. Search all new incoming messages, newest first.
        for message in reversed(list(messages or [])):
            if getattr(message, "out", False):
                continue
            for row in (getattr(message, "buttons", None) or []):
                for button in row:
                    label = (getattr(button, "text", "") or "").lower()
                    if "custom" in label or "nominal" in label:
                        return button
        return None

    try:
        await call_with_timeout(
            client.connect(), TELEGRAM_CONNECT_TIMEOUT,
            "Koneksi Telegram timeout.",
        )
        if not await call_with_timeout(
            client.is_user_authorized(), TELEGRAM_CALL_TIMEOUT,
            "Pemeriksaan sesi Telegram timeout.",
        ):
            return "error", "Session expired."

        command = await call_with_timeout(
            client.send_message(DEHUYZ_BOT, "/topup"), TELEGRAM_CALL_TIMEOUT,
            "Gagal mengirim perintah /topup ke Telegram.",
        )
        command_id = getattr(command, "id", None)
        if command_id is None:
            return "error", "Telegram tidak mengembalikan ID pesan /topup."

        reply_deadline = min(request_deadline, loop.time() + DEHUYZ_REPLY_TIMEOUT)
        custom_button = None
        while loop.time() < reply_deadline:
            messages = await messages_after(
                command_id, "Telegram tidak merespons saat membaca balasan /topup."
            )
            custom_button = find_custom_button(messages)
            if custom_button is not None:
                break
            await asyncio.sleep(min(DEHUYZ_POLL_INTERVAL, max(0, reply_deadline - loop.time())))
        if custom_button is None:
            return "error", "Dehuyz tidak menampilkan tombol Custom/Nominal setelah /topup (timeout 20 detik)."

        await call_with_timeout(
            custom_button.click(), TELEGRAM_CALL_TIMEOUT,
            "Klik tombol Custom/Nominal di Dehuyz timeout.",
        )
        await asyncio.sleep(min(2, max(0, request_deadline - loop.time())))
        amount_message = await call_with_timeout(
            client.send_message(DEHUYZ_BOT, str(amount)), TELEGRAM_CALL_TIMEOUT,
            "Gagal mengirim nominal deposit ke Dehuyz.",
        )
        amount_message_id = getattr(amount_message, "id", None)
        if amount_message_id is None:
            return "error", "Telegram tidak mengembalikan ID pesan nominal deposit."

        qris_msg = None
        photo_deadline = min(request_deadline, loop.time() + DEHUYZ_QRIS_TIMEOUT)
        while loop.time() < photo_deadline:
            messages = await messages_after(
                amount_message_id,
                "Telegram tidak merespons saat menunggu QRIS.",
            )
            for msg in reversed(list(messages or [])):
                if not getattr(msg, "out", False) and getattr(msg, "photo", None):
                    qris_msg = msg
                    break
            if qris_msg is not None:
                break
            await asyncio.sleep(min(QRIS_POLL_INTERVAL, max(0, photo_deadline - loop.time())))
        if not qris_msg:
            return "error", "QRIS tidak muncul dari Dehuyz dalam 45 detik."

        qris_bytes = await call_with_timeout(
            client.download_media(qris_msg, bytes), TELEGRAM_CALL_TIMEOUT,
            "Unduhan gambar QRIS dari Telegram timeout.",
        )
        if not qris_bytes:
            return "error", "Dehuyz mengirim QRIS tetapi gambar tidak dapat diunduh."
        caption_text = qris_msg.text or ""
        return "success", {"image": qris_bytes, "caption": caption_text, "msg_id": qris_msg.id}
    except Exception as e:
        detail = str(e) or e.__class__.__name__
        # Avoid accidentally returning configured credentials if a provider
        # exception includes request/session details.
        for secret in (TG_SESSION_STR, API_HASH):
            if secret and len(secret) >= 8:
                detail = detail.replace(secret, "[redacted]")
        return "error", detail
    finally:
        try:
            await asyncio.wait_for(
                client.disconnect(), timeout=TELEGRAM_DISCONNECT_TIMEOUT
            )
        except Exception:
            pass

async def wait_for_payment(amount: int, qris_msg_id: int):
    if not API_ID or not API_HASH:
        return "error"
    client = TelegramClient(StringSession(TG_SESSION_STR), API_ID, API_HASH)
    try:
        await client.connect()
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
        try:
            await client.disconnect()
        except Exception:
            pass