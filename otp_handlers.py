import asyncio
import httpx
import os
import re
import time
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import RetryAfter, BadRequest

import database as db
import wahub_client
import dehuyz_automation

MARGIN = 700
BIAYA_CEK_FRESH = 200

BOOSTLY_BASE = "https://api.boostly.id"
BOOSTLY_API_KEY = os.getenv("BOOSTLY_API_KEY", "kpk_live_b3fa7a15fd240b44cb12938be0c7774f")

MAP_DISPLAY = {
    "kopken": "Kopken Gacha",
    "kopken filter": "Kopken Asoy",
    "shopee": "Shope Gacha",
    "shopee filter": "Shope Asoy",
}
MAP_BACK = {v.lower(): k for k, v in MAP_DISPLAY.items()}
CALLBACK_SEP = "|"

# ---------------- keyboards ----------------

def get_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["📱 WhatsApp OTP", "💰 Cek Balance"],
        ["🔙 Kembali ke Menu"]
    ], resize_keyboard=True)

async def get_otp_keyboard():
    services = await wahub_client.get_services()
    stock_map = {}
    for s in services:
        clean = s['name'].strip().lower()
        if clean in MAP_DISPLAY:
            stock_map[MAP_DISPLAY[clean]] = s

    def fmt(name):
        s = stock_map.get(name)
        if s:
            return f"🛒 {name} ({s['stock']}) - {s['price']+MARGIN:,}"
        return f"🛒 {name} (0) - 0"

    return ReplyKeyboardMarkup([
        [fmt("Kopken Gacha"), fmt("Kopken Asoy")],
        [fmt("Shope Gacha"), fmt("Shope Asoy")],
        ["📋 Cek Pesanan", "💰 Cek Balance"],
        ["🔙 Kembali ke Menu"]
    ], resize_keyboard=True)

def build_all_services_inline(services):
    keyboard = []
    row = []
    for s in services:
        clean = s['name'].strip().lower()
        if clean not in MAP_DISPLAY:
            continue
        display = MAP_DISPLAY[clean]
        if s['stock'] > 0:
            harga = s['price'] + MARGIN
            btn = InlineKeyboardButton(
                f"{display} | {harga:,} (Stok: {s['stock']})",
                callback_data=f"otp_buy{CALLBACK_SEP}{s['id']}{CALLBACK_SEP}{harga}{CALLBACK_SEP}{display}"
            )
            row.append(btn)
        else:
            row.append(InlineKeyboardButton(f"{display} (Habis)", callback_data="otp_none"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def build_order_ui(order_id, service_name, phone, status="Menunggu OTP", time_left=1200, otp="-"):
    menit = max(time_left, 0) // 60
    detik = max(time_left, 0) % 60
    if status == "Menunggu OTP":
        blocks = 20
        sisa = max(0, min(blocks, int((time_left / 1200) * blocks)))
        bar = "█" * sisa + "░" * (blocks - sisa)
        status_line = f"⏳ <b>Menunggu OTP</b>\n<code>[{bar}] {menit:02d}:{detik:02d}</code>"
        otp_line = ""
    elif status == "Selesai":
        status_line = "✅ <b>Pesanan Selesai</b>"
        otp_line = f"🔑 Kode OTP: <code>{otp}</code>\n"
    else:
        status_line = f"❌ Status: <b>{status}</b>"
        otp_line = ""
    return (
        "📦 <b>Pesanan Aktif</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Order ID: <code>{order_id}</code>\n"
        f"📞 No. HP: <code>{phone}</code>\n"
        f"📱 Layanan: {service_name}\n"
        f"{status_line}\n"
        f"{otp_line}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Jangan bagikan kode ini ke siapa pun.</i>"
    )

def get_order_inline_keyboard(order_id, status="Menunggu OTP"):
    if status == "Menunggu OTP":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Ganti Nomor", callback_data=f"otp_act{CALLBACK_SEP}ganti{CALLBACK_SEP}{order_id}"),
                InlineKeyboardButton("❌ Batal & Refund", callback_data=f"otp_act{CALLBACK_SEP}batal{CALLBACK_SEP}{order_id}"),
            ],
            [
                InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data=f"otp_act{CALLBACK_SEP}resend{CALLBACK_SEP}{order_id}"),
            ],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data=f"otp_act{CALLBACK_SEP}resend{CALLBACK_SEP}{order_id}"),
            InlineKeyboardButton("✅ Selesai", callback_data=f"otp_act{CALLBACK_SEP}selesai{CALLBACK_SEP}{order_id}"),
        ],
    ])

