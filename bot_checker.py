import os
import asyncio
import logging
import random
import string
import re
import time
import httpx
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

import database as db
import wahub_client
from invite_access import (cmd_generate_invite, cmd_cancel_invite,
    handle_invite_message, require_whatsapp_access, clear_invite_flow)
from otp_handlers import (
    cmd_otp_menu, process_direct_buy, process_deposit, 
    cmd_deposit, get_menu_keyboard, get_otp_keyboard, handle_cekstatus_callback,
    handle_cek_kopken_callback, handle_order_action_callback, 
    handle_buy_filter_auto_cancel_callback, handle_buy_callback,
    boostly_cek_single, BIAYA_CEK_FRESH, handle_history_callback
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

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

bot_app = None

def is_admin(user_id): return user_id in ADMIN_IDS

def normalize_phone(text):
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if t.startswith("0"): t = "62" + t[1:]
    return t

def format_phone_display(phone_62):
    if phone_62.startswith("62"): return "0" + phone_62[2:]
    return phone_62

def is_valid_phone(text):
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not t.isdigit(): return False
    if t.startswith("62"): return 10 <= len(t) <= 15
    if t.startswith("0"): return 10 <= len(t) <= 14
    return False

def gen_voc_code():
    chars = string.ascii_uppercase + string.digits
    return f"VOC-{''.join(random.choices(chars, k=6))}"

def sanitize_voc_input(text):
    t = text.strip().upper().replace(" ", "")
    if not re.match(r"^[A-Z0-9_-]+$", t): return None
    if len(t) < 3 or len(t) > 50: return None
    return t

def main_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Nomor Kopken", "💰 Cek Balance"],
        ["📱 WhatsApp OTP", "💳 Topup Balance"],
        ["🔑 Redeem Invitation Code"],
        ["🍔 ShopeeFood", "❓ Bantuan"]
    ], resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Nomor Kopken", "💰 Cek Balance"],
        ["📱 WhatsApp OTP", "💳 Topup Balance"],
        ["🔑 Redeem Invitation Code"],
        ["🍔 ShopeeFood", "🔧 Admin Panel"],
        ["📥 Topup Log", "🆕 User Baru"],
        ["❓ Bantuan"]
    ], resize_keyboard=True)

