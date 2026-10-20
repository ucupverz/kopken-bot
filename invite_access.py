"""Shared invitation guard. No provider calls or balance changes before access."""
import logging
import os
import re
import time

from telegram import ReplyKeyboardMarkup

import database as db

logger = logging.getLogger(__name__)
WAITING_KEY = "whatsapp_invite_pending"
BUY_KEYS = ("buy_state", "buy_service", "buy_id", "buy_qty", "buy_total")
MENU_TEXTS = {
    "📱 WhatsApp OTP", "💰 Cek Balance", "💳 Topup Balance", "☕ Cek Nomor Kopken",
    "🔑 Redeem Invitation Code", "🍔 ShopeeFood", "❓ Bantuan", "🔧 Admin Panel",
    "📥 Topup Log", "🆕 User Baru", "🔙 Kembali ke Menu", "🔙 Menu Utama",
}
CODE_PATTERN = re.compile(r"^WA-[A-F0-9]{32}$")


def clear_invite_flow(ctx):
    ctx.user_data.pop(WAITING_KEY, None)
    for key in BUY_KEYS:
        ctx.user_data.pop(key, None)


async def require_whatsapp_access(update, ctx):
    user = update.effective_user
    # Private-only so invitation codes and order details never enter group chats.
    if not user or not update.effective_chat or update.effective_chat.type != "private":
        if update.callback_query:
            await update.callback_query.answer("Buka fitur ini di chat pribadi bot.", show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text("Buka WhatsApp OTP di chat pribadi bot.")
        return False
    if db.has_whatsapp_access(user.id):
        ctx.user_data.pop(WAITING_KEY, None)
        return True
    already_waiting = ctx.user_data.get(WAITING_KEY, False)
    clear_invite_flow(ctx)
    ctx.user_data[WAITING_KEY] = True
    # Remove incompatible prompts, but preserve active orders and unrelated data.
    ctx.user_data.pop("cek_kopken_state", None)
    ctx.user_data.pop("deposit_state", None)
    if update.callback_query:
        await update.callback_query.answer("Akses khusus undangan. Kirim kode dari admin di chat bot.", show_alert=True)
    await update.effective_message.reply_text(
        ("Masih menunggu kode undangan.\n\n" if already_waiting else "WhatsApp OTP khusus pengguna undangan.\n\n")
        + "Kirim kode WA- dari admin untuk membuka akses. Satu kode hanya untuk satu pengguna.\n"
          "Ketik /cancel atau tekan Kembali ke Menu untuk batal.",
        reply_markup=ReplyKeyboardMarkup([["🔙 Kembali ke Menu"]], resize_keyboard=True),
    )
    return False


async def handle_invite_message(update, ctx):
    text = (update.effective_message.text or "").strip()
    if text in MENU_TEXTS:
        # Repeated OTP taps go back through the guard; other menus leave the flow.
        if text != "📱 WhatsApp OTP":
            ctx.user_data.pop(WAITING_KEY, None)
        return False
    if not ctx.user_data.get(WAITING_KEY) and not text.upper().startswith("WA-"):
        return False
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Redeem kode lewat chat pribadi bot.")
        return True
    user_id = update.effective_user.id
    if db.has_whatsapp_access(user_id):
        clear_invite_flow(ctx)
        await update.effective_message.reply_text("Akses WhatsApp OTP kamu sudah aktif.")
        from otp_handlers import cmd_otp_menu
        await cmd_otp_menu(update, ctx)
        return True
    now = time.time()
    if now < ctx.user_data.get("whatsapp_invite_retry_at", 0):
        await update.effective_message.reply_text("Tunggu beberapa detik sebelum mencoba kode lagi.")
        return True
    ctx.user_data[WAITING_KEY] = True
    normalized = text.upper()
    result = db.redeem_whatsapp_invite(normalized, user_id) if CODE_PATTERN.fullmatch(normalized) else "invalid"
    if result in ("granted", "already_allowed"):
        clear_invite_flow(ctx)
        ctx.user_data.pop("whatsapp_invite_retry_at", None)
        await update.effective_message.reply_text("Kode diterima. Akses WhatsApp OTP kamu sudah aktif.")
        from otp_handlers import cmd_otp_menu
        await cmd_otp_menu(update, ctx)
    else:
        ctx.user_data["whatsapp_invite_retry_at"] = now + 3
        await update.effective_message.reply_text(
            "Kode sudah dipakai pengguna lain. Minta kode baru ke admin."
            if result == "used" else "Kode undangan tidak valid. Kirim kode WA- dari admin atau /cancel."
        )
    return True


async def cmd_cancel_invite(update, ctx):
    clear_invite_flow(ctx)
    ctx.user_data.pop("deposit_state", None)
    ctx.user_data.pop("cek_kopken_state", None)
    # Import lazily to avoid a bot_checker/otp_handlers circular dependency.
    await update.effective_message.reply_text(
        "Input dibatalkan. Tekan /start untuk kembali ke menu.",
        reply_markup=ReplyKeyboardMarkup([["🔙 Kembali ke Menu"]], resize_keyboard=True),
    )


async def cmd_generate_invite(update, ctx):
    # Use exactly the existing numeric ADMIN_IDS configuration, never usernames.
    admin_ids = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
    if not update.effective_user or update.effective_user.id not in admin_ids:
        await update.effective_message.reply_text("Perintah ini hanya untuk admin.")
        return
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Generate kode hanya lewat chat pribadi bot.")
        return
    if len(ctx.args) > 1 or (ctx.args and (not ctx.args[0].isascii() or not ctx.args[0].isdigit() or len(ctx.args[0]) > 2)):
        await update.effective_message.reply_text("Format: /generate_invite [1-20]")
        return
    count = int(ctx.args[0]) if ctx.args else 1
    if not 1 <= count <= 20:
        await update.effective_message.reply_text("Jumlah kode harus 1 sampai 20.")
        return
    try:
        codes = db.create_whatsapp_invites(update.effective_user.id, count)
    except Exception:
        logger.exception("Gagal membuat kode undangan WhatsApp")
        await update.effective_message.reply_text("Gagal membuat kode. Coba lagi nanti.")
        return
    await update.effective_message.reply_text(
        "Kode undangan WhatsApp OTP (sekali pakai, satu pengguna per kode):\n\n"
        + "\n".join(codes)
        + "\n\nAkses tersimpan setelah redeem. Kode ini bukan VOC saldo. Simpan sebelum menutup chat.",
        protect_content=False,
    )
