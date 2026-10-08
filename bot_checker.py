import os
import logging
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode

import database as db

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
NASKY_API_KEY = os.getenv("NASKY_API_KEY", "")
NASKY_BASE = "https://nasky.web.id"

# Admin user IDs (pisah koma di env: "123,456")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

# Harga cek FRESH (dipotong dari saldo user)
PRICE_FRESH = int(os.getenv("PRICE_FRESH", "150"))
PRICE_REGISTERED = 0  # gratis untuk terdaftar
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "5000"))

# QRIS Dana Bisnis kamu (ganti dengan gambar QRIS kamu)
QRIS_IMAGE_PATH = os.getenv("QRIS_IMAGE_PATH", "qris.png")
# Nomor Dana Bisnis kamu (untuk verifikasi manual)
DANA_NUMBER = os.getenv("DANA_NUMBER", "0812xxxxxxx")
DANA_NAME = os.getenv("DANA_NAME", "Nama Kamu")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# State untuk conversation
WAITING_PROOF = 1

# ----------------------------------------------------------------------
# NASKY API
# ----------------------------------------------------------------------

async def nasky_cek_single(nomor: str) -> dict:
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

# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def is_admin(user_id):
    return user_id in ADMIN_IDS

def normalize_phone(text: str) -> str:
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if t.startswith("0"):
        t = "62" + t[1:]
    return t

def is_valid_phone(text: str) -> bool:
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not t.isdigit():
        return False
    if t.startswith("62"):
        return 10 <= len(t) <= 15
    if t.startswith("0"):
        return 10 <= len(t) <= 14
    return False