async def bg_check_stuck_orders(app):
    while True:
        await asyncio.sleep(60)
        try:
            stuck_orders = db.get_stuck_waiting_orders(minutes=20)
            for order in stuck_orders:
                oid = order['order_id']
                uid = order['user_id']
                sname = order['service_name'] or "OTP"
                try:
                    _, code = await asyncio.wait_for(
                        wahub_client.cancel_order(oid), timeout=25
                    )
                except Exception as exc:
                    logger.warning("[CRON] Batal order %s gagal: %s", oid, exc)
                    continue

                if code != 200:
                    logger.warning("[CRON] Provider belum mengonfirmasi batal order %s (HTTP %s)", oid, code)
                    continue

                changed, price = db.finish_otp_order_and_refund(
                    oid, uid, 'expired', f"Refund Auto {sname}"
                )
                if not changed:
                    continue

                logger.info("[CRON] Refund otomatis order %s (%s)", oid, sname)
                try:
                    await app.bot.send_message(
                        chat_id=uid,
                        text=(
                            f"⏰ <b>Waktu Habis</b>\n\nOrder <code>{oid}</code> "
                            f"tidak menerima OTP. Balance Rp {price:,} otomatis dikembalikan."
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    logger.warning("[CRON] Gagal mengirim notifikasi order %s: %s", oid, exc)
        except Exception as exc:
            logger.exception("[CRON] Gagal memeriksa order OTP: %s", exc)

async def cmd_start(update, ctx):
    clear_invite_flow(ctx)
    user = update.effective_user
    db.create_user(user.id, user.username or "", user.first_name or "")
    u = db.get_user(user.id)
    kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
    balance = u['saldo'] if u and 'saldo' in u.keys() else 0
    text = (
        f"Halo {user.first_name}! 👋\n\n"
        f"☕ *Bot Multi Fungsi*\n\n"
        f"💰 Balance kamu: *Rp {balance:,}*\n\n"
        f"Ketik `/help` untuk bantuan."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def cmd_help(update, ctx):
    text = (
        "📖 *Bantuan*\n\n"
        "*☕ Cek Nomor Kopken:*\n"
        "• Klik tombol Cek Nomor Kopken, lalu masukkan nomor HP.\n\n"
        "*📱 OTP:*\n"
        "• Klik tombol WhatsApp OTP di keyboard\n\n"
        "*💳 Topup:*\n"
        "• Klik tombol Topup Balance untuk isi saldo via QRIS\n\n"
        "*🍔 ShopeeFood Tracker:*\n"
        "• Kirim link tracker + label\n"
        "• `/sfood` — Menu tracker (Termasuk Pantau Kurir & Tarif)\n\n"
        "*Commands:*\n"
        "/start, /help, /balance, /sfood"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_balance(update, ctx):
    user = update.effective_user
    u = db.get_user(user.id)
    if not u:
        db.create_user(user.id, user.username or "", user.first_name or "")
        u = db.get_user(user.id)
    balance = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    trxs = db.get_user_transactions(user.id, limit=10)
    
    text = f"💰 *Balance Kamu*\n\nSaldo saat ini: *Rp {balance:,}*\n\n"
    text += "🧾 *10 Transaksi Terakhir:*\n"
    
    if not trxs:
        text += "_(Belum ada transaksi)_"
    else:
        import datetime as dtmod
        for t in trxs:
            jam = dtmod.datetime.fromtimestamp(t['created_at']).strftime("%d/%m %H:%M")
            if t['type'] == 'IN':
                text += f"🟢 +Rp {t['amount']:,} | {t['description']} ({jam})\n"
            else:
                text += f"🔴 -Rp {t['amount']:,} | {t['description']} ({jam})\n"
                
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update, ctx):
    text = (update.message.text or "").strip()
    user = update.effective_user

    if not db.get_user(user.id):
        db.create_user(user.id, user.username or "", user.first_name or "")
    u = db.get_user(user.id)

    if text in ("🔙 Kembali ke Menu", "🔙 Menu Utama", "Batal", "batal", "cancel"):
        clear_invite_flow(ctx)
        ctx.user_data.pop('deposit_state', None)
        ctx.user_data.pop('cek_kopken_state', None)
        await cmd_start(update, ctx)
        return
    if await handle_invite_message(update, ctx):
        return

    if ctx.user_data.get('buy_state') == 'WAITING_QTY' and text.isdigit():
        qty = int(text)
        await process_direct_buy(update, ctx, qty)
        return
        
    if ctx.user_data.get('deposit_state') == 'WAITING_AMOUNT' and text.isdigit():
        amount = int(text)
        await process_deposit(update, ctx, amount)
        return

    if ctx.user_data.get('cek_kopken_state') == 'WAITING_PHONE':
        ctx.user_data['cek_kopken_state'] = None
        if not is_valid_phone(text):
            await update.message.reply_text("❌ Format nomor tidak valid. Silakan coba lagi.")
            return
        
        phone = normalize_phone(text)
        msg = await update.message.reply_text("🔍 Mengecek nomor Kopken...")
        
        trial_tersisa = db.get_trial_kopken(user.id)
        result = await boostly_cek_single(phone)
        
        if not result.get("success"):
            raw_data = result.get("raw", "Ga ada data")
            detail_error = result.get("detail", "Ga ada detail")
            
            promo_txt = (
                f"⚠️ <b>Debugging API Boostly</b>\n\n"
                f"<b>Detail:</b> {detail_error}\n"
                f"<b>Raw Response:</b>\n<code>{raw_data}</code>\n\n"
                f"Tolong kirim screenshot ini ke developer."
            )
            await msg.edit_text(promo_txt, parse_mode=ParseMode.HTML)
            return
            
        status = result.get("status")
        
        if status == "fresh":
            if trial_tersisa > 0:
                db.set_trial_kopken(user.id, trial_tersisa - 1)
                reply = f"❌ Nomor `{phone}` BELUM TERDAFTAR (FRESH).\n\n🎁 Kamu menggunakan 1x Free Trial cek. Sisa trial: {trial_tersisa-1}x."
            else:
                u = db.get_user(user.id)
                balance = u['saldo'] if u and 'saldo' in u.keys() else 0
                if balance < BIAYA_CEK_FRESH:
                    await msg.edit_text(f"❌ Balance tidak cukup buat bayar biaya cek (Rp {BIAYA_CEK_FRESH:,}).")
                    return
                db.deduct_saldo(user.id, BIAYA_CEK_FRESH)
                reply = f"❌ Nomor `{phone}` BELUM TERDAFTAR (FRESH).\n\n💳 Balance terpotong: Rp {BIAYA_CEK_FRESH:,}."
                
        elif status == "registered":
            reply = f"✅ Nomor `{phone}` TERDAFTAR di Kopken.\nNama: {result.get('name') or '-'}\n\n💳 Cek GRATIS."
        else:
            reply = f"❓ Status tidak dikenal: `{status}`"
            
        await msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)
        return

    if "shopeefood.co.id/tracker" in text or "shptools.my.id/sfood" in text:
        await handle_sfood_message(update, ctx)
        return

    if text == "☕ Cek Nomor Kopken":
        await update.message.reply_text("Silakan kirim nomor HP yang mau dicek.\n\nContoh: `081234567890`", parse_mode=ParseMode.MARKDOWN)
        ctx.user_data['cek_kopken_state'] = 'WAITING_PHONE'
        return

    if text == "💰 Cek Balance":
        await cmd_balance(update, ctx)
        return

    if text == "💳 Topup Balance":
        await cmd_deposit(update, ctx)
        return

    if text == "📱 WhatsApp OTP":
        await cmd_otp_menu(update, ctx)
        return
        
    if text == "🔑 Redeem Invitation Code":
        await update.message.reply_text("Silakan kirim kode Invitation / VOC kamu.\n\nFormat: `VOC-XXXXXX`", parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🍔 ShopeeFood":
        await cmd_sfood(update, ctx)
        return

    if text == "❓ Bantuan":
        await cmd_help(update, ctx)
        return

    if text == "🔧 Admin Panel" and is_admin(user.id):
        await cmd_admin(update, ctx)
        return
        
    if text == "📥 Topup Log" and is_admin(user.id):
        topups = db.get_recent_topups(10)
        if not topups:
            await update.message.reply_text("Belum ada topup.")
            return
        txt = "📥 *10 Topup Terakhir*\n\n"
        import datetime as dtmod
        for t in topups:
            jam = dtmod.datetime.fromtimestamp(t['created_at']).strftime("%d/%m %H:%M")
            txt += f"🟢 Rp {t['amount']:,} | User: `{t['user_id']}` | {jam}\n"
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🆕 User Baru" and is_admin(user.id):
        users = db.get_recent_users(10)
        txt = "🆕 *10 User Terbaru*\n\n"
        import datetime as dtmod
        for u in users:
            jam = dtmod.datetime.fromtimestamp(u['created_at']).strftime("%d/%m %H:%M")
            uname = f"@{u['username']}" if u['username'] else "(no uname)"
            txt += f"👤 `{u['user_id']}` | {u['first_name']} {uname} | {jam}\n"
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🔙 Kembali ke Menu" or text == "🔙 Menu Utama":
        kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
        balance = u['saldo'] if u and 'saldo' in u.keys() else 0
        await update.message.reply_text(f"🏠 <b>Menu Utama</b>\n\n💰 Balance: <b>Rp {balance:,}</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if text == "📋 Cek Pesanan":
        if not await require_whatsapp_access(update, ctx):
            return
        trxs = db.get_user_transactions(user.id, limit=15)
        txt = "📜 <b>Riwayat Transaksi Kamu</b>\n\n"
        
        if not trxs:
            txt += "<i>Belum ada transaksi.</i>"
        else:
            import datetime as dtmod
            for t in trxs:
                try:
                    dt = dtmod.datetime.strptime(t['created_at'], "%Y-%m-%d %H:%M:%S")
                    jam = dt.strftime("%d/%m %H:%M")
                except:
                    jam = "-"
                    
                if t['amount'] > 0:
                    txt += f"🟢 +Rp {t['amount']:,} | {t['description']} ({jam})\n"
                else:
                    txt += f"🔴 -Rp {abs(t['amount']):,} | {t['description']} ({jam})\n"
                    
        await update.message.reply_text(txt, parse_mode=ParseMode.HTML)
        return

    if text.startswith("🛒 "):
        if not await require_whatsapp_access(update, ctx):
            return
        match = re.match(r"🛒\s+(.+?)\s+\(\d+\)\s+-\s+[\d,]+", text)
        service_name = match.group(1).strip() if match else None
        if not service_name:
            await update.message.reply_text("❌ Format tombol tidak dikenali.")
            return
        kb_otp = await get_otp_keyboard()
        await update.message.reply_text(f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", parse_mode=ParseMode.HTML, reply_markup=kb_otp)
        ctx.user_data['buy_service'] = service_name
        ctx.user_data['buy_state'] = 'WAITING_QTY'
        return

    text_upper = text.upper()
    if len(text_upper) >= 3 and re.match(r"^[A-Z0-9_-]+$", text_upper) and db.get_voc(text_upper):
        success, message, quota = db.use_voc(text_upper, user.id)
        if success:
            await update.message.reply_text(f"✅ *{message}*\n\nSekarang Balance kamu: *Rp {db.get_user(user.id)['saldo']:,}*.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ {message}")
        return

    await update.message.reply_text("❌ Format tidak dikenali.")

async def cmd_admin(update, ctx):
    if not is_admin(update.effective_user.id): return
    s = db.get_stats()
    text = f"🔧 *Admin Panel*\n\n👥 Users: {s['total_users']}\n☕ Total cek: {s['total_cek']}\n\n*Generate VOC:* `/genvoc` `[saldo]` `[kode]`\n\n*ShopeeFood Admin:*\n`/sfood_users` — List user\n`/sfood_leaderboard` — Leaderboard\n`/sfood_userinfo <id>` — Detail user\n\n*Kelola:* `/revoke <kode>` `/addsaldo <id> <n>`"
    text += "\n\n*Undangan WhatsApp OTP:* `/generate_invite [1-20]` (chat pribadi, sekali pakai)."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_genvoc(update, ctx):
    if not is_admin(update.effective_user.id): return
    args = ctx.args or []
    if not args:
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, DEFAULT_VOC_QUOTA, update.effective_user.id):
                await update.message.reply_text(f"✅ *Kode VOC dibuat*\n\n`{code}`\n\nIsi Balance: *Rp {DEFAULT_VOC_QUOTA}x*", parse_mode=ParseMode.MARKDOWN)
                return
        return
    if len(args) == 1 and args[0].isdigit():
        quota = int(args[0])
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, quota, update.effective_user.id):
                await update.message.reply_text(f"✅ *Kode VOC dibuat*\n\n`{code}`\n\nIsi Balance: *Rp {quota:,}*", parse_mode=ParseMode.MARKDOWN)
                return
        return
    if len(args) == 1:
        custom = sanitize_voc_input(args[0])
        if not custom: await update.message.reply_text("❌ Kode tidak valid."); return
        if db.create_voc(custom, DEFAULT_VOC_QUOTA, update.effective_user.id): await update.message.reply_text(f"✅ Kode: `{custom}` Balance: Rp {DEFAULT_VOC_QUOTA}x", parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(f"❌ Kode `{custom}` sudah ada.")
        return
    if len(args) >= 2:
        custom = sanitize_voc_input(args[0])
        if not custom or not args[1].isdigit(): await update.message.reply_text("❌ Format: `/genvoc <KODE> <SALDO>`", parse_mode=ParseMode.MARKDOWN); return
        quota = int(args[1])
        if db.create_voc(custom, quota, update.effective_user.id): await update.message.reply_text(f"✅ Kode: `{custom}` Balance: Rp {quota:,}", parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(f"❌ Kode `{custom}` sudah ada.")

async def cmd_listvoc(update, ctx):
    if not is_admin(update.effective_user.id): return
    rows = db.list_voc_codes(20)
    if not rows: await update.message.reply_text("Belum ada kode VOC."); return
    lines = ["🔑 *20 Kode VOC Terakhir*\n"]
    for r in rows:
        status = "🟢" if r["active"] else "🔴"
        lines.append(f"{status} `{r['code']}` — Rp {r['quota']:,}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_revoke(update, ctx):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Usage: `/revoke <kode>`", parse_mode=ParseMode.MARKDOWN); return
    db.revoke_voc(ctx.args[0].upper())
    await update.message.reply_text(f"✅ Kode `{ctx.args[0].upper()}` dinonaktifkan.", parse_mode=ParseMode.MARKDOWN)

async def cmd_addsaldo(update, ctx):
    if not is_admin(update.effective_user.id): return
    if len(ctx.args) < 2: await update.message.reply_text("Usage: `/addsaldo <id> <n>`", parse_mode=ParseMode.MARKDOWN); return
    try: uid = int(ctx.args[0]); amt = int(ctx.args[1])
    except ValueError: return
    if not db.get_user(uid): await update.message.reply_text("❌ User tidak ditemukan."); return
    db.add_saldo(uid, amt, f"Admin Add Saldo")
    new_s = db.get_user(uid)["saldo"]
    await update.message.reply_text(f"✅ Saldo user `{uid}` +{amt}. Total: Rp {new_s:,}", parse_mode=ParseMode.MARKDOWN)

async def cmd_stats(update, ctx):
    if not is_admin(update.effective_user.id): return
    s = db.get_stats()
    await update.message.reply_text(f"📊 *Statistik*\n\n👥 Users: {s['total_users']}\n☕ Total cek: {s['total_cek']}", parse_mode=ParseMode.MARKDOWN)

async def cmd_users(update, ctx):
    if not is_admin(update.effective_user.id): return
    users = db.list_all_users(limit=50)
    if not users: await update.message.reply_text("Belum ada user."); return
    import datetime as dtmod
    lines = [f"👥 *Total Users: {len(users)}*\n"]
    for u in users:
        uname = f"@{u['username']}" if u['username'] else "(no uname)"
        name = u['first_name'] or "(no name)"
        dt = dtmod.datetime.fromtimestamp(u['created_at']).strftime("%d/%m %H:%M")
        saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
        lines.append(f"• {name} {uname}\n  ID: `{u['user_id']}` | Balance: Rp {saldo:,} | {dt}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_userinfo(update, ctx):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Usage: `/userinfo <id>`", parse_mode=ParseMode.MARKDOWN); return
    try: uid = int(ctx.args[0])
    except ValueError: return
    user, vocs = db.get_user_detail(uid)
    if not user: await update.message.reply_text(f"❌ User `{uid}` tidak ditemukan."); return
    import datetime as dtmod
    dt = dtmod.datetime.fromtimestamp(user['created_at']).strftime("%d/%m/%Y %H:%M")
    uname = f"@{user['username']}" if user['username'] else "(no uname)"
    saldo = user['saldo'] if user and 'saldo' in user.keys() else 0
    text = f"👤 *Detail User*\n\nID: `{user['user_id']}`\nUsername: {uname}\nNama: {user['first_name'] or '-'}\n\n💰 Balance: *Rp {saldo:,}*\n📅 Daftar: {dt}\n\n"
    if vocs:
        text += f"🔑 *VOC dipakai ({len(vocs)}):*\n"
        for v in vocs: text += f"• `{v['code']}` — Rp {v['quota']:,}\n"
    else: text += "🔑 Belum pernah redeem VOC."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_vocdetail(update, ctx):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Usage: `/vocdetail <kode>`", parse_mode=ParseMode.MARKDOWN); return
    code = ctx.args[0].upper()
    voc, user = db.get_voc_detail(code)
    if not voc: await update.message.reply_text(f"❌ Kode `{code}` tidak ditemukan."); return
    import datetime as dtmod
    dt = dtmod.datetime.fromtimestamp(voc['created_at']).strftime("%d/%m/%Y %H:%M")
    text = f"🔑 *Detail Kode VOC*\n\nKode: `{voc['code']}`\nBalance: Rp {voc['quota']:,}\nDibuat: {dt}\nStatus: {'🟢 Aktif' if voc['active'] else '🔴 Terpakai'}\n\n"
    if user:
        used_at = dtmod.datetime.fromtimestamp(voc['used_at']).strftime("%d/%m/%Y %H:%M") if voc['used_at'] else "-"
        uname = f"@{user['username']}" if user['username'] else "(no uname)"
        text += f"👤 *Dipakai oleh:*\nNama: {user['first_name'] or '-'}\nUsername: {uname}\nUser ID: `{user['user_id']}`\nRedeem: {used_at}"
    else: text += "👤 Belum dipakai siapa pun."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def error_handler(update, ctx):
    logger.error(f"Error: {ctx.error}", exc_info=ctx.error)

def main():
    global bot_app
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN belum di-set")
        return
    db.init_db()
    
    bot_app = Application.builder().token(BOT_TOKEN).build()

    async def on_startup(app: Application):
        # Bersihkan webhook yang mungkin tersisa dari Railway
        await app.bot.delete_webhook(drop_pending_updates=True)
        asyncio.create_task(tracker_worker(app.bot))
        print("[SFOOD] Tracker worker started")
        asyncio.create_task(bg_check_stuck_orders(app))
        print("[CRON] Background refunder started")

    bot_app.post_init = on_startup
    
    bot_app.add_handler(CommandHandler("start", cmd_start))
    bot_app.add_handler(CommandHandler("help", cmd_help))
    bot_app.add_handler(CommandHandler("balance", cmd_balance))
    bot_app.add_handler(CommandHandler("sfood", cmd_sfood))
    bot_app.add_handler(CommandHandler("sfood_list", cmd_sfood_list))
    bot_app.add_handler(CommandHandler("sfood_stop", cmd_sfood_stop))
    bot_app.add_handler(CommandHandler("sfood_stopall", cmd_sfood_stopall))
    bot_app.add_handler(CommandHandler("sfood_users", cmd_sfood_users))
    bot_app.add_handler(CommandHandler("sfood_leaderboard", cmd_sfood_leaderboard))
    bot_app.add_handler(CommandHandler("sfood_userinfo", cmd_sfood_userinfo))
    bot_app.add_handler(CommandHandler("telemetry", cmd_telemetry))
    bot_app.add_handler(CommandHandler("pantau_on", cmd_pantau_on))
    bot_app.add_handler(CommandHandler("pantau_off", cmd_pantau_off))
    bot_app.add_handler(CommandHandler("jarak_set", cmd_jarak_set))
    bot_app.add_handler(CommandHandler("stuck_set", cmd_stuck_set))
    bot_app.add_handler(CommandHandler("sfood_metrics", cmd_sfood_metrics))
    bot_app.add_handler(CommandHandler("lokasi_saya", cmd_lokasi_saya))
    bot_app.add_handler(CommandHandler("tarif", cmd_tarif))
    bot_app.add_handler(CommandHandler("tarif_cek", cmd_tarif_cek))
    bot_app.add_handler(CommandHandler("tarif_stats", cmd_tarif_stats))
    bot_app.add_handler(CommandHandler("generate_invite", cmd_generate_invite))
    bot_app.add_handler(CommandHandler("cancel", cmd_cancel_invite))
    bot_app.add_handler(CommandHandler("admin", cmd_admin))
    bot_app.add_handler(CommandHandler("genvoc", cmd_genvoc))
    bot_app.add_handler(CommandHandler("listvoc", cmd_listvoc))
    bot_app.add_handler(CommandHandler("revoke", cmd_revoke))
    bot_app.add_handler(CommandHandler("addsaldo", cmd_addsaldo))
    bot_app.add_handler(CommandHandler("stats", cmd_stats))
    bot_app.add_handler(CommandHandler("users", cmd_users))
    bot_app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    bot_app.add_handler(CommandHandler("vocdetail", cmd_vocdetail))
    
    bot_app.add_handler(CallbackQueryHandler(handle_cekstatus_callback, pattern=r"^cekstatus_"))
    bot_app.add_handler(CallbackQueryHandler(handle_order_action_callback, pattern=r"^otp_act_"))
    bot_app.add_handler(CallbackQueryHandler(handle_cek_kopken_callback, pattern=r"^otp_cekkopken_"))
    bot_app.add_handler(CallbackQueryHandler(handle_buy_filter_auto_cancel_callback, pattern=r"^otp_buyfilter_"))
    bot_app.add_handler(CallbackQueryHandler(handle_buy_callback, pattern=r"^otp_buy_"))
    bot_app.add_handler(CallbackQueryHandler(handle_history_callback, pattern=r"^otp_history$"))
    
    bot_app.add_handler(MessageHandler(filters.LOCATION, handle_live_location))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot_app.add_error_handler(error_handler)
    
    print("🤖 Bot jalan dalam mode Long Polling...")
    bot_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()