import asyncio
import httpx
import os
import time
from datetime import datetime, timedelta
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

def get_menu_keyboard():
    return ReplyKeyboardMarkup([["📱 WhatsApp OTP", "💰 Cek Balance"], ["🔙 Kembali ke Menu"]], resize_keyboard=True)

async def get_otp_keyboard():
    services = await wahub_client.get_services()
    map_names = {
        "kopken": "Kopken Gacha",
        "kopken filter": "Kopken Asoy",
        "shopee": "Shope Gacha",
        "shopee filter": "Shope Asoy"
    }
    stock_map = {}
    for s in services:
        clean_name = s['name'].strip().lower()
        if clean_name in map_names:
            stock_map[map_names[clean_name]] = s
            
    def format_btn(name):
        if name in stock_map:
            s = stock_map[name]
            return f"🛒 {name} ({s['stock']}) - {s['price']+MARGIN:,}"
        return f"🛒 {name} (0) - 0"

    return ReplyKeyboardMarkup([
        [format_btn("Kopken Gacha"), format_btn("Kopken Asoy")],
        [format_btn("Shope Gacha"), format_btn("Shope Asoy")],
        ["📋 Cek Pesanan", "💰 Cek Balance"], ["🔙 Kembali ke Menu"]
    ], resize_keyboard=True)

def build_all_services_inline(services):
    map_names = {
        "kopken": "Kopken Gacha",
        "kopken filter": "Kopken Asoy",
        "shopee": "Shope Gacha",
        "shopee filter": "Shope Asoy"
    }
    keyboard = []
    row = []
    for s in services:
        clean_name = s['name'].strip().lower()
        if clean_name in map_names:
            display_name = map_names[clean_name]
            if s['stock'] > 0:
                harga_jual = s['price'] + MARGIN
                btn_text = f"{display_name} | {harga_jual:,} (Stok: {s['stock']})"
                # PAKE PIPE "|" BIAR SPASI AMAN
                row.append(InlineKeyboardButton(btn_text, callback_data=f"otp_buy|{s['id']}|{harga_jual}|{display_name}"))
            else:
                row.append(InlineKeyboardButton(f"{display_name} (Habis)", callback_data="otp_none"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

async def boostly_cek_single(nomor):
    phone = nomor if not nomor.startswith("62") else "0" + nomor[2:]
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{BOOSTLY_BASE}/api/v1/check", headers={"X-API-Key": BOOSTLY_API_KEY, "Content-Type": "application/json"}, json={"phone": phone})
            resp_json = r.json()
            if resp_json.get("success"):
                data_api = resp_json.get("data", {})
                status_api = data_api.get("status", "").upper()
                if status_api in ["TERDAFTAR", "REGISTERED"]:
                    return {"success": True, "status": "registered", "name": data_api.get("name")}
                elif status_api in ["BELUM TERDAFTAR", "FRESH", "NOT REGISTERED"]:
                    return {"success": True, "status": "fresh", "name": None}
            return {"success": False, "detail": "Format respon API tidak dikenal", "raw": resp_json}
        except Exception as e:
            return {"success": False, "detail": f"Network error: {e}", "raw": None}

def build_order_ui(order_id, service_name, phone, status="Menunggu OTP", time_left=1200, otp="-"):
    if status == "Menunggu OTP":
        status_line = f"⏳ <b>Menunggu OTP</b>\n<code>Exp: {int(time_left)}s</code>"
        otp_line = ""
    elif status == "Selesai":
        status_line = f"✅ <b>Pesanan Selesai</b>"
        otp_line = f"🔑 Kode OTP: <code>{otp}</code>\n"
    else:
        status_line = f"❌ Status: <b>{status}</b>"
        otp_line = ""
    return (
        f"📦 <b>Pesanan Aktif</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Order ID: <code>{order_id}</code>\n"
        f"📞 No. HP: <code>{phone}</code>\n"
        f"📱 Layanan: {service_name}\n"
        f"{status_line}\n{otp_line}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Jangan bagikan kode ini ke siapa pun.</i>"
    )

def get_order_inline_keyboard(status="Menunggu OTP", order_id=""):
    if status == "Menunggu OTP":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Ganti Nomor", callback_data=f"otp_act_ganti|{order_id}"), InlineKeyboardButton("❌ Batal & Refund", callback_data=f"otp_act_batal|{order_id}")],
            [InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data=f"otp_act_resend|{order_id}")]
        ])
    else:
        return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Selesai", callback_data=f"otp_act_selesai|{order_id}")]])

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    balance = u['saldo'] if u and 'saldo' in u.keys() else 0
    kb_bawah = await get_otp_keyboard()
    services = await wahub_client.get_services()
    if not services:
        await update.message.reply_text("❌ Gagal mengambil daftar layanan.")
        return
    text = f"📱 <b>Menu WhatsApp OTP</b>\n\n💰 Balance Anda: <b>Rp {balance:,}</b>\n\nSilakan pilih layanan dari list di bawah ini."
    inline_kb = build_all_services_inline(services)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=inline_kb)
    await update.message.reply_text("⬇️ Menu cepat:", reply_markup=kb_bawah)