# ---------------- boostly ----------------

async def boostly_cek_single(nomor):
    phone = nomor
    if phone.startswith("62"):
        phone = "0" + phone[2:]
    for attempt in range(2):
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                r = await client.post(
                    f"{BOOSTLY_BASE}/api/v1/check",
                    headers={"X-API-Key": BOOSTLY_API_KEY, "Content-Type": "application/json"},
                    json={"phone": phone},
                )
                data = r.json()
                if data.get("success"):
                    d = data.get("data", {})
                    st = (d.get("status") or "").upper()
                    if st in ("TERDAFTAR", "REGISTERED"):
                        return {"success": True, "status": "registered", "name": d.get("name")}
                    if st in ("BELUM TERDAFTAR", "FRESH", "NOT REGISTERED"):
                        return {"success": True, "status": "fresh", "name": None}
                return {"success": False, "detail": f"HTTP {r.status_code}", "raw": data}
            except Exception as e:
                if attempt == 1:
                    return {"success": False, "detail": f"Network: {e}", "raw": None}
                await asyncio.sleep(1)
    return {"success": False, "detail": "unknown", "raw": None}

# ---------------- order UI helpers ----------------

async def send_order_ui(bot, chat_id, order_id, service_name, phone, user_id, total_time=1200):
    text = build_order_ui(order_id, service_name, phone, "Menunggu OTP", total_time)
    msg = await bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
        reply_markup=get_order_inline_keyboard(order_id, "Menunggu OTP"),
    )
    db.set_order_msg_id(order_id, msg.message_id)
    asyncio.create_task(run_timer(bot, chat_id, msg.message_id, order_id, service_name, phone, total_time))
    asyncio.create_task(poll_otp_bg(bot, user_id, chat_id, order_id, service_name, phone, msg.message_id, total_time))
    return msg

async def run_timer(bot, chat_id, msg_id, order_id, service_name, phone, total_time):
    start = time.time()
    while True:
        await asyncio.sleep(5)
        left = total_time - int(time.time() - start)
        if left <= 0:
            return
        order = db.get_otp_order(order_id)
        if not order or order['status'] != 'waiting':
            return
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=build_order_ui(order_id, service_name, phone, "Menunggu OTP", left),
                parse_mode=ParseMode.HTML,
                reply_markup=get_order_inline_keyboard(order_id, "Menunggu OTP"),
            )
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except BadRequest:
            return
        except Exception:
            return

