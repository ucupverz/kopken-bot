import os
import asyncio
import logging
import random
import string
import re
import time
import httpx
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

import database as db
import wahub_client
from otp_handlers import (
    cmd_otp_menu, process_direct_buy, process_deposit, 
    cmd_deposit, get_otp_keyboard, handle_cekstatus_callback,
    BIAYA_CEK_FRESH
)

from sfood_handlers import (
    cmd_sfood, cmd_sfood_list, cmd_sfood_stop,
    cmd_sfood_stopall, handle_sfood_message
)
from sfood_tracker import tracker_worker
from sfood_admin_handlers import (
    cmd_sfood_users, cmd_sfood_leaderboard, cmd_sfood_userinfo
)
from sfood_telemetry import (
    cmd_telemetry, cmd_pantau_on, cmd_pantau_off,
    cmd_jarak_set, cmd_stuck_set,
    cmd_sfood_metrics, cmd_lokasi_saya, handle_live_location
)
from tarif_handlers import cmd_tarif, cmd_tarif_cek, cmd_tarif_stats

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
NASKY_API_KEY = os.getenv("NASKY_API_KEY", "")
NASKY_BASE = "https://nasky.web.id"
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DEFAULT_VOC_QUOTA = int(os.getenv("DEFAULT_VOC_QUOTA", "3"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def is_admin(user_id):
    return user_id in ADMIN_IDS


def normalize_phone(text):
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if t.startswith("0"):
        t = "62" + t[1:]
    return t


def format_phone_display(phone_62):
    if phone_62.startswith("62"):
        return "0" + phone_62[2:]
    return phone_62


def is_valid_phone(text):
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not t.isdigit():
        return False
    if t.startswith("62"):
        return 10 <= len(t) <= 15
    if t.startswith("0"):
        return 10 <= len(t) <= 14
    return False


def gen_voc_code():
    chars = string.ascii_uppercase + string.digits
    return f"VOC-{''.join(random.choices(chars, k=6))}"


def sanitize_voc_input(text):
    t = text.strip().upper().replace(" ", "")
    if not re.match(r"^[A-Z0-9_-]+$", t):
        return None
    if len(t) < 3 or len(t) > 50:
        return None
    return t


def main_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Kopken", "📊 Kuota Saya"],
        ["📱 WhatsApp OTP", "🔑 Pakai Kode VOC"],
        ["🍔 ShopeeFood", "📊 Tarif ShopeeFood"],
        ["📡 Pantau Kurir", "❓ Bantuan"]
    ], resize_keyboard=True)


def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Kopken", "📊 Kuota Saya"],
        ["📱 WhatsApp OTP", "🔑 Pakai Kode VOC"],
        ["🍔 ShopeeFood", "📊 Tarif ShopeeFood"],
        ["📡 Pantau Kurir", "🔧 Admin Panel"],
        ["❓ Bantuan"]
    ], resize_keyboard=True)


async def nasky_cek_single(nomor):
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