async def process_direct_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE, qty: int):
    user = update.effective_user
    if ctx.user_data.get('is_processing'):
        await update.message.reply_text("⏳ Mohon tunggu, pesanan sebelumnya sedang diproses.")
        return
    ctx.user_data['is_processing'] = True

    display_name = ctx.user_data.get('buy_service')
    if not display_name:
        ctx.user_data['is_processing'] = False
        return
        
    map_back = {
        "kopken gacha": "kopken",
        "kopken asoy": "kopken filter",
        "shope gacha": "shopee",
        "shope asoy": "shopee filter"
    }
    api_name = map_back.get(display_name.lower(), display_name.lower())
        
    services = await wahub_client.get_services()
    target_service = next((s for s in services if s['name'].strip().lower() == api_name), None)
    
    if not target_service:
        await update.message.reply_text("❌ Layanan tidak ditemukan.")
        ctx.user_data['is_processing'] = False
        ctx.user_data.clear()
        return

    if target_service['stock'] < qty:
        await update.message.reply_text(f"❌ Maaf, stok {display_name} sedang habis atau tidak cukup.")
        ctx.user_data['is_processing'] = False
        ctx.user_data.clear()
        return
        
    harga_satuan = target_service['price'] + MARGIN
    total_harga = harga_satuan * qty
    u = db.get_user(user.id)
    balance = u['saldo'] if u and 'saldo' in u.keys() else 0
    kb_bawah = await get_otp_keyboard()
    
    if balance < total_harga:
        await update.message.reply_text(f"💰 Saldo Anda: Rp {balance:,}\n💸 Total: Rp {total_harga:,}\n\n❌ Saldo tidak cukup.", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
        ctx.user_data['is_processing'] = False
        return

    await update.message.reply_text(f"⏳ Sedang membuat pesanan...", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
    
    numbers_bought = []
    for i in range(qty):
        if not db.deduct_saldo(user.id, harga_satuan):
            break 
        res, code = await wahub_client.rent_number(target_service['id'])
        
        if code == 200 and res.get('order_id'):
            order_id = res['order_id']
            token = res.get('token')
            phone = res.get('phone')
            
            waktu_sekarang_wib = datetime.utcnow() + timedelta(hours=7)
            waktu_expired = waktu_sekarang_wib + timedelta(minutes=20)
            fmt_expired = waktu_expired.strftime("%H:%M")
            
            ui_text = build_order_ui(order_id, display_name, phone, "Menunggu OTP", 1200)
            ui_text = ui_text.replace("Exp: 1200s", f"Expired: {fmt_expired} WIB")
            
            msg_ui = await update.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP", order_id))
            
            db.create_otp_order(user.id, order_id, token, phone, display_name, harga_satuan, msg_ui.message_id)
            numbers_bought.append((order_id, token, phone))
            
            asyncio.create_task(poll_otp_bg(ctx.bot, user.id, order_id, token, display_name, msg_ui.message_id, phone))
            if ctx.job_queue:
                ctx.job_queue.run_once(task_timeout_job, when=1200, data={"user_id": user.id, "order_id": order_id, "msg_id": msg_ui.message_id, "service_name": display_name, "price": harga_satuan})
        else:
            err_msg = res.get('message') or res.get('error') or res.get('msg') or f"HTTP {code}"
            db.add_saldo(user.id, harga_satuan)
            await update.message.reply_text(f"❌ Gagal membuat pesanan ke-{i+1}.\nAlasan: <code>{err_msg}</code>\n\nSaldo dikembalikan.", parse_mode=ParseMode.HTML)
            break
            
    ctx.user_data['is_processing'] = False
    ctx.user_data['buy_state'] = None

async def task_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    oid, msg_id, price = data["order_id"], data["msg_id"], data["price"]
    
    if db.mark_expired_if_waiting(oid): # ATOMIC GUARD
        cxl_res, cxl_status = await wahub_client.cancel_order(oid)
        if cxl_status == 200:
            db.add_saldo(data["user_id"], price)
            try:
                await context.bot.edit_message_text(chat_id=data["user_id"], message_id=msg_id, text=f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk. Saldo Rp {price:,} dikembalikan.", parse_mode=ParseMode.HTML)
            except Exception: pass

async def poll_otp_bg(bot, user_id, order_id, token, service_name, msg_id, phone):
    start_time = time.time()
    while time.time() - start_time < 1200: 
        res, status = await wahub_client.check_otp(token, timeout=15)
        if status == 200 and res.get('state') == 'success':
            otp = res.get('otp')
            if db.mark_success_if_waiting(order_id, otp): # ATOMIC GUARD
                await wahub_client.mark_done(order_id)
                ui_text = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp)
                try:
                    await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Selesai", order_id))
                except Exception: pass
                return
        await asyncio.sleep(15)

async def handle_order_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("|")
    action = parts[0].replace("otp_act_", "")
    order_id = parts[1] if len(parts) > 1 else None
    user = update.effective_user
    
    if action == "selesai":
        try: await query.delete_message()
        except: pass
        return
        
    if not order_id:
        await query.message.reply_text("❌ ID Pesanan tidak valid.")
        return
        
    db_order = db.get_otp_order(order_id)
    if not db_order:
        await query.message.reply_text("❌ Pesanan tidak ditemukan.")
        return
        
    if action == "batal":
        try: await query.delete_message()
        except: pass
            
        if db.mark_expired_if_waiting(order_id): # ATOMIC GUARD
            res, code = await wahub_client.cancel_order(order_id)
            if code == 200:
                db.add_saldo(user.id, db_order['price'])
                await query.message.reply_text(f"✅ Pesanan dibatalkan. Saldo Rp {db_order['price']:,} dikembalikan.")
            else:
                await query.message.reply_text("❌ Gagal membatalkan di provider, tapi saldo Anda tidak dipotong ulang.")
        else:
            await query.message.reply_text("❌ Pesanan sudah selesai atau dibatalkan.")

    elif action == "ganti":
        await handle_ganti_nomor_inline(update, ctx, order_id)
        
    elif action == "resend":
        res, code = await wahub_client.retry_otp(db_order['token'])
        if code == 200:
            await query.message.reply_text("✅ Permintaan OTP ulang dikirim.")
        else:
            await query.message.reply_text("❌ Gagal minta ulang OTP.")

async def handle_ganti_nomor_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE, order_id_lama):
    query = update.callback_query
    user = update.effective_user
    db_order = db.get_otp_order(order_id_lama)
    if not db_order: return
        
    display_name = db_order['service_name']
    harga = db_order['price']
    map_back = {"kopken gacha": "kopken", "kopken asoy": "kopken filter", "shope gacha": "shopee", "shope asoy": "shopee filter"}
    api_name = map_back.get(display_name.lower(), display_name.lower())
    
    services = await wahub_client.get_services()
    target_service = next((s for s in services if s['name'].strip().lower() == api_name), None)
    
    if not target_service or target_service['stock'] < 1:
        await query.message.reply_text(f"❌ Maaf, stok {display_name} kosong.")
        return

    try: await query.delete_message()
    except: pass
        
    await query.message.reply_text("⏳ Membatalkan nomor lama & memproses nomor baru...")
    
    if not db.mark_expired_if_waiting(order_id_lama):
        await query.message.reply_text("❌ Pesanan lama sudah selesai.")
        return

    await wahub_client.cancel_order(order_id_lama)
    
    if not db.deduct_saldo(user.id, harga):
        await query.message.reply_text("❌ Gagal memotong saldo.")
        return

    res, code = await wahub_client.rent_number(target_service['id'])
    if code == 200 and res.get('order_id'):
        new_oid, new_tkn, new_ph = res['order_id'], res.get('token'), res.get('phone')
        
        waktu_sekarang_wib = datetime.utcnow() + timedelta(hours=7)
        fmt_expired = (waktu_sekarang_wib + timedelta(minutes=20)).strftime("%H:%M")
        
        ui_text = build_order_ui(new_oid, display_name, new_ph, "Menunggu OTP", 1200).replace("Exp: 1200s", f"Expired: {fmt_expired} WIB")
        msg_ui = await query.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP", new_oid))
        
        db.create_otp_order(user.id, new_oid, new_tkn, new_ph, display_name, harga, msg_ui.message_id)
        asyncio.create_task(poll_otp_bg(ctx.bot, user.id, new_oid, new_tkn, display_name, msg_ui.message_id, new_ph))
    else:
        db.add_saldo(user.id, harga)
        await query.message.reply_text("❌ Gagal mendapatkan nomor baru. Saldo dikembalikan.")

async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['deposit_state'] = 'WAITING_AMOUNT'
    await update.message.reply_text("💰 <b>Deposit Balance</b>\n\nMinimal Rp 5.000.\nKetik jumlah deposit (contoh: 10000):", parse_mode=ParseMode.HTML)

async def process_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    if amount < 5000:
        await update.message.reply_text("❌ Minimal deposit Rp 5.000.")
        return
    ctx.user_data['deposit_state'] = None 
    await update.message.reply_text(f"⏳ Memproses QRIS untuk Rp {amount:,}...", parse_mode=ParseMode.HTML)
    asyncio.create_task(process_qris_payment(ctx.bot, update.effective_chat.id, amount, ctx, is_direct_buy=False))

async def process_qris_payment(bot, chat_id, amount, ctx, is_direct_buy=False):
    status, result = await dehuyz_automation.request_qris(amount)
    if status == "error":
        await bot.send_message(chat_id, f"❌ Gagal membuat QRIS: {result}")
        return
        
    qris_msg = await bot.send_photo(chat_id=chat_id, photo=InputFile(result["image"], filename="qris.jpg"), caption=result["caption"], parse_mode=ParseMode.HTML)
    
    async def bg_wait():
        is_paid = await dehuyz_automation.wait_for_payment(amount, result["msg_id"])
        if is_paid == "success":
            try: await bot.edit_message_caption(chat_id=chat_id, message_id=qris_msg.message_id, caption=f"✅ <b>Topup Rp{amount:,} berhasil.</b>", parse_mode=ParseMode.HTML)
            except: pass
                
            if is_direct_buy:
                display_name = ctx.user_data.get('buy_service')
                service_id = ctx.user_data.get('buy_id')
                qty = ctx.user_data.get('buy_qty', 1)
                
                if not db.deduct_saldo(chat_id, amount, f"Beli {display_name}"):
                    await bot.send_message(chat_id, "❌ Gagal memotong saldo setelah bayar.")
                    return

                res, code = await wahub_client.rent_number(service_id)
                if code == 200 and res.get('order_id'):
                    oid, tkn, ph = res['order_id'], res['token'], res['phone']
                    harga_satuan = amount // qty
                    
                    waktu_sekarang_wib = datetime.utcnow() + timedelta(hours=7)
                    fmt_expired = (waktu_sekarang_wib + timedelta(minutes=20)).strftime("%H:%M")
                    
                    ui_text = build_order_ui(oid, display_name, ph, "Menunggu OTP", 1200).replace("Exp: 1200s", f"Expired: {fmt_expired} WIB")
                    msg_ui = await bot.send_message(chat_id, ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP", oid))
                    
                    db.create_otp_order(chat_id, oid, tkn, ph, display_name, harga_satuan, msg_ui.message_id)
                    asyncio.create_task(poll_otp_bg(bot, chat_id, oid, tkn, display_name, msg_ui.message_id, ph))
                else:
                    db.add_saldo(chat_id, amount)
                    await bot.send_message(chat_id, "❌ Gagal menyewa nomor setelah bayar. Saldo dikembalikan.")
            else:
                db.add_saldo(chat_id, amount)
                await bot.send_message(chat_id, f"✅ Deposit Rp {amount:,} Berhasil!", reply_markup=await get_otp_keyboard())
                
        elif is_paid == "timeout":
            try: await bot.edit_message_caption(chat_id=chat_id, message_id=qris_msg.message_id, caption="❌ <b>QRIS Kadaluarsa</b>", parse_mode=ParseMode.HTML)
            except: pass

    asyncio.create_task(bg_wait())

async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # SPLIT PAKE PIPE "|"
    parts = query.data.split("|")
    if len(parts) != 4: return
    
    _, service_id, harga_jual, service_name = parts
    ctx.user_data['buy_service'] = service_name
    ctx.user_data['buy_qty'] = 1
    ctx.user_data['buy_total'] = int(harga_jual)
    ctx.user_data['buy_id'] = int(service_id)
    await query.edit_message_text(f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka:", parse_mode=ParseMode.HTML)
    ctx.user_data['buy_state'] = 'WAITING_QTY'