async def poll_otp_bg(bot, user_id, chat_id, order_id, service_name, phone, msg_id, total_time):
    start = time.time()
    while time.time() - start < total_time:
        order = db.get_otp_order(order_id)
        if not order or order['status'] != 'waiting':
            return
        res, status = await wahub_client.check_otp(order['token'], timeout=15)
        if status == 200 and res.get('state') == 'success':
            otp = res.get('otp')
            if db.mark_success_if_waiting(order_id, otp):
                await wahub_client.mark_done(order_id)
                ui = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp)
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id, text=ui,
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_order_inline_keyboard(order_id, "Selesai"),
                    )
                except Exception:
                    try:
                        await bot.send_message(
                            chat_id=chat_id, text=ui, parse_mode=ParseMode.HTML,
                            reply_markup=get_order_inline_keyboard(order_id, "Selesai"),
                        )
                    except Exception:
                        pass
            return
        await asyncio.sleep(15)

    order = db.get_otp_order(order_id)
    if not order or order['status'] != 'waiting':
        return
    res, status = await wahub_client.check_otp(order['token'], timeout=15)
    if status == 200 and res.get('state') == 'success':
        otp = res.get('otp')
        if db.mark_success_if_waiting(order_id, otp):
            await wahub_client.mark_done(order_id)
            ui = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id, text=ui,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_order_inline_keyboard(order_id, "Selesai"),
                )
            except Exception:
                pass
        return

    cxl_res, cxl_status = await wahub_client.cancel_order(order_id)
    if cxl_status == 200 and db.mark_status_if_waiting(order_id, 'expired'):
        db.add_saldo(user_id, order['price'], "Refund OTP Timeout")
        ui = f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk. Balance Rp {order['price']:,} dikembalikan."
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=ui,
                parse_mode=ParseMode.HTML,
                reply_markup=get_order_inline_keyboard(order_id, "Selesai"),
            )
        except Exception:
            pass

# ---------------- handlers ----------------

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    balance = u['saldo'] if u else 0
    services = await wahub_client.get_services()
    if not services:
        await update.message.reply_text("❌ Gagal mengambil daftar layanan.")
        return
    text = (
        "📱 <b>Menu WhatsApp OTP</b>\n\n"
        f"💰 Balance Anda: <b>Rp {balance:,}</b>\n\n"
        "Silakan pilih layanan di bawah ini."
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=build_all_services_inline(services)
    )

async def process_direct_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE, qty: int):
    user = update.effective_user
    if qty < 1:
        await update.message.reply_text("❌ Jumlah minimal 1.")
        return
    if ctx.user_data.get('is_processing'):
        await update.message.reply_text("⏳ Mohon tunggu, pesanan sebelumnya sedang diproses.")
        return
    ctx.user_data['is_processing'] = True
    try:
        display_name = ctx.user_data.get('buy_service')
        if not display_name:
            return
        api_name = MAP_BACK.get(display_name.lower(), display_name.lower())
        services = await wahub_client.get_services()
        target = next((s for s in services if s['name'].strip().lower() == api_name), None)
        if not target:
            await update.message.reply_text("❌ Layanan tidak ditemukan.")
            return
        if target['stock'] < qty:
            await update.message.reply_text(
                f"❌ Stok {display_name} tidak cukup (Sisa: {target['stock']})."
            )
            return

        harga = target['price'] + MARGIN
        total = harga * qty

        u = db.get_user(user.id)
        if (u['saldo'] if u else 0) < total:
            await update.message.reply_text(
                f"💰 Balance: Rp {(u['saldo'] if u else 0):,}\n"
                f"💸 Butuh: Rp {total:,}\n\n"
                "❌ Balance tidak cukup.",
                parse_mode=ParseMode.HTML,
            )
            ctx.user_data['buy_qty'] = qty
            ctx.user_data['buy_total'] = total
            ctx.user_data['buy_id'] = target['id']
            asyncio.create_task(process_qris_payment(
                ctx.bot, update.effective_chat.id, user.id, total, ctx, is_direct_buy=True
            ))
            return

        await update.message.reply_text("⏳ Membuat pesanan...")

        bought = []
        for i in range(qty):
            res, code = await wahub_client.rent_number(target['id'])
            if code == 200 and res.get('order_id'):
                oid = str(res['order_id'])
                token = res.get('token')
                phone = res.get('phone')
                if not db.deduct_saldo(user.id, harga, f"Beli {display_name}"):
                    await wahub_client.cancel_order(oid)
                    await update.message.reply_text("❌ Gagal potong saldo. Pesanan dibatalkan.")
                    break
                db.create_otp_order(user.id, oid, token, phone, display_name, harga)
                bought.append((oid, token, phone))
            else:
                err = res.get('message') or res.get('error') or res.get('msg') or f"HTTP {code}"
                await update.message.reply_text(
                    f"❌ Gagal pesanan ke-{i+1}.\n<b>Alasan:</b> <code>{err}</code>",
                    parse_mode=ParseMode.HTML,
                )
                break

        if not bought:
            return

        for idx, (oid, token, phone) in enumerate(bought):
            if idx > 0:
                await asyncio.sleep(2)
            await send_order_ui(
                ctx.bot, update.effective_chat.id, oid, display_name, phone, user.id
            )

        if "kopken" in api_name.lower() and "filter" not in api_name.lower():
            ph0 = bought[0][2]
            promo = (
                f"💡 Nomor <code>{ph0}</code> siap. Mau cek status Kopkennya sekarang?\n"
                "*(2x Free Trial cek gratis)*"
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken{CALLBACK_SEP}{ph0}")
            ]])
            await ctx.bot.send_message(
                chat_id=update.effective_chat.id, text=promo,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
    finally:
        ctx.user_data['buy_state'] = None
        ctx.user_data['is_processing'] = False

async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(CALLBACK_SEP)
    if len(parts) != 4:
        return
    _, sid, harga, name = parts
    try:
        sid_i = int(sid)
        harga_i = int(harga)
    except ValueError:
        return
    ctx.user_data['buy_service'] = name
    ctx.user_data['buy_qty'] = 1
    ctx.user_data['buy_total'] = harga_i
    ctx.user_data['buy_id'] = sid_i
    await query.edit_message_text(
        f"Berapa nomor <b>{name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)",
        parse_mode=ParseMode.HTML,
    )
    ctx.user_data['buy_state'] = 'WAITING_QTY'

async def handle_order_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(CALLBACK_SEP)
    if len(parts) != 3:
        return
    _, action, order_id = parts
    user = update.effective_user
    order = db.get_otp_order(order_id)

    if action == "selesai":
        try:
            await query.delete_message()
        except Exception:
            pass
        return

    if not order:
        await query.message.reply_text("❌ Pesanan tidak ditemukan.")
        return

    if order['user_id'] != user.id:
        await query.message.reply_text("❌ Bukan pesanan Anda.")
        return

    if action == "batal":
        if order['status'] != 'waiting':
            await query.message.reply_text(f"❌ Status pesanan: {order['status']}.")
            return
        res, code = await wahub_client.cancel_order(order_id)
        if code == 200 and db.mark_status_if_waiting(order_id, 'cancelled'):
            db.add_saldo(user.id, order['price'], "Refund Batal Pesanan")
            try:
                await query.edit_message_text(
                    f"✅ Dibatalkan. Balance Rp {order['price']:,} dikembalikan."
                )
            except Exception:
                await query.message.reply_text(
                    f"✅ Dibatalkan. Balance Rp {order['price']:,} dikembalikan."
                )
        else:
            await query.message.reply_text("❌ Gagal membatalkan pesanan.")
        return

    if action == "ganti":
        await handle_ganti_nomor_inline(update, ctx, order)
        return

    if action == "resend":
        if order['status'] != 'waiting':
            await query.message.reply_text("❌ Pesanan tidak aktif.")
            return
        res, code = await wahub_client.retry_otp(order['token'])
        if code == 200:
            await query.answer("Permintaan OTP ulang terkirim.", show_alert=True)
        else:
            await query.answer("Gagal minta ulang OTP.", show_alert=True)

async def handle_ganti_nomor_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE, order):
    query = update.callback_query
    user = update.effective_user
    order_id_lama = order['order_id']
    display_name = order['service_name']
    harga = order['price']

    api_name = MAP_BACK.get(display_name.lower(), display_name.lower())
    services = await wahub_client.get_services()
    target = next((s for s in services if s['name'].strip().lower() == api_name), None)
    if not target or target['stock'] < 1:
        await query.message.reply_text(f"❌ Stok {display_name} kosong.")
        return

    res_cxl, code_cxl = await wahub_client.cancel_order(order_id_lama)
    if code_cxl != 200:
        await query.message.reply_text("❌ Gagal batalkan nomor lama. Coba lagi.")
        return
    if not db.mark_status_if_waiting(order_id_lama, 'cancelled'):
        await query.message.reply_text("❌ Pesanan lama sudah tidak aktif.")
        return
    db.add_saldo(user.id, harga, "Refund Ganti Nomor")

    res, code = await wahub_client.rent_number(target['id'])
    if code != 200 or not res.get('order_id'):
        err = res.get('message') or res.get('error') or "unknown"
        await query.message.reply_text(
            f"❌ Gagal nomor baru: <code>{err}</code>. Balance lama sudah dikembalikan.",
            parse_mode=ParseMode.HTML,
        )
        return

    new_oid = str(res['order_id'])
    new_token = res.get('token')
    new_phone = res.get('phone')

    if not db.deduct_saldo(user.id, harga, f"Ganti Nomor {display_name}"):
        await wahub_client.cancel_order(new_oid)
        await query.message.reply_text("❌ Gagal potong saldo nomor baru.")
        return

    db.create_otp_order(user.id, new_oid, new_token, new_phone, display_name, harga)

    try:
        await query.delete_message()
    except Exception:
        pass

    await send_order_ui(
        ctx.bot, user.id, new_oid, display_name, new_phone, user.id
    )

    if "kopken" in api_name.lower() and "filter" not in api_name.lower():
        promo = (
            f"💡 Nomor <code>{new_phone}</code> siap.\n"
            "*(2x Free Trial cek gratis)*"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken{CALLBACK_SEP}{new_phone}")
        ]])
        await ctx.bot.send_message(
            chat_id=user.id, text=promo, parse_mode=ParseMode.HTML, reply_markup=kb
        )

# ---------------- deposit / QRIS ----------------

async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['deposit_state'] = 'WAITING_AMOUNT'
    await update.message.reply_text(
        "💰 <b>Deposit</b>\n\nMinimal Rp 5.000.\nKetik jumlah (contoh: 10000):",
        parse_mode=ParseMode.HTML,
    )

async def process_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    if amount < 5000:
        await update.message.reply_text("❌ Minimal deposit Rp 5.000.")
        return
    ctx.user_data['deposit_state'] = None
    await update.message.reply_text(f"⏳ Memproses QRIS Rp {amount:,}...")
    asyncio.create_task(process_qris_payment(
        ctx.bot, update.effective_chat.id, update.effective_user.id, amount, ctx, is_direct_buy=False
    ))

async def process_qris_payment(bot, chat_id, user_id, amount, ctx, is_direct_buy=False):
    status, result = await dehuyz_automation.request_qris(amount)
    if status == "error":
        await bot.send_message(chat_id, f"❌ Gagal buat QRIS: {result}")
        return

    qris_bytes = result["image"]
    caption = result["caption"]
    qris_msg_id = result["msg_id"]

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Cek Status", callback_data=f"cekstatus{CALLBACK_SEP}{amount}")
    ]])
    qris_msg = await bot.send_photo(
        chat_id=chat_id, photo=InputFile(qris_bytes, filename="qris.jpg"),
        caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb,
    )

    async def bg():
        paid = await dehuyz_automation.wait_for_payment(amount, qris_msg_id)
        if paid == "success":
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id, message_id=qris_msg.message_id,
                    caption=f"✅ <b>Pembayaran Rp {amount:,} diterima.</b>",
                    parse_mode=ParseMode.HTML, reply_markup=None,
                )
            except Exception:
                pass

            if is_direct_buy:
                display_name = ctx.user_data.get('buy_service')
                service_id = ctx.user_data.get('buy_id')
                qty = ctx.user_data.get('buy_qty', 1)
                harga_satuan = ctx.user_data.get('buy_total', amount) // max(qty, 1)

                bought = []
                for i in range(qty):
                    res, code = await wahub_client.rent_number(service_id)
                    if code == 200 and res.get('order_id'):
                        oid = str(res['order_id'])
                        token = res.get('token')
                        phone = res.get('phone')
                        if not db.deduct_saldo(user_id, harga_satuan, f"Beli {display_name}"):
                            await wahub_client.cancel_order(oid)
                            break
                        db.create_otp_order(user_id, oid, token, phone, display_name, harga_satuan)
                        bought.append((oid, token, phone))
                    else:
                        err = res.get('message') or res.get('error') or "unknown"
                        await bot.send_message(
                            chat_id, f"❌ Gagal sewa nomor: <code>{err}</code>",
                            parse_mode=ParseMode.HTML,
                        )
                        break

                if not bought:
                    db.add_saldo(user_id, amount, "Refund Gagal Order QRIS")
                    await bot.send_message(
                        chat_id, f"💰 Balance Rp {amount:,} dikembalikan."
                    )
                    return

                for idx, (oid, token, phone) in enumerate(bought):
                    if idx > 0:
                        await asyncio.sleep(2)
                    await send_order_ui(
                        bot, chat_id, oid, display_name, phone, user_id
                    )

                sisa = amount - harga_satuan * len(bought)
                if sisa > 0:
                    db.add_saldo(user_id, sisa, "Sisa QRIS")
            else:
                db.add_saldo(user_id, amount, f"Topup QRIS Rp {amount:,}")
                u = db.get_user(user_id)
                await bot.send_message(
                    chat_id,
                    f"✅ Deposit berhasil. Balance: Rp {(u['saldo'] if u else 0):,}",
                )
        elif paid == "timeout":
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id, message_id=qris_msg.message_id,
                    caption="❌ <b>QRIS Kadaluarsa</b>",
                    parse_mode=ParseMode.HTML, reply_markup=None,
                )
            except Exception:
                pass

    asyncio.create_task(bg())