def get_main_keyboard(is_admin_user=False):
    keyboard = [
        ["☕ Cek Nomor", "💳 Top Up"],
        ["💰 Saldo", "📜 Riwayat"],
        ["❓ Bantuan"]
    ]
    if is_admin_user:
        keyboard.append(["🔧 Admin Panel"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def fmt_rupiah(n):
    return f"Rp {n:,}".replace(",", ".")

# ----------------------------------------------------------------------
# COMMANDS
# ----------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.create_user(user.id, user.username or "", user.first_name or "")

    u = db.get_user(user.id)
    is_admin_user = is_admin(user.id)

    text = (
        f"Halo {user.first_name}! 👋\n\n"
        f"☕ *Bot Cek Nomor Kopi Kenangan*\n\n"
        f"Saldo kamu: *{fmt_rupiah(u['balance'])}*\n\n"
        f"*Harga:*\n"
        f"• Cek nomor FRESH: {fmt_rupiah(PRICE_FRESH)}\n"
        f"• Cek nomor TERDAFTAR: GRATIS\n\n"
        f"Kirim nomor HP langsung untuk cek, atau pakai tombol di bawah."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=get_main_keyboard(is_admin_user))

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Bantuan*\n\n"
        "*Cara cek nomor:*\n"
        "Kirim nomor HP langsung, contoh: `081234567890`\n"
        "Atau kirim banyak nomor (pisah baris) untuk cek massal.\n\n"
        "*Harga:*\n"
        f"• Cek nomor FRESH: {fmt_rupiah(PRICE_FRESH)}\n"
        "• Cek nomor TERDAFTAR: GRATIS\n"
        "• Cek nomor INVALID: GRATIS\n\n"
        "*Top up:*\n"
        "Kirim /topup atau klik tombol 💳 Top Up.\n"
        f"Minimal top up: {fmt_rupiah(MIN_TOPUP)}\n\n"
        "*Commands:*\n"
        "/start - Menu utama\n"
        "/saldo - Cek saldo\n"
        "/topup - Top up saldo\n"
        "/riwayat - Riwayat transaksi\n"
        "/help - Bantuan ini"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_saldo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    if not u:
        db.create_user(user.id, user.username or "", user.first_name or "")
        u = db.get_user(user.id)

    text = (
        f"💰 *Saldo Kamu*\n\n"
        f"Saldo: *{fmt_rupiah(u['balance'])}*\n"
        f"Total top up: {fmt_rupiah(u['total_topup'])}\n"
        f"Total cek: {u['total_cek']}x\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_riwayat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    txs = db.get_user_transactions(user.id, limit=10)

    if not txs:
        await update.message.reply_text("📜 Belum ada riwayat transaksi.")
        return

    lines = ["📜 *Riwayat Transaksi (10 terakhir)*\n"]
    for t in txs:
        import datetime
        dt = datetime.datetime.fromtimestamp(t["timestamp"]).strftime("%d/%m %H:%M")
        icon = "💳" if t["type"] == "topup" else "☕"
        sign = "+" if t["type"] == "topup" else "-"
        lines.append(f"{icon} {dt} {sign}{fmt_rupiah(t['amount'])} [{t['status']}]")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# ----------------------------------------------------------------------
# TOP UP
# ----------------------------------------------------------------------

async def cmd_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        f"💳 *Top Up Saldo*\n\n"
        f"Minimal top up: *{fmt_rupiah(MIN_TOPUP)}*\n\n"
        f"*Cara top up:*\n"
        f"1. Transfer ke Dana Bisnis:\n"
        f"   📱 Nomor: `{DANA_NUMBER}`\n"
        f"   👤 Nama: {DANA_NAME}\n"
        f"2. Kirim screenshot bukti transfer ke bot ini\n"
        f"3. Tunggu admin verifikasi (maks 24 jam)\n"
        f"4. Saldo otomatis masuk setelah di-approve\n\n"
        f"Ketentuan:\n"
        f"• Screenshot harus jelas (jumlah + tanggal)\n"
        f"• Sertakan nominal di caption foto\n"
        f"• Top up di bawah {fmt_rupiah(MIN_TOPUP)} akan ditolak"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def topup_amount_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle user kirim foto bukti transfer."""
    user = update.effective_user
    photo = update.message.photo[-1]
    caption = update.message.caption or ""

    # Coba parse amount dari caption
    import re
    amount_match = re.search(r"(\d{3,})", caption.replace(".", "").replace(",", ""))
    if not amount_match:
        await update.message.reply_text(
            f"❌ Caption tidak ada nominal.\n\n"
            f"Kirim ulang foto dengan caption angka, contoh:\n`10000`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    amount = int(amount_match.group(1))
    if amount < MIN_TOPUP:
        await update.message.reply_text(
            f"❌ Nominal minimal {fmt_rupiah(MIN_TOPUP)}.\n"
            f"Nominal kamu: {fmt_rupiah(amount)}"
        )
        return

    # Simpan pending topup
    topup_id = db.create_pending_topup(user.id, amount, photo.file_id)
    db.add_transaction(user.id, "topup_pending", amount, "pending",
                       f"Pending #{topup_id}")

    await update.message.reply_text(
        f"✅ *Bukti diterima!*\n\n"
        f"Nominal: *{fmt_rupiah(amount)}*\n"
        f"ID: `#{topup_id}`\n\n"
        f"Tunggu admin verifikasi. Kamu akan dapat notifikasi setelah di-approve.",
        parse_mode=ParseMode.MARKDOWN
    )

    # Notif ke admin
    u = db.get_user(user.id)
    for admin_id in ADMIN_IDS:
        try:
            await ctx.bot.send_photo(
                chat_id=admin_id,
                photo=photo.file_id,
                caption=(
                    f"🔔 *Top Up Baru*\n\n"
                    f"ID: `#{topup_id}`\n"
                    f"User: {user.first_name} (@{user.username or '-'})\n"
                    f"User ID: `{user.id}`\n"
                    f"Nominal: *{fmt_rupiah(amount)}*\n\n"
                    f"Approve: `/approve {topup_id}`\n"
                    f"Reject: `/reject {topup_id}`"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Notif admin gagal: {e}")

# ----------------------------------------------------------------------
# CEK NOMOR
# ----------------------------------------------------------------------

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    # Buat user kalau belum ada
    if not db.get_user(user.id):
        db.create_user(user.id, user.username or "", user.first_name or "")

    u = db.get_user(user.id)

    # Handle tombol menu
    if text == "☕ Cek Nomor":
        await update.message.reply_text(
            "Kirim nomor HP yang mau dicek, contoh:\n`081234567890`\n\n"
            "Bisa juga banyak nomor sekaligus (pisah baris).",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    if text == "💳 Top Up":
        await cmd_topup(update, ctx)
        return
    if text == "💰 Saldo":
        await cmd_saldo(update, ctx)
        return
    if text == "📜 Riwayat":
        await cmd_riwayat(update, ctx)
        return
    if text == "❓ Bantuan":
        await cmd_help(update, ctx)
        return
    if text == "🔧 Admin Panel" and is_admin(user.id):
        await cmd_admin(update, ctx)
        return

    # Handle cek nomor
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    if len(lines) == 1 and is_valid_phone(lines[0]):
        # Single check
        if u["balance"] < PRICE_FRESH:
            await update.message.reply_text(
                f"❌ *Saldo kurang!*\n\n"
                f"Saldo kamu: {fmt_rupiah(u['balance'])}\n"
                f"Harga cek: {fmt_rupiah(PRICE_FRESH)}\n\n"
                f"Top up dulu pakai /topup",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        msg = await update.message.reply_text("🔍 Mengecek...")
        result = await nasky_cek_single(normalize_phone(lines[0]))

        if not result.get("success"):
            await msg.edit_text(f"❌ Error: {result.get('detail', 'Unknown')}")
            return

        d = result["data"]
        status = d["status"]
        phone = d["phone"]

        if status == "fresh":
            db.update_balance(user.id, -PRICE_FRESH)
            db.add_transaction(user.id, "cek", PRICE_FRESH, "success", f"FRESH {phone}")
            db.add_cek_count(user.id)
            reply = (
                f"✅ *FRESH* (belum terdaftar)\n\n"
                f"📱 Nomor: `{phone}`\n"
                f"💰 Dipotong: {fmt_rupiah(PRICE_FRESH)}\n"
                f"💳 Saldo tersisa: {fmt_rupiah(u['balance'] - PRICE_FRESH)}"
            )
        elif status == "registered":
            db.add_transaction(user.id, "cek", 0, "success", f"REG {phone}")
            db.add_cek_count(user.id)
            reply = (
                f"⚠️ *TERDAFTAR*\n\n"
                f"📱 Nomor: `{phone}`\n"
                f"👤 Nama: {d.get('customer_name') or '-'}\n"
                f"💰 Dipotong: GRATIS\n"
                f"💳 Saldo: {fmt_rupiah(u['balance'])}"
            )
        else:
            reply = f"❓ Status tidak dikenal: `{status}`"

        await msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)
        return

    # Bulk check
    valid = [normalize_phone(l) for l in lines if is_valid_phone(l)]
    if not valid:
        await update.message.reply_text("❌ Tidak ada nomor valid.")
        return

    if len(valid) > 2000:
        await update.message.reply_text("❌ Max 2000 nomor per request.")
        return

    # Cek saldo cukup
    max_cost = len(valid) * PRICE_FRESH
    if u["balance"] < max_cost:
        # Hitung berapa yang bisa dicek
        max_cek = u["balance"] // PRICE_FRESH
        if max_cek == 0:
            await update.message.reply_text(
                f"❌ Saldo kurang.\nSaldo: {fmt_rupiah(u['balance'])}\nHarga: {fmt_rupiah(PRICE_FRESH)}/cek"
            )
            return

    msg = await update.message.reply_text(f"🔍 Mengecek {len(valid)} nomor...")
    result = await nasky_cek_bulk(valid)

    if not result.get("success"):
        await msg.edit_text(f"❌ Error: {result.get('detail', 'Unknown')}")
        return

    # Potong saldo hanya untuk yang FRESH
    fresh_count = result.get("fresh", 0)
    cost = fresh_count * PRICE_FRESH

    if u["balance"] < cost:
        # Saldo tidak cukup untuk semua fresh — batalkan
        await msg.edit_text(
            f"⚠️ Saldo tidak cukup!\n\n"
            f"Hasil: {fresh_count} fresh × {fmt_rupiah(PRICE_FRESH)} = {fmt_rupiah(cost)}\n"
            f"Saldo kamu: {fmt_rupiah(u['balance'])}\n\n"
            f"Top up dulu: /topup"
        )
        return

    db.update_balance(user.id, -cost)
    db.add_transaction(user.id, "cek", cost, "success", f"Bulk {fresh_count} fresh")
    db.add_cek_count(user.id)

    # Bikin file hasil
    lines_out = ["=" * 40, "HASIL CEK KOPI KENANGAN", "=" * 40]
    lines_out.append(f"Total: {result.get('total', 0)}")
    lines_out.append(f"Fresh: {fresh_count}")
    lines_out.append(f"Terdaftar: {result.get('registered', 0)}")
    lines_out.append(f"Invalid: {result.get('invalid', 0)}")
    lines_out.append(f"Dipotong: {fmt_rupiah(cost)}")
    lines_out.append(f"Saldo sisa: {fmt_rupiah(u['balance'] - cost)}")
    lines_out.append("")
    lines_out.append("--- FRESH ---")
    for n in result.get("fresh_numbers", []):
        lines_out.append(n)
    lines_out.append("")
    lines_out.append("--- TERDAFTAR ---")
    for d in result.get("details", []):
        if d.get("status") == "registered":
            lines_out.append(f"{d.get('phone')} | {d.get('customer_name') or '-'}")

    file_bytes = "\n".join(lines_out).encode("utf-8")

    await msg.edit_text(
        f"📊 *Hasil Cek Massal*\n\n"
        f"Total: {result.get('total', 0)}\n"
        f"✅ Fresh: {fresh_count}\n"
        f"⚠️ Terdaftar: {result.get('registered', 0)}\n"
        f"❌ Invalid: {result.get('invalid', 0)}\n\n"
        f"💰 Dipotong: {fmt_rupiah(cost)}\n"
        f"💳 Saldo sisa: {fmt_rupiah(u['balance'] - cost)}",
        parse_mode=ParseMode.MARKDOWN
    )
    await update.message.reply_document(document=file_bytes, filename="hasil_cek.txt")

# ----------------------------------------------------------------------
# ADMIN COMMANDS
# ----------------------------------------------------------------------

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return

    stats = db.get_stats()
    text = (
        f"🔧 *Admin Panel*\n\n"
        f"👥 Total users: {stats['total_users']}\n"
        f"💰 Total saldo user: {fmt_rupiah(stats['total_balance'])}\n"
        f"💳 Total top up masuk: {fmt_rupiah(stats['total_topup'])}\n"
        f"☕ Total cek: {stats['total_cek']}\n"
        f"⏳ Pending top up: {stats['pending']}\n\n"
        f"*Commands:*\n"
        f"/pending - Lihat top up pending\n"
        f"/approve <id> - Approve top up\n"
        f"/reject <id> - Reject top up\n"
        f"/addsaldo <user_id> <amount> - Tambah saldo manual\n"
        f"/broadcast <pesan> - Broadcast ke semua user\n"
        f"/stats - Statistik"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    pendings = db.get_pending_topups()
    if not pendings:
        await update.message.reply_text("✅ Tidak ada top up pending.")
        return

    for p in pendings:
        u = db.get_user(p["user_id"])
        caption = (
            f"🔔 *Top Up #{p['id']}*\n\n"
            f"User: {u['first_name'] if u else '?'} (@{u['username'] if u else '-'})\n"
            f"User ID: `{p['user_id']}`\n"
            f"Nominal: *{fmt_rupiah(p['amount'])}*\n\n"
            f"Approve: `/approve {p['id']}`\n"
            f"Reject: `/reject {p['id']}`"
        )
        await ctx.bot.send_photo(
            chat_id=update.effective_user.id,
            photo=p["photo_file_id"],
            caption=caption,
            parse_mode=ParseMode.MARKDOWN
        )

async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /approve <topup_id>")
        return
    try:
        topup_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID tidak valid.")
        return

    row = db.approve_topup(topup_id, update.effective_user.id)
    if not row:
        await update.message.reply_text("❌ Top up tidak ditemukan atau sudah di-proses.")
        return

    db.add_topup(row["user_id"], row["amount"])
    db.add_transaction(row["user_id"], "topup", row["amount"], "approved",
                       f"Topup #{topup_id} approved")

    await update.message.reply_text(
        f"✅ Top up #{topup_id} di-approve.\n"
        f"Saldo user bertambah {fmt_rupiah(row['amount'])}."
    )

    try:
        await ctx.bot.send_message(
            chat_id=row["user_id"],
            text=(
                f"✅ *Top Up Berhasil!*\n\n"
                f"Nominal: {fmt_rupiah(row['amount'])}\n"
                f"Saldo kamu sekarang: {fmt_rupiah(db.get_user(row['user_id'])['balance'])}\n\n"
                f"Terima kasih! 🙏"
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Notif user gagal: {e}")

async def cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /reject <topup_id>")
        return
    try:
        topup_id = int(args[0])
    except ValueError:
        return

    db.reject_topup(topup_id, update.effective_user.id)
    db.add_transaction(0, "topup", 0, "rejected", f"Topup #{topup_id} rejected")
    await update.message.reply_text(f"❌ Top up #{topup_id} ditolak.")

async def cmd_addsaldo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /addsaldo <user_id> <amount>")
        return
    try:
        uid = int(args[0])
        amt = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Format salah.")
        return

    u = db.get_user(uid)
    if not u:
        await update.message.reply_text("❌ User tidak ditemukan.")
        return

    db.add_topup(uid, amt)
    db.add_transaction(uid, "topup", amt, "approved", "Manual by admin")
    new_balance = db.get_user(uid)["balance"]

    await update.message.reply_text(f"✅ Saldo user `{uid}` bertambah {fmt_rupiah(amt)}. Total: {fmt_rupiah(new_balance)}", parse_mode=ParseMode.MARKDOWN)

    try:
        await ctx.bot.send_message(
            chat_id=uid,
            text=f"✅ Saldo kamu bertambah {fmt_rupiah(amt)}.\nSaldo sekarang: {fmt_rupiah(new_balance)}",
        )
    except Exception:
        pass

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    msg = " ".join(ctx.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast <pesan>")
        return
    ids = db.get_all_user_ids()
    sent = 0
    for uid in ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=f"📢 {msg}")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Broadcast terkirim ke {sent}/{len(ids)} user.")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = db.get_stats()
    text = (
        f"📊 *Statistik Bot*\n\n"
        f"👥 Users: {s['total_users']}\n"
        f"💰 Total saldo: {fmt_rupiah(s['total_balance'])}\n"
        f"💳 Total top up: {fmt_rupiah(s['total_topup'])}\n"
        f"☕ Total cek: {s['total_cek']}\n"
        f"⏳ Pending: {s['pending']}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ----------------------------------------------------------------------
# ERROR HANDLER
# ----------------------------------------------------------------------

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {ctx.error}", exc_info=ctx.error)

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN belum di-set")
        return
    if not NASKY_API_KEY:
        print("❌ NASKY_API_KEY belum di-set")
        return
    if not ADMIN_IDS:
        print("⚠️ ADMIN_IDS kosong, fitur admin tidak akan jalan")

    db.init_db()
    print("✅ Database initialized")

    app = Application.builder().token(BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("riwayat", cmd_riwayat))

    # Admin commands
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("addsaldo", cmd_addsaldo))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Photo handler (bukti top up)
    app.add_handler(MessageHandler(filters.PHOTO, topup_amount_received))

    # Text handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    print("🤖 Bot jalan... Ctrl+C untuk stop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()