async def cmd_start(update, ctx):
    user = update.effective_user
    db.create_user(user.id, user.username or "", user.first_name or "")
    u = db.get_user(user.id)
    kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    text = (
        f"Halo {user.first_name}! 👋\n\n"
        f"☕ *Bot Multi Fungsi*\n\n"
        f"📊 Kuota kamu: *{u['quota']}x cek*\n"
        f"💰 Saldo OTP kamu: *Rp {saldo:,}*\n\n"
        f"*Fitur:*\n"
        f"• ☕ Cek Nomor Kopi Kenangan\n"
        f"• 🍔 ShopeeFood Tracker\n"
        f"• 📊 Tarif ShopeeFood\n"
        f"• 📡 Pantau Kurir\n"
        f"• 📱 Beli Nomor OTP\n"
        f"• 🔑 Redeem kode VOC\n\n"
        f"Ketik `/help` untuk bantuan."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_help(update, ctx):
    text = (
        "📖 *Bantuan*\n\n"
        "*☕ Cek Nomor Kopi Kenangan:*\n"
        "• Kirim nomor HP (1 atau banyak, pisah baris)\n\n"
        "*🍔 ShopeeFood Tracker:*\n"
        "• Kirim link tracker + label\n"
        "• `/sfood` — Menu tracker\n\n"
        "*📡 Pantau Kurir:*\n"
        "• `/telemetry` — Menu Pantau Kurir\n"
        "• `/sfood_metrics <order_id>` — Statistik order\n\n"
        "*📊 Tarif:*\n"
        "• `/tarif <angka>` — Input biaya layanan\n\n"
        "*📱 OTP:*\n"
        "• Klik tombol WhatsApp OTP di keyboard\n\n"
        "*Commands:*\n"
        "/start, /help, /kuota, /sfood, /telemetry, /tarif"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_kuota(update, ctx):
    user = update.effective_user
    u = db.get_user(user.id)
    if not u:
        db.create_user(user.id, user.username or "", user.first_name or "")
        u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    await update.message.reply_text(
        f"📊 *Kuota Kamu*\n\n"
        f"Sisa kuota: *{u['quota']}x cek*\n"
        f"Total cek: {u['total_cek']}x\n"
        f"💰 Saldo OTP: *Rp {saldo:,}*",
        parse_mode=ParseMode.MARKDOWN
    )


async def handle_message(update, ctx):
    text = (update.message.text or "").strip()
    user = update.effective_user

    if not db.get_user(user.id):
        db.create_user(user.id, user.username or "", user.first_name or "")
    u = db.get_user(user.id)

    # Cek State Percakapan (Direct Buy / Deposit)
    if ctx.user_data.get('buy_state') == 'WAITING_QTY' and text.isdigit():
        qty = int(text)
        await process_direct_buy(update, ctx, qty)
        return
        
    if ctx.user_data.get('deposit_state') == 'WAITING_AMOUNT' and text.isdigit():
        amount = int(text)
        await process_deposit(update, ctx, amount)
        return

    if "shopeefood.co.id/tracker" in text or "shptools.my.id/sfood" in text:
        await handle_sfood_message(update, ctx)
        return

    # --- HANDLER MENU UTAMA ---
    if text == "☕ Cek Kopken":
        if u["quota"] <= 0:
            await update.message.reply_text("❌ Kuota habis. Kirim VOC atau minta admin.")
        else:
            await update.message.reply_text("Kirim nomor HP yang mau dicek, contoh:\n`081234567890`", parse_mode=ParseMode.MARKDOWN)
        return

    if text == "📊 Kuota Saya":
        await cmd_kuota(update, ctx)
        return

    if text == "📱 WhatsApp OTP":
        await cmd_otp_menu(update, ctx)
        return
        
    if text == "🔑 Pakai Kode VOC":
        await update.message.reply_text("Kirim kode VOC kamu.\n\nFormat: `VOC-XXXXXX`", parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🍔 ShopeeFood":
        await cmd_sfood(update, ctx)
        return

    if text == "📋 List Order":
        await cmd_sfood_list(update, ctx)
        return

    if text == "❌ Stop Order":
        await update.message.reply_text(
            "Kirim: <code>/sfood_stop &lt;order_id&gt;</code>\n\nAtau: /sfood_stopall",
            parse_mode=ParseMode.HTML)
        return

    if text == "📊 Tarif ShopeeFood":
        await update.message.reply_text(
            "📊 <b>Tarif ShopeeFood</b>\n\n"
            "<code>/tarif &lt;angka&gt;</code> — Input biaya\n"
            "<code>/tarif_cek</code> — Cek status\n"
            "<code>/tarif_stats</code> — Statistik",
            parse_mode=ParseMode.HTML)
        return

    if text == "📡 Pantau Kurir":
        await cmd_telemetry(update, ctx)
        return

    if text == "❓ Bantuan":
        await cmd_help(update, ctx)
        return

    if text == "🔧 Admin Panel" and is_admin(user.id):
        await cmd_admin(update, ctx)
        return

    # --- HANDLER MENU OTP (KEYBOARD BAWAH) ---
    if text == "🔙 Kembali ke Menu" or text == "🔙 Menu Utama":
        kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
        saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
        await update.message.reply_text(
            f"🏠 <b>Menu Utama</b>\n\n📊 Kuota: <b>{u['quota']}x cek</b>\n💰 Saldo OTP: <b>Rp {saldo:,}</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if text == "💰 Deposit Saldo":
        await cmd_deposit(update, ctx)
        return
        
    if text == "📋 Cek Pesanan":
        # Cegah cek pesanan kalau ada proses lain
        if ctx.user_data.get('is_processing'):
            await update.message.reply_text("⏳ Mohon tunggu, pesanan sebelumnya sedang diproses.")
            return
            
        last_order = ctx.user_data.get('last_order')
        if not last_order:
            await update.message.reply_text("❌ Tidak ada pesanan aktif.")
            return
            
        status = "Menunggu OTP"
        if last_order.get('status') == 'success':
            status = "Selesai (OTP Diterima)"
        
        msg = (
            f"📋 <b>Detail Pesanan Aktif</b>\n\n"
            f"Layanan: {last_order['service']}\n"
            f"Nomor: <code>{last_order['phone']}</code>\n"
            f"Status: {status}\n\n"
            f"Gunakan tombol Batal atau Kirim Ulang OTP jika diperlukan."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return
    
    # --- HANDLER DETEKSI TOMBOL LAYANAN (🛒) ---
    if text.startswith("🛒 "):
        # Regex ini lebih kuat buat nangkep nama layanan dengan spasi (misal: "Kopken Filter")
        match = re.match(r"🛒\s+(.+?)\s+\(\d+\)\s+-\s+[\d,]+", text)
        service_name = match.group(1).strip() if match else None
        
        if not service_name:
            await update.message.reply_text("❌ Format tombol tidak dikenali. Silakan klik ulang dari menu WhatsApp OTP.")
            return

        # Kembalikan keyboard OTP agar user ga nyasar
        kb_otp = await get_otp_keyboard()
        
        await update.message.reply_text(f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", parse_mode=ParseMode.HTML, reply_markup=kb_otp)
        ctx.user_data['buy_service'] = service_name
        ctx.user_data['buy_state'] = 'WAITING_QTY'
        return

    if text == "❌ Batal & Refund":
        last_order = ctx.user_data.get('last_order')
        if not last_order:
            await update.message.reply_text("❌ Tidak ada pesanan aktif untuk dibatalkan.")
            return
            
        ctx.bot_data[f"poll_{last_order['order_id']}"] = False
        res, code = await wahub_client.cancel_order(last_order['order_id'])
        
        if code == 200:
            refund_amount = last_order.get('price', 0)
            db.add_saldo(user.id, refund_amount)
            await update.message.reply_text(f"✅ Pesanan dibatalkan. Saldo Rp {refund_amount:,} telah dikembalikan.")
            ctx.user_data['last_order'] = None
            kb = await get_otp_keyboard()
            await update.message.reply_text("Menu OTP:", reply_markup=kb)
        else:
            await update.message.reply_text("❌ Gagal membatalkan pesanan. Mungkin waktu sewa habis.")
        return

    if text == "🔄 Kirim Ulang OTP":
        last_order = ctx.user_data.get('last_order')
        if not last_order:
            await update.message.reply_text("❌ Tidak ada pesanan aktif.")
            return
        res, code = await wahub_client.retry_otp(last_order['token'])
        if code == 200:
            await update.message.reply_text("✅ Permintaan OTP ulang dikirim. Mohon tunggu SMS/WA masuk.")
        else:
            await update.message.reply_text("❌ Gagal minta ulang OTP. Mungkin batas retry habis atau waktu sewa habis.")
        return

    text_upper = text.upper()
    if len(text_upper) >= 3 and re.match(r"^[A-Z0-9_-]+$", text_upper) and db.get_voc(text_upper):
        success, message, quota = db.use_voc(text_upper, user.id)
        if success:
            await update.message.reply_text(
                f"✅ *{message}*\n\nSekarang kamu punya *{db.get_user(user.id)['quota']}x* kuota.",
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ {message}")
        return

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    if len(lines) == 1 and is_valid_phone(lines[0]):
        if u["quota"] <= 0:
            await update.message.reply_text("❌ Kuota habis.")
            return
        msg = await update.message.reply_text("🔍 Mengecek...")
        start_time = time.time()
        result = await nasky_cek_single(normalize_phone(lines[0]))
        elapsed = time.time() - start_time
        if not result.get("success"):
            await msg.edit_text(f"❌ Error: {result.get('detail', 'Unknown')}")
            return
        d = result["data"]
        status = d["status"]
        phone_display = format_phone_display(d["phone"])
        if status == "fresh":
            db.use_quota(user.id)
            db.add_transaction(user.id, "cek", 1, "success", f"FRESH {d['phone']}")
        else:
            db.add_transaction(user.id, "cek", 0, "success", f"{status} {d['phone']}")
        new_quota = db.get_user(user.id)["quota"]
        if status == "fresh":
            reply = (f"📊 HASIL PENGECEKAN NOMOR\n\n"
                     f"❌ BELUM TERDAFTAR [1]:\n{phone_display}\n\n"
                     f"⏱️ Waktu: {elapsed:.2f} detik\n"
                     f"💳 Sisa Kuota: {new_quota} Kredit")
        elif status == "registered":
            reply = (f"📊 HASIL PENGECEKAN NOMOR\n\n"
                     f"✅ TERDAFTAR [1]:\n{phone_display} | {d.get('customer_name') or '-'}\n\n"
                     f"⏱️ Waktu: {elapsed:.2f} detik\n"
                     f"💳 Sisa Kuota: {new_quota} Kredit")
        else:
            reply = f"❓ Status tidak dikenal: `{status}`"
        await msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)
        return

    valid = [normalize_phone(l) for l in lines if is_valid_phone(l)]
    if valid:
        if u["quota"] < len(valid):
            await update.message.reply_text(f"❌ Kuota tidak cukup (butuh {len(valid)}x).")
            return
        msg = await update.message.reply_text(f"🔍 Mengecek {len(valid)} nomor...")
        start_time = time.time()
        fresh_list = []
        reg_list = []
        invalid_list = []
        for nomor in valid:
            r = await nasky_cek_single(nomor)
            if not r.get("success"):
                invalid_list.append(nomor)
                continue
            d = r["data"]
            phone_display = format_phone_display(d["phone"])
            if d["status"] == "fresh":
                fresh_list.append(phone_display)
                db.use_quota(user.id)
                db.add_transaction(user.id, "cek", 1, "success", f"FRESH {d['phone']}")
            elif d["status"] == "registered":
                reg_list.append((phone_display, d.get("customer_name") or "-"))
                db.add_transaction(user.id, "cek", 0, "success", f"REG {d['phone']}")
            else:
                invalid_list.append(phone_display)
        elapsed = time.time() - start_time
        new_quota = db.get_user(user.id)["quota"]
        out_lines = ["📊 HASIL PENGECEKAN NOMOR", ""]
        if reg_list:
            out_lines.append(f"✅ TERDAFTAR [{len(reg_list)}]:")
            for ph, nm in reg_list:
                out_lines.append(f"{ph} | {nm}")
            out_lines.append("")
        if fresh_list:
            out_lines.append(f"❌ BELUM TERDAFTAR [{len(fresh_list)}]:")
            for ph in fresh_list:
                out_lines.append(ph)
            out_lines.append("")
        if invalid_list:
            out_lines.append(f"⚠️ INVALID [{len(invalid_list)}]:")
            for ph in invalid_list:
                out_lines.append(ph)
            out_lines.append("")
        out_lines.append(f"⏱️ Waktu: {elapsed:.2f} detik")
        out_lines.append(f"💳 Sisa Kuota: {new_quota} Kredit")
        await msg.edit_text("\n".join(out_lines))
        file_out = ["=" * 40, "HASIL PENGECEKAN NOMOR", "=" * 40, ""]
        if reg_list:
            file_out.append(f"TERDAFTAR [{len(reg_list)}]:")
            for ph, nm in reg_list:
                file_out.append(f"{ph} | {nm}")
            file_out.append("")
        if fresh_list:
            file_out.append(f"BELUM TERDAFTAR [{len(fresh_list)}]:")
            for ph in fresh_list:
                file_out.append(ph)
            file_out.append("")
        if invalid_list:
            file_out.append(f"INVALID [{len(invalid_list)}]:")
            for ph in invalid_list:
                file_out.append(ph)
            file_out.append("")
        file_bytes = "\n".join(file_out).encode("utf-8")
        await update.message.reply_document(document=file_bytes, filename="hasil_cek.txt")
        return

    await update.message.reply_text("❌ Format tidak dikenali.")


# --- HANDLERS CALLBACK (INLINE BUTTONS) ---
async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    
    data_parts = query.data.split("_")
    if len(data_parts) < 4 or data_parts[1] == "none":
        return
        
    service_id = int(data_parts[2])
    harga_jual = int(data_parts[3])
    service_name = data_parts[4]
    
    ctx.user_data['buy_service'] = service_name
    ctx.user_data['buy_qty'] = 1
    ctx.user_data['buy_total'] = harga_jual
    ctx.user_data['buy_id'] = service_id
    
    await query.edit_message_text(
        f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", 
        parse_mode=ParseMode.HTML
    )
    ctx.user_data['buy_state'] = 'WAITING_QTY'

async def handle_cek_kopken_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    phone = query.data.split("_")[2]
    user = update.effective_user
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    await query.edit_message_text(f"🔍 Mengecek nomor `{phone}` ke Kopi Kenangan...", parse_mode=ParseMode.MARKDOWN)
    
    result = await nasky_cek_single(phone)
    if not result.get("success"):
        await query.edit_message_text(f"❌ Gagal cek: {result.get('detail', 'Unknown')}")
        return
        
    d = result["data"]
    if d["status"] == "fresh":
        if saldo < 200:
            await query.edit_message_text("❌ Saldo tidak cukup buat bayar biaya cek (Rp 200).")
            return
        db.deduct_saldo(user.id, 200)
        reply = f"❌ Nomor `{phone}` BELUM TERDAFTAR (FRESH).\n\n💳 Saldo terpotong: Rp 200."
    elif d["status"] == "registered":
        reply = f"✅ Nomor `{phone}` TERDAFTAR di Kopken.\nNama: {d.get('customer_name') or '-'}\n\n💳 Cek GRATIS."
    else:
        reply = f"❓ Status tidak dikenal: `{d['status']}`"
        
    await query.edit_message_text(reply, parse_mode=ParseMode.MARKDOWN)

# --- ADMIN COMMANDS ---
async def cmd_admin(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    s = db.get_stats()
    text = (
        f"🔧 *Admin Panel*\n\n"
        f"👥 Total users: {s['total_users']}\n"
        f"☕ Total cek: {s['total_cek']}\n"
        f"🔑 VOC aktif: {s['active_voc']}\n"
        f"🔑 Total VOC: {s['total_voc']}\n\n"
        f"*Generate VOC:* `/genvoc` `[kuota]` `[kode]`\n\n"
        f"*ShopeeFood Admin:*\n"
        f"`/sfood_users` — List user\n"
        f"`/sfood_leaderboard` — Leaderboard\n"
        f"`/sfood_userinfo <id>` — Detail user\n\n"
        f"*Kelola:* `/revoke <kode>` `/addkuota <id> <n>` `/addsaldo <id> <n>`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_genvoc(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args or []
    if not args:
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, DEFAULT_VOC_QUOTA, update.effective_user.id):
                await update.message.reply_text(
                    f"✅ *Kode VOC dibuat*\n\n`{code}`\n\nKuota: *{DEFAULT_VOC_QUOTA}x*",
                    parse_mode=ParseMode.MARKDOWN)
                return
        return
    if len(args) == 1 and args[0].isdigit():
        quota = int(args[0])
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, quota, update.effective_user.id):
                await update.message.reply_text(
                    f"✅ *Kode VOC dibuat*\n\n`{code}`\n\nKuota: *{quota}x*",
                    parse_mode=ParseMode.MARKDOWN)
                return
        return
    if len(args) == 1:
        custom = sanitize_voc_input(args[0])
        if not custom:
            await update.message.reply_text("❌ Kode tidak valid.")
            return
        if db.create_voc(custom, DEFAULT_VOC_QUOTA, update.effective_user.id):
            await update.message.reply_text(f"✅ Kode: `{custom}` Kuota: {DEFAULT_VOC_QUOTA}x",
                                             parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ Kode `{custom}` sudah ada.")
        return
    if len(args) >= 2:
        custom = sanitize_voc_input(args[0])
        if not custom or not args[1].isdigit():
            await update.message.reply_text("❌ Format: `/genvoc <KODE> <KUOTA>`",
                                             parse_mode=ParseMode.MARKDOWN)
            return
        quota = int(args[1])
        if db.create_voc(custom, quota, update.effective_user.id):
            await update.message.reply_text(f"✅ Kode: `{custom}` Kuota: {quota}x",
                                             parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ Kode `{custom}` sudah ada.")

async def cmd_listvoc(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    rows = db.list_voc_codes(20)
    if not rows:
        await update.message.reply_text("Belum ada kode VOC.")
        return
    lines = ["🔑 *20 Kode VOC Terakhir*\n"]
    for r in rows:
        status = "🟢" if r["active"] else "🔴"
        lines.append(f"{status} `{r['code']}` — {r['quota']}x")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_revoke(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/revoke <kode>`", parse_mode=ParseMode.MARKDOWN)
        return
    db.revoke_voc(ctx.args[0].upper())
    await update.message.reply_text(f"✅ Kode `{ctx.args[0].upper()}` dinonaktifkan.",
                                     parse_mode=ParseMode.MARKDOWN)

async def cmd_addkuota(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: `/addkuota <id> <n>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0])
        amt = int(ctx.args[1])
    except ValueError:
        return
    if not db.get_user(uid):
        await update.message.reply_text("❌ User tidak ditemukan.")
        return
    db.add_quota(uid, amt)
    new_q = db.get_user(uid)["quota"]
    await update.message.reply_text(f"✅ Kuota user `{uid}` +{amt}. Total: {new_q}x",
                                     parse_mode=ParseMode.MARKDOWN)

async def cmd_addsaldo(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: `/addsaldo <id> <n>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0])
        amt = int(ctx.args[1])
    except ValueError:
        return
    if not db.get_user(uid):
        await update.message.reply_text("❌ User tidak ditemukan.")
        return
    db.add_saldo(uid, amt)
    new_s = db.get_user(uid)["saldo"]
    await update.message.reply_text(f"✅ Saldo user `{uid}` +{amt}. Total: Rp {new_s:,}",
                                     parse_mode=ParseMode.MARKDOWN)

async def cmd_stats(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    s = db.get_stats()
    await update.message.reply_text(
        f"📊 *Statistik*\n\n"
        f"👥 Users: {s['total_users']}\n"
        f"☕ Total cek: {s['total_cek']}\n"
        f"🔑 VOC aktif: {s['active_voc']}\n"
        f"🔑 VOC total: {s['total_voc']}",
        parse_mode=ParseMode.MARKDOWN)

async def cmd_users(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    users = db.list_all_users(limit=50)
    if not users:
        await update.message.reply_text("Belum ada user.")
        return
    import datetime as dtmod
    lines = [f"👥 *Total Users: {len(users)}*\n"]
    for u in users:
        uname = f"@{u['username']}" if u['username'] else "(no uname)"
        name = u['first_name'] or "(no name)"
        dt = dtmod.datetime.fromtimestamp(u['created_at']).strftime("%d/%m %H:%M")
        saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
        lines.append(f"• {name} {uname}\n  ID: `{u['user_id']}` | Kuota: {u['quota']}x | Saldo: Rp {saldo:,} | {dt}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_userinfo(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/userinfo <id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        return
    user, vocs = db.get_user_detail(uid)
    if not user:
        await update.message.reply_text(f"❌ User `{uid}` tidak ditemukan.")
        return
    import datetime as dtmod
    dt = dtmod.datetime.fromtimestamp(user['created_at']).strftime("%d/%m/%Y %H:%M")
    uname = f"@{user['username']}" if user['username'] else "(no uname)"
    saldo = user['saldo'] if user and 'saldo' in user.keys() else 0
    text = (
        f"👤 *Detail User*\n\n"
        f"ID: `{user['user_id']}`\n"
        f"Username: {uname}\n"
        f"Nama: {user['first_name'] or '-'}\n\n"
        f"📊 Kuota: *{user['quota']}x*\n"
        f"💰 Saldo OTP: *Rp {saldo:,}*\n"
        f"☕ Total cek: {user['total_cek']}x\n"
        f"📅 Daftar: {dt}\n\n"
    )
    if vocs:
        text += f"🔑 *VOC dipakai ({len(vocs)}):*\n"
        for v in vocs:
            text += f"• `{v['code']}` — {v['quota']}x\n"
    else:
        text += "🔑 Belum pernah redeem VOC."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_vocdetail(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/vocdetail <kode>`", parse_mode=ParseMode.MARKDOWN)
        return
    code = ctx.args[0].upper()
    voc, user = db.get_voc_detail(code)
    if not voc:
        await update.message.reply_text(f"❌ Kode `{code}` tidak ditemukan.")
        return
    import datetime as dtmod
    dt = dtmod.datetime.fromtimestamp(voc['created_at']).strftime("%d/%m/%Y %H:%M")
    text = (
        f"🔑 *Detail Kode VOC*\n\n"
        f"Kode: `{voc['code']}`\n"
        f"Kuota: {voc['quota']}x\n"
        f"Dibuat: {dt}\n"
        f"Status: {'🟢 Aktif' if voc['active'] else '🔴 Terpakai'}\n\n"
    )
    if user:
        used_at = dtmod.datetime.fromtimestamp(voc['used_at']).strftime("%d/%m/%Y %H:%M") if voc['used_at'] else "-"
        uname = f"@{user['username']}" if user['username'] else "(no uname)"
        text += (
            f"👤 *Dipakai oleh:*\n"
            f"Nama: {user['first_name'] or '-'}\n"
            f"Username: {uname}\n"
            f"User ID: `{user['user_id']}`\n"
            f"Redeem: {used_at}"
        )
    else:
        text += "👤 Belum dipakai siapa pun."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def error_handler(update, ctx):
    logger.error(f"Error: {ctx.error}", exc_info=ctx.error)


def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN belum di-set")
        return
    if not NASKY_API_KEY:
        print("❌ NASKY_API_KEY belum di-set")
        return

    db.init_db()

    async def post_init(app):
        app.bot_data["admin_ids"] = ADMIN_IDS
        asyncio.create_task(tracker_worker(app.bot))
        print("[SFOOD] Tracker worker started")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("kuota", cmd_kuota))

    app.add_handler(CommandHandler("sfood", cmd_sfood))
    app.add_handler(CommandHandler("sfood_list", cmd_sfood_list))
    app.add_handler(CommandHandler("sfood_stop", cmd_sfood_stop))
    app.add_handler(CommandHandler("sfood_stopall", cmd_sfood_stopall))
    app.add_handler(CommandHandler("sfood_users", cmd_sfood_users))
    app.add_handler(CommandHandler("sfood_leaderboard", cmd_sfood_leaderboard))
    app.add_handler(CommandHandler("sfood_userinfo", cmd_sfood_userinfo))

    app.add_handler(CommandHandler("telemetry", cmd_telemetry))
    app.add_handler(CommandHandler("pantau_on", cmd_pantau_on))
    app.add_handler(CommandHandler("pantau_off", cmd_pantau_off))
    app.add_handler(CommandHandler("jarak_set", cmd_jarak_set))
    app.add_handler(CommandHandler("stuck_set", cmd_stuck_set))
    app.add_handler(CommandHandler("sfood_metrics", cmd_sfood_metrics))
    app.add_handler(CommandHandler("lokasi_saya", cmd_lokasi_saya))

    app.add_handler(CommandHandler("tarif", cmd_tarif))
    app.add_handler(CommandHandler("tarif_cek", cmd_tarif_cek))
    app.add_handler(CommandHandler("tarif_stats", cmd_tarif_stats))

    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("genvoc", cmd_genvoc))
    app.add_handler(CommandHandler("listvoc", cmd_listvoc))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("addkuota", cmd_addkuota))
    app.add_handler(CommandHandler("addsaldo", cmd_addsaldo))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    app.add_handler(CommandHandler("vocdetail", cmd_vocdetail))

    # Handler Callback
    app.add_handler(CallbackQueryHandler(handle_cekstatus_callback, pattern=r"^cekstatus_"))
    app.add_handler(CallbackQueryHandler(handle_cek_kopken_callback, pattern=r"^otp_cekkopken_"))
    app.add_handler(CallbackQueryHandler(handle_buy_callback, pattern=r"^otp_buy_"))

    app.add_handler(MessageHandler(filters.LOCATION, handle_live_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 Bot jalan... Ctrl+C untuk stop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()