async def handle_cekstatus_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Pembayaran dicek otomatis. Mohon tunggu.", show_alert=True)

# ---------------- kopken ----------------

async def _do_cek_kopken(user_id, phone):
    trial = db.get_trial_kopken(user_id)
    result = await boostly_cek_single(phone)
    if not result.get("success"):
        return (
            "⚠️ <b>Gagal cek API</b>\n\n"
            f"Detail: {result.get('detail')}\n"
            f"Raw: <code>{result.get('raw')}</code>",
            True,
        )
    st = result.get("status")
    if st == "fresh":
        if trial > 0:
            db.set_trial_kopken(user_id, trial - 1)
            return (
                f"❌ <code>{phone}</code> BELUM TERDAFTAR (FRESH).\n"
                f"🎁 Free trial dipakai. Sisa: {trial-1}x.",
                False,
            )
        u = db.get_user(user_id)
        if (u['saldo'] if u else 0) < BIAYA_CEK_FRESH:
            return (f"❌ Balance kurang dari Rp {BIAYA_CEK_FRESH:,}.", False)
        db.deduct_saldo(user_id, BIAYA_CEK_FRESH, "Cek Nomor Kopken")
        return (
            f"❌ <code>{phone}</code> BELUM TERDAFTAR (FRESH).\n"
            f"💳 Terpotong Rp {BIAYA_CEK_FRESH:,}.",
            False,
        )
    if st == "registered":
        return (
            f"✅ <code>{phone}</code> TERDAFTAR.\nNama: {result.get('name') or '-'}\n💳 GRATIS.",
            False,
        )
    return (f"❓ Status tidak dikenal: {st}", False)

async def handle_cek_kopken_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(CALLBACK_SEP)
    if len(parts) != 2:
        return
    phone = parts[1]
    reply, is_html = await _do_cek_kopken(update.effective_user.id, phone)
    await q.edit_message_text(
        reply, parse_mode=ParseMode.HTML if is_html else ParseMode.MARKDOWN
    )