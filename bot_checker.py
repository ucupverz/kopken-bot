import os
import asyncio
import logging
import random
import string
import re
from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager
import uvicorn
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

import database as db
import wahub_client
from otp_handlers import (
    cmd_otp_menu, process_direct_buy, process_deposit, cmd_deposit,
    get_otp_menu_keyboard, handle_cekstatus_callback, handle_cek_kopken_callback,
    handle_order_action_callback, handle_buy_callback,
    boostly_cek_single, BIAYA_CEK_FRESH, CALLBACK_SEP, _do_cek_kopken,
    build_order_ui, get_order_inline_keyboard,
    handle_history_callback, handle_menu_back_callback
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
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DEFAULT_VOC_QUOTA = int(os.getenv("DEFAULT_VOC_QUOTA", "3"))
WEBHOOK_URL = "https://okezones-bot.up.railway.app"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

bot_app = None

def is_admin(uid): return uid in ADMIN_IDS

def normalize_phone(text):
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if t.startswith("0"):
        t = "62" + t[1:]
    return t

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
    return f"VOC-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"

def sanitize_voc_input(text):
    t = text.strip().upper().replace(" ", "")
    if not re.match(r"^[A-Z0-9_-]+$", t):
        return None
    if len(t) < 3 or len(t) > 50:
        return None
    return t

def main_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Nomor Kopken", "💰 Cek Balance"],
        ["📱 WhatsApp OTP", "💳 Topup Balance"],
        ["🔑 Redeem Invitation Code"],
        ["🍔 ShopeeFood", "❓ Bantuan"],
    ], resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Nomor Kopken", "💰 Cek Balance"],
        ["📱 WhatsApp OTP", "💳 Topup Balance"],
        ["🔑 Redeem Invitation Code"],
        ["🍔 ShopeeFood", "🔧 Admin Panel"],
        ["📥 Topup Log", "🆕 User Baru"],
        ["❓ Bantuan"],
    ], resize_keyboard=True)

async def bg_check_stuck_orders(app):
    while True:
        await asyncio.sleep(60)
        try:
            rows = db.get_stuck_waiting_orders(20)
            for order in rows:
                oid = order['order_id']
                uid = order['user_id']
                price = order['price']
                res, code = await wahub_client.cancel_order(oid)
                if not db.mark_status_if_waiting(oid, 'expired'):
                    continue
                db.add_saldo(uid, price, "Refund Auto Timeout")
                try:
                    await app.bot.send_message(
                        chat_id=uid,
                        text=f"⏰ Order <code>{oid}</code> timeout. Balance Rp {price:,} dikembalikan.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"stuck cron: {e}")

async def cmd_start(update, ctx):
    user = update.effective_user
    db.create_user(user.id, user.username or "", user.first_name or "")
    u = db.get_user(user.id)
    kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
    balance = u['saldo'] if u else 0
    await update.message.reply_text(
        f"Halo {user.first_name}! 👋\n\n💰 Balance: *Rp {balance:,}*\n\n/help untuk bantuan.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
    )

async def cmd_help(update, ctx):
    await update.message.reply_text(
        "📖 *Bantuan*\n\n"
        "☕ Cek Nomor Kopken — via keyboard\n"
        "📱 WhatsApp OTP — via keyboard\n"
        "💳 Topup Balance — via keyboard\n"
        "🍔 ShopeeFood — /sfood\n\n"
        "/start /help /balance /sfood",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_balance(update, ctx):
    user = update.effective_user
    u = db.get_user(user.id)
    if not u:
        db.create_user(user.id, user.username or "", user.first_name or "")
        u = db.get_user(user.id)
    balance = u['saldo'] if u else 0
    trxs = db.get_user_transactions(user.id, limit=10)
    text = f"💰 *Balance*\n\nSaldo: *Rp {balance:,}*\n\n🧾 *10 Transaksi:*\n"
    if not trxs:
        text += "_(kosong)_"
    else:
        import datetime as dtmod
        for t in trxs:
            jam = dtmod.datetime.fromtimestamp(t['created_at']).strftime("%d/%m %H:%M")
            sign = "+" if t['type'] == "IN" else "-"
            text += f"{'🟢' if t['type']=='IN' else '🔴'} {sign}Rp {t['amount']:,} | {t['description']} ({jam})\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update, ctx):
    text = (update.message.text or "").strip()
    user = update.effective_user
    if not db.get_user(user.id):
        db.create_user(user.id, user.username or "", user.first_name or "")

    if ctx.user_data.get('buy_state') == 'WAITING_QTY':
        clean = re.sub(r'\D', '', text)
        if clean:
            await process_direct_buy(update, ctx, int(clean))
            return

    if ctx.user_data.get('deposit_state') == 'WAITING_AMOUNT' and text.isdigit():
        await process_deposit(update, ctx, int(text))
        return

    if ctx.user_data.get('cek_kopken_state') == 'WAITING_PHONE':
        ctx.user_data['cek_kopken_state'] = None
        if not is_valid_phone(text):
            await update.message.reply_text("❌ Format nomor tidak valid.")
            return
        phone = normalize_phone(text)
        msg = await update.message.reply_text("🔍 Mengecek...")
        reply, is_html = await _do_cek_kopken(user.id, phone)
        await msg.edit_text(
            reply, parse_mode=ParseMode.HTML if is_html else ParseMode.MARKDOWN
        )
        return

    if "shopeefood.co.id/tracker" in text or "shptools.my.id/sfood" in text:
        await handle_sfood_message(update, ctx)
        return

    simple_map = {
        "💰 Cek Balance": cmd_balance,
        "💳 Topup Balance": cmd_deposit,
        "📱 WhatsApp OTP": cmd_otp_menu,
        "🍔 ShopeeFood": cmd_sfood,
        "❓ Bantuan": cmd_help,
    }
    if text in simple_map:
        await simple_map[text](update, ctx)
        return

    if text == "☕ Cek Nomor Kopken":
        ctx.user_data['cek_kopken_state'] = 'WAITING_PHONE'
        await update.message.reply_text(
            "Kirim nomor HP.\nContoh: `081234567890`", parse_mode=ParseMode.MARKDOWN
        )
        return

    if text == "🔑 Redeem Invitation Code":
        await update.message.reply_text(
            "Kirim kode VOC.\nFormat: `VOC-XXXXXX`", parse_mode=ParseMode.MARKDOWN
        )
        return

    if text == "🔧 Admin Panel" and is_admin(user.id):
        await cmd_admin(update, ctx)
        return

    if text == "📥 Topup Log" and is_admin(user.id):
        topups = db.get_recent_topups(10)
        if not topups:
            await update.message.reply_text("Belum ada topup.")
            return
        import datetime as dtmod
        txt = "📥 *10 Topup Terakhir*\n\n"
        for t in topups:
            jam = dtmod.datetime.fromtimestamp(t['created_at']).strftime("%d/%m %H:%M")
            txt += f"🟢 Rp {t['amount']:,} | `{t['user_id']}` | {jam}\n"
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🆕 User Baru" and is_admin(user.id):
        users = db.get_recent_users(10)
        import datetime as dtmod
        txt = "🆕 *10 User Terbaru*\n\n"
        for u in users:
            jam = dtmod.datetime.fromtimestamp(u['created_at']).strftime("%d/%m %H:%M")
            uname = f"@{u['username']}" if u['username'] else "(no uname)"
            txt += f"👤 `{u['user_id']}` | {u['first_name']} {uname} | {jam}\n"
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
        return

    if text in ("🔙 Kembali ke Menu", "🔙 Menu Utama"):
        u = db.get_user(user.id)
        kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
        await update.message.reply_text(
            f"🏠 <b>Menu Utama</b>\n\n💰 Balance: <b>Rp {(u['saldo'] if u else 0):,}</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    if text == "📋 Cek Pesanan":
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM otp_orders WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
                (user.id,)
            ).fetchall()
        if not rows:
            await update.message.reply_text("❌ Tidak ada pesanan.")
            return
        o = rows[0]
        await update.message.reply_text(
            f"📋 <b>Pesanan</b>\n\nLayanan: {o['service_name']}\n"
            f"Nomor: <code>{o['phone']}</code>\nStatus: {o['status']}",
            parse_mode=ParseMode.HTML,
        )
        return

    if text.startswith("🛒 "):
        m = re.match(r"🛒\s+(.+?)\s+\(\d+\)\s+-\s+[\d,]+", text)
        if not m:
            await update.message.reply_text("❌ Format tombol tidak dikenali.")
            return
        name = m.group(1).strip()
        ctx.user_data['buy_service'] = name
        ctx.user_data['buy_state'] = 'WAITING_QTY'
        await update.message.reply_text(
            f"Berapa nomor <b>{name}</b>?\n\nKetik angka (contoh: 1)",
            parse_mode=ParseMode.HTML, reply_markup=await get_otp_menu_keyboard(),
        )
        return

    if text == "🔙 Balik ke Menu Utama" or text == "🏠 Menu Utama":
        kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
        await update.message.reply_text(
            "🏠 <b>Menu Utama</b>", 
            parse_mode=ParseMode.HTML, 
            reply_markup=kb
        )
        return

    if text == "📋 Riwayat Pesanan":
        # Panggil fungsi riwayat
        from otp_handlers import handle_history_callback
        
        # Bikin fake callback query biar bisa reuse fungsi history
        class FakeQuery:
            def __init__(self, msg):
                self.message = msg
            async def answer(self, *args, **kwargs): pass
            async def edit_message_text(self, *args, **kwargs): pass # Biar ga error, kirim baru aja
                
        class FakeUpdate:
            def __init__(self, user, msg):
                self.effective_user = user
                self.callback_query = FakeQuery(msg)
                
        # Kirim pesan history baru (karena ga bisa edit pesan yang ga ada inline button)
        trxs = db.get_user_transactions(user.id, limit=10)
        import datetime as dtmod
        
        res_text = "📋 <b>10 Riwayat Transaksi Terakhir</b>\n\n"
        if not trxs:
            res_text += "<i>Belum ada transaksi.</i>"
        else:
            for t in trxs:
                jam = dtmod.datetime.fromtimestamp(t['created_at']).strftime("%d/%m %H:%M")
                sign = "+" if t['type'] == "IN" else "-"
                icon = "🟢" if t['type'] == "IN" else "🔴"
                res_text += f"{icon} {sign}Rp {t['amount']:,} | {t['description']} ({jam})\n"
                
        await update.message.reply_text(res_text, parse_mode=ParseMode.HTML)
        return

    tu = text.upper()
    if len(tu) >= 3 and re.match(r"^[A-Z0-9_-]+$", tu) and db.get_voc(tu):
        ok, msg, quota = db.use_voc(tu, user.id)
        if ok:
            u = db.get_user(user.id)
            await update.message.reply_text(
                f"✅ *{msg}*\n\nBalance: *Rp {(u['saldo'] if u else 0):,}*",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(f"❌ {msg}")
        return

    await update.message.reply_text("❌ Format tidak dikenali.")

async def cmd_admin(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    s = db.get_stats()
    await update.message.reply_text(
        f"🔧 *Admin Panel*\n\n👥 Users: {s['total_users']}\n☕ Total cek: {s['total_cek']}\n\n"
        "/genvoc /listvoc /revoke /addsaldo /stats /users /userinfo /vocdetail",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_genvoc(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args or []
    if not args:
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, DEFAULT_VOC_QUOTA, update.effective_user.id):
                await update.message.reply_text(
                    f"✅ `{code}` | Rp {DEFAULT_VOC_QUOTA:,}", parse_mode=ParseMode.MARKDOWN
                )
                return
        return
    if len(args) == 1 and args[0].isdigit():
        quota = int(args[0])
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, quota, update.effective_user.id):
                await update.message.reply_text(
                    f"✅ `{code}` | Rp {quota:,}", parse_mode=ParseMode.MARKDOWN
                )
                return
        return
    if len(args) == 1:
        custom = sanitize_voc_input(args[0])
        if not custom:
            await update.message.reply_text("❌ Kode tidak valid.")
            return
        if db.create_voc(custom, DEFAULT_VOC_QUOTA, update.effective_user.id):
            await update.message.reply_text(
                f"✅ `{custom}` | Rp {DEFAULT_VOC_QUOTA:,}", parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(f"❌ `{custom}` sudah ada.")
        return
    if len(args) >= 2:
        custom = sanitize_voc_input(args[0])
        if not custom or not args[1].isdigit():
            await update.message.reply_text("❌ Format: `/genvoc <KODE> <SALDO>`", parse_mode=ParseMode.MARKDOWN)
            return
        quota = int(args[1])
        if db.create_voc(custom, quota, update.effective_user.id):
            await update.message.reply_text(
                f"✅ `{custom}` | Rp {quota:,}", parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(f"❌ `{custom}` sudah ada.")

async def cmd_listvoc(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    rows = db.list_voc_codes(20)
    if not rows:
        await update.message.reply_text("Kosong.")
        return
    lines = ["🔑 *20 VOC Terakhir*\n"]
    for r in rows:
        st = "🟢" if r["active"] else "🔴"
        lines.append(f"{st} `{r['code']}` — Rp {r['quota']:,}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_revoke(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/revoke <kode>`", parse_mode=ParseMode.MARKDOWN)
        return
    db.revoke_voc(ctx.args[0].upper())
    await update.message.reply_text(f"✅ `{ctx.args[0].upper()}` dinonaktifkan.", parse_mode=ParseMode.MARKDOWN)

async def cmd_addsaldo(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: `/addsaldo <id> <n>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0]); amt = int(ctx.args[1])
    except ValueError:
        return
    if not db.get_user(uid):
        await update.message.reply_text("❌ User tidak ada.")
        return
    db.add_saldo(uid, amt, "Admin Add Saldo")
    u = db.get_user(uid)
    await update.message.reply_text(
        f"✅ `{uid}` +{amt}. Total Rp {u['saldo']:,}", parse_mode=ParseMode.MARKDOWN
    )

async def cmd_stats(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    s = db.get_stats()
    await update.message.reply_text(
        f"📊 Users: {s['total_users']} | Cek: {s['total_cek']}", parse_mode=ParseMode.MARKDOWN
    )

async def cmd_users(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    users = db.list_all_users(50)
    if not users:
        await update.message.reply_text("Kosong.")
        return
    import datetime as dtmod
    lines = [f"👥 *{len(users)} User*\n"]
    for u in users:
        uname = f"@{u['username']}" if u['username'] else "-"
        dt = dtmod.datetime.fromtimestamp(u['created_at']).strftime("%d/%m %H:%M")
        lines.append(f"• {u['first_name']} {uname} | `{u['user_id']}` | Rp {u['saldo']:,} | {dt}")
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
        await update.message.reply_text(f"❌ `{uid}` tidak ada.")
        return
    import datetime as dtmod
    dt = dtmod.datetime.fromtimestamp(user['created_at']).strftime("%d/%m/%Y %H:%M")
    uname = f"@{user['username']}" if user['username'] else "-"
    txt = (
        f"👤 `{user['user_id']}`\nUsername: {uname}\nNama: {user['first_name'] or '-'}\n"
        f"💰 Rp {user['saldo']:,}\n📅 {dt}\n"
    )
    if vocs:
        txt += f"\n🔑 VOC dipakai ({len(vocs)}):\n"
        for v in vocs:
            txt += f"• `{v['code']}` Rp {v['quota']:,}\n"
    else:
        txt += "\n🔑 Belum redeem VOC."
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_vocdetail(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/vocdetail <kode>`", parse_mode=ParseMode.MARKDOWN)
        return
    code = ctx.args[0].upper()
    voc, user = db.get_voc_detail(code)
    if not voc:
        await update.message.reply_text(f"❌ `{code}` tidak ada.")
        return
    import datetime as dtmod
    dt = dtmod.datetime.fromtimestamp(voc['created_at']).strftime("%d/%m/%Y %H:%M")
    txt = (
        f"🔑 `{voc['code']}`\nRp {voc['quota']:,}\nDibuat: {dt}\n"
        f"Status: {'🟢 Aktif' if voc['active'] else '🔴 Terpakai'}\n"
    )
    if user:
        used = dtmod.datetime.fromtimestamp(voc['used_at']).strftime("%d/%m/%Y %H:%M") if voc['used_at'] else "-"
        uname = f"@{user['username']}" if user['username'] else "-"
        txt += f"\n👤 {user['first_name'] or '-'} {uname}\n`{user['user_id']}`\nRedeem: {used}"
    else:
        txt += "\n👤 Belum dipakai."
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def error_handler(update, ctx):
    logger.error(f"Error: {ctx.error}", exc_info=ctx.error)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.bot.set_webhook(url=f"{WEBHOOK_URL}/telegram-webhook", drop_pending_updates=True)
    logger.info(f"[TG] webhook set: {WEBHOOK_URL}/telegram-webhook")
    asyncio.create_task(tracker_worker(bot_app.bot))
    asyncio.create_task(bg_check_stuck_orders(bot_app))
    yield
    await bot_app.stop()
    await bot_app.shutdown()

api_app = FastAPI(lifespan=lifespan)

@api_app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return Response(status_code=200)

@api_app.post("/wahub-webhook")
async def wahub_webhook(request: Request):
    data = await request.json()
    order_id = str(data.get('order_id') or "")
    otp = data.get('otp')
    if order_id and otp:
        asyncio.create_task(process_webhook_otp(order_id, otp))
        return {"status": "ok"}
    return {"status": "ignored"}

async def process_webhook_otp(order_id, otp):
    order = db.get_otp_order(order_id)
    if not order or order['status'] != 'waiting':
        return
    if not db.mark_success_if_waiting(order_id, otp):
        return
    chat_id = order['user_id']
    text = build_order_ui(
        order_id, order['service_name'], order['phone'], "Selesai", 1200, otp
    )
    msg_id = order['msg_id']
    if msg_id:
        try:
            await bot_app.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_order_inline_keyboard(order_id, "Selesai"),
            )
            return
        except Exception:
            pass
    await bot_app.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
        reply_markup=get_order_inline_keyboard(order_id, "Selesai"),
    )

def main():
    global bot_app
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN kosong")
        return
    db.init_db()
    bot_app = Application.builder().token(BOT_TOKEN).build()

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
    bot_app.add_handler(CommandHandler("admin", cmd_admin))
    bot_app.add_handler(CommandHandler("genvoc", cmd_genvoc))
    bot_app.add_handler(CommandHandler("listvoc", cmd_listvoc))
    bot_app.add_handler(CommandHandler("revoke", cmd_revoke))
    bot_app.add_handler(CommandHandler("addsaldo", cmd_addsaldo))
    bot_app.add_handler(CommandHandler("stats", cmd_stats))
    bot_app.add_handler(CommandHandler("users", cmd_users))
    bot_app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    bot_app.add_handler(CommandHandler("vocdetail", cmd_vocdetail))

    bot_app.add_handler(CallbackQueryHandler(handle_cekstatus_callback, pattern=r"^cekstatus\|"))
    bot_app.add_handler(CallbackQueryHandler(handle_order_action_callback, pattern=r"^otp_act\|"))
    bot_app.add_handler(CallbackQueryHandler(handle_cek_kopken_callback, pattern=r"^otp_cekkopken\|"))
    bot_app.add_handler(CallbackQueryHandler(handle_buy_callback, pattern=r"^otp_buy\|"))
    bot_app.add_handler(CallbackQueryHandler(handle_history_callback, pattern=r"^otp_history$"))
    bot_app.add_handler(CallbackQueryHandler(handle_menu_back_callback, pattern=r"^otp_menu_back$"))

    bot_app.add_handler(MessageHandler(filters.LOCATION, handle_live_location))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot_app.add_error_handler(error_handler)

    uvicorn.run(api_app, host="0.0.0.0", port=8080, log_level="info")

if __name__ == "__main__":
    main()