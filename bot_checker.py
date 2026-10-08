import os
import logging
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "ISI_BOT_TOKEN_KAMU_DISINI")
NASKY_API_KEY = os.getenv("NASKY_API_KEY", "nasky_0286c54e38a76a9e73094e4e2229d288")
NASKY_BASE = "https://nasky.web.id"
MAX_BULK = 2000

# Admin user IDs yang boleh pakai fitur bulk (pisah koma, contoh: "123456789,987654321")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# NASKY CLIENT
# ----------------------------------------------------------------------

async def nasky_cek_single(nomor: str) -> dict:
    """Cek satu nomor ke API Nasky."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(
                f"{NASKY_BASE}/api/cek",
                headers={"X-API-Key": NASKY_API_KEY},
                json={"code": "kopken", "nomor": nomor}
            )
            return r.json()
        except Exception as e:
            return {"success": False, "detail": f"Network error: {e}"}


async def nasky_cek_bulk(nomor_list: list) -> dict:
    """Cek massal ke API Nasky (max 2000)."""
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            r = await client.post(
                f"{NASKY_BASE}/api/cek-bulk",
                headers={"X-API-Key": NASKY_API_KEY},
                json={"code": "kopken", "nomor": nomor_list}
            )
            return r.json()
        except Exception as e:
            return {"success": False, "detail": f"Network error: {e}"}


async def nasky_get_balance() -> dict:
    """Ambil info saldo dari Nasky (via user/me)."""
    # Endpoint ini butuh user_id + unique_code, bukan API key.
    # Untuk sementara kita skip, atau isi di env kalau kamu punya.
    user_id = os.getenv("NASKY_USER_ID", "")
    unique_code = os.getenv("NASKY_UNIQUE_CODE", "")
    if not user_id or not unique_code:
        return {"success": False, "detail": "NASKY_USER_ID / NASKY_UNIQUE_CODE belum di-set"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(
                f"{NASKY_BASE}/api/user/me",
                params={"user_id": user_id, "unique_code": unique_code}
            )
            return r.json()
        except Exception as e:
            return {"success": False, "detail": f"Network error: {e}"}

# ----------------------------------------------------------------------
# HANDLERS
# ----------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "☕ *Kopi Kenangan Checker Bot*\n\n"
        "Bot ini untuk cek apakah nomor HP sudah terdaftar di Kopi Kenangan.\n\n"
        "*Cara pakai:*\n"
        "• Kirim nomor HP langsung → cek single\n"
        "• Kirim banyak nomor (pisah baris) → cek massal\n"
        "• /saldo → cek saldo Nasky\n"
        "• /help → bantuan\n\n"
        "_Contoh:_\n"
        "`081234567890`\n"
        "atau\n"
        "`081234567890`\n"
        "`6281234567891`"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *Bantuan*\n\n"
        "*Single check:*\n"
        "Kirim 1 nomor HP, contoh: `081234567890`\n\n"
        "*Bulk check:*\n"
        "Kirim banyak nomor, satu per baris.\n"
        f"Max {MAX_BULK} nomor per request.\n\n"
        "*Format nomor yang diterima:*\n"
        "• `081234567890`\n"
        "• `6281234567890`\n"
        "• `+6281234567890`\n\n"
        "*Output:*\n"
        "• ✅ *FRESH* = belum terdaftar\n"
        "• ⚠️ *TERDAFTAR* = sudah terdaftar\n"
        "• ❌ *INVALID* = format nomor salah\n\n"
        "*Biaya:*\n"
        "Rp 100 per nomor FRESH (dipotong dari saldo Nasky).\n"
        "Nomor terdaftar & invalid gratis."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_saldo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("💳 Mengambil saldo...")
    result = await nasky_get_balance()

    if not result.get("success"):
        await msg.edit_text(
            f"❌ *Error:*\n`{result.get('detail', 'Unknown')}`\n\n"
            "Pastikan `NASKY_USER_ID` dan `NASKY_UNIQUE_CODE` sudah di-set di environment.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    d = result.get("data", {})
    text = (
        f"💳 *Info Saldo*\n\n"
        f"👤 User ID: `{d.get('user_id', '-')}`\n"
        f"🏷️ Unique Code: `{d.get('unique_code', '-')}`\n"
        f"💰 Saldo: *Rp {d.get('balance', 0):,}*\n"
    )
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


def format_single_result(data: dict) -> str:
    """Format hasil single check jadi teks Telegram."""
    if not data.get("success"):
        detail = data.get("detail", "Unknown error")
        return f"❌ *Error:*\n`{detail}`"

    d = data.get("data", {})
    phone = d.get("phone", "-")
    status = d.get("status", "-")
    name = d.get("customer_name")
    msg = d.get("message", "")

    if status == "fresh":
        return (
            f"✅ *FRESH* (belum terdaftar)\n\n"
            f"📱 Nomor: `{phone}`\n"
            f"📝 Status: {msg}\n"
            f"💰 Charged: Rp {data.get('charged', 0):,}\n"
            f"💳 Saldo: Rp {data.get('balance_after', 0):,}"
        )
    elif status == "registered":
        return (
            f"⚠️ *TERDAFTAR*\n\n"
            f"📱 Nomor: `{phone}`\n"
            f"👤 Nama: {name or '-'}\n"
            f"📝 Status: {msg}\n"
            f"💰 Charged: Rp {data.get('charged', 0):,}\n"
            f"💳 Saldo: Rp {data.get('balance_after', 0):,}"
        )
    else:
        return f"❓ Status tidak dikenal: `{status}`"


def format_bulk_result(data: dict) -> str:
    """Format hasil bulk check jadi ringkasan."""
    if not data.get("success"):
        return f"❌ *Error:*\n`{data.get('detail', 'Unknown')}`"

    total = data.get("total", 0)
    fresh = data.get("fresh", 0)
    reg = data.get("registered", 0)
    inv = data.get("invalid", 0)
    unch = data.get("unchecked", 0)
    charged = data.get("charged", 0)
    bal = data.get("balance_after", 0)

    return (
        f"📊 *Hasil Cek Massal*\n\n"
        f"Total: {total}\n"
        f"✅ Fresh: {fresh}\n"
        f"⚠️ Terdaftar: {reg}\n"
        f"❌ Invalid: {inv}\n"
        f"⏸️ Belum dicek: {unch}\n\n"
        f"💰 Charged: Rp {charged:,}\n"
        f"💳 Saldo: Rp {bal:,}"
    )


def make_bulk_file(data: dict) -> bytes:
    """Bikin file hasil bulk."""
    lines = []
    lines.append("=" * 40)
    lines.append("HASIL CEK KOPI KENANGAN")
    lines.append("=" * 40)
    lines.append(f"Total: {data.get('total', 0)}")
    lines.append(f"Fresh: {data.get('fresh', 0)}")
    lines.append(f"Terdaftar: {data.get('registered', 0)}")
    lines.append(f"Invalid: {data.get('invalid', 0)}")
    lines.append(f"Charged: Rp {data.get('charged', 0)}")
    lines.append(f"Saldo akhir: Rp {data.get('balance_after', 0)}")
    lines.append("")

    lines.append("--- FRESH (belum terdaftar) ---")
    for n in data.get("fresh_numbers", []):
        lines.append(n)
    lines.append("")

    lines.append("--- TERDAFTAR ---")
    for d in data.get("details", []):
        if d.get("status") == "registered":
            lines.append(f"{d.get('phone')} | {d.get('customer_name') or '-'}")
    lines.append("")

    lines.append("--- INVALID ---")
    for n in data.get("invalid_numbers", []):
        lines.append(n)
    lines.append("")

    lines.append("--- BELUM DICEK ---")
    for n in data.get("unchecked_numbers", []):
        lines.append(n)

    return "\n".join(lines).encode("utf-8")


def normalize_phone(text: str) -> str:
    """Normalisasi nomor HP ke format 62xxx."""
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if t.startswith("0"):
        t = "62" + t[1:]
    return t


def is_valid_phone_format(text: str) -> bool:
    """Cek apakah teks adalah nomor HP Indonesia."""
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not t.isdigit():
        return False
    if t.startswith("62"):
        return 10 <= len(t) <= 15
    if t.startswith("0"):
        return 10 <= len(t) <= 14
    return False


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle input user: single atau bulk."""
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    if not text:
        return

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Single check
    if len(lines) == 1 and is_valid_phone_format(lines[0]):
        msg = await update.message.reply_text("🔍 Mengecek...")
        result = await nasky_cek_single(normalize_phone(lines[0]))
        reply = format_single_result(result)
        await msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)
        return

    # Bulk check
    valid_lines = [normalize_phone(l) for l in lines if is_valid_phone_format(l)]
    if len(valid_lines) == 0:
        await update.message.reply_text(
            "❌ Tidak ada nomor valid.\n"
            "Format: `081234567890` atau `6281234567890`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if len(valid_lines) > MAX_BULK:
        await update.message.reply_text(f"❌ Max {MAX_BULK} nomor per request.")
        return

    # Batasi bulk hanya untuk admin (opsional, buat anti-abuse)
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "🔒 Fitur bulk hanya untuk admin.\n"
            "Kirim 1 nomor saja untuk cek single."
        )
        return

    msg = await update.message.reply_text(
        f"🔍 Mengecek {len(valid_lines)} nomor...\nMohon tunggu."
    )

    result = await nasky_cek_bulk(valid_lines)
    summary = format_bulk_result(result)
    await msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN)

    if result.get("success"):
        file_bytes = make_bulk_file(result)
        await update.message.reply_document(
            document=file_bytes,
            filename="hasil_cek.txt",
            caption="📁 Detail hasil cek"
        )


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {ctx.error}")


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    if BOT_TOKEN == "ISI_BOT_TOKEN_KAMU_DISINI":
        print("⚠️ Set BOT_TOKEN dulu (dari @BotFather)")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 Bot jalan... Ctrl+C untuk stop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()