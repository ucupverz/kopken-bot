import asyncio
import httpx
import os
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
import wahub_client
import dehuyz_automation

QUICK_SERVICES = ["Kopken Gacha", "Kopken Asoy", "Shope Gacha", "Shope Asoy"] 
MARGIN = 700
BIAYA_CEK_FRESH = 200
NASKY_BASE = "https://nasky.web.id"
NASKY_API_KEY = os.getenv("NASKY_API_KEY", "")

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
        ["📋 Cek Pesanan", "💰 Cek Balance"],
        ["🔙 Kembali ke Menu"]
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
                row.append(InlineKeyboardButton(btn_text, callback_data=f"otp_buy_{s['id']}_{harga_jual}_{display_name}"))
            else:
                row.append(InlineKeyboardButton(f"{display_name} (Habis)", callback_data="otp_none"))
                
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

async def nasky_cek_single(nomor):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{NASKY_BASE}/api/cek", headers={"X-API-Key": NASKY_API_KEY}, json={"code": "kopken", "nomor": nomor})
            return r.json()
        except Exception as e:
            return {"success": False, "detail": f"Network error: {e}"}

def build_order_ui(order_id, service_name, phone, price, status="Menunggu OTP", expire_time=None, otp="-"):
    if expire_time:
        waktu_str = expire_time.strftime("%H:%M:%S")
        expire_line = f"Berlaku sampai: {waktu_str}"
    else:
        expire_line = ""

    if status == "Menunggu OTP":
        status_line = f"⏳ Menunggu OTP\n{expire_line}"
        otp_line = ""
    elif status == "Selesai":
        status_line = f"✅ Selesai"
        otp_line = f"🔑 Kode OTP: <code>{otp}</code>\n"
    else:
        status_line = f"❌ Status: {status}"
        otp_line = ""

    return (
        f"⏳ <b>Menunggu OTP</b>\n"
        f"Order #{order_id}\n"
        f"Layanan: {service_name}\n"
        f"Nomor: <code>{phone}</code>\n"
        f"Harga: Rp{price:,}\n"
        f"{status_line}\n"
        f"{otp_line}"
    )

def get_order_inline_keyboard(status="Menunggu OTP"):
    if status == "Menunggu OTP":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Ganti Nomor", callback_data="otp_act_ganti"),
                InlineKeyboardButton("❌ Batal & Refund", callback_data="otp_act_batal")
            ],
            [
                InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data="otp_act_resend")
            ]
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data="otp_act_resend"),
                InlineKeyboardButton("✅ Selesai", callback_data="otp_act_selesai")
            ]
        ])

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    balance = u['saldo'] if u and 'saldo' in u.keys() else 0
    kb_bawah = await get_otp_keyboard()
    services = await wahub_client.get_services()
    if not services:
        await update.message.reply_text("❌ Gagal mengambil daftar layanan.")
        return
    text = (
        f"📱 <b>Menu WhatsApp OTP</b>\n\n"
        f"💰 Balance Anda: <b>Rp {balance:,}</b>\n\n"
        f"Silakan pilih layanan dari list di bawah ini, atau dari keyboard bawah."
    )
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
        await update.message.reply_text("Menu OTP:", reply_markup=await get_otp_keyboard())
        return

    if target_service['stock'] < qty:
        await update.message.reply_text(f"❌ Maaf, stok {display_name} sedang habis atau tidak cukup (Sisa: {target_service['stock']}).", reply_markup=await get_otp_keyboard())
        ctx.user_data['is_processing'] = False
        ctx.user_data.clear()
        return
        
    harga_satuan = target_service['price'] + MARGIN
    total_harga_semua = harga_satuan * qty
    
    u = db.get_user(user.id)
    balance = u['saldo'] if u and 'saldo' in u.keys() else 0
    kb_bawah = await get_otp_keyboard()
    
    if balance < total_harga_semua:
        await update.message.reply_text(
            f"💰 Balance Anda: Rp {balance:,}\n"
            f"💸 Total Harga untuk {qty} nomor: Rp {total_harga_semua:,}\n\n"
            f"❌ Balance tidak cukup. Silakan deposit via QRIS di bawah ini.", 
            parse_mode=ParseMode.HTML, reply_markup=kb_bawah
        )
        ctx.user_data['buy_qty'] = qty
        ctx.user_data['buy_total'] = total_harga_semua
        ctx.user_data['buy_id'] = target_service['id']
        asyncio.create_task(process_qris_payment(ctx.bot, update.effective_chat.id, total_harga_semua, ctx, is_direct_buy=True))
        ctx.user_data['is_processing'] = False
        return

    await update.message.reply_text(f"⏳ Sedang membuat pesanan...", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
    
    numbers_bought = []
    for i in range(qty):
        try:
            res, code = await wahub_client.rent_number(target_service['id'])
        except Exception as e:
            await update.message.reply_text(f"❌ Error Provider: {e}")
            break
        
        if code == 200 and res.get('order_id'):
            order_id = res['order_id']
            token = res.get('token')
            phone = res.get('phone')
            
            if not db.deduct_saldo(user.id, harga_satuan):
                await wahub_client.cancel_order(order_id)
                break 
                
            db.create_otp_order(user.id, order_id, token, phone, display_name, harga_satuan)
            numbers_bought.append((order_id, token, phone))
        else:
            err_msg = res.get('message') or res.get('error') or res.get('msg') or f"HTTP {code}"
            await update.message.reply_text(f"❌ Gagal membuat pesanan ke-{i+1}.\n<b>Alasan:</b> <code>{err_msg}</code>", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
            break
            
    if not numbers_bought:
        ctx.user_data['is_processing'] = False
        return

    oid, tkn, ph = numbers_bought[0]
    expire_time = datetime.now() + timedelta(minutes=20)
    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name, "price": harga_satuan, "expire_time": expire_time}
    
    for idx, (o, t, p) in enumerate(numbers_bought):
        ui_text = build_order_ui(o, display_name, p, harga_satuan, "Menunggu OTP", expire_time)
        msg_ui = await ctx.bot.send_message(
            chat_id=user.id, 
            text=ui_text, 
            parse_mode=ParseMode.HTML, 
            reply_markup=get_order_inline_keyboard("Menunggu OTP")
        )
        
        if idx == 0:
            ctx.user_data['ui_msg_id'] = msg_ui.message_id
            ctx.user_data['last_order']['msg_id'] = msg_ui.message_id
        
        asyncio.create_task(expire_timer(ctx.bot, user.id, msg_ui.message_id, o, display_name, p, harga_satuan, expire_time))
        asyncio.create_task(poll_otp_bg(ctx.bot, user.id, o, t, display_name, msg_ui.message_id, p))
        await asyncio.sleep(1) 

    if "kopken" in api_name.lower() and "filter" not in api_name.lower():
        cek = await nasky_cek_single(ph)
        if not cek.get("success"):
            promo_txt = (
                f"⚠️ <b>Sistem Cek Kopken Sedang Maintenance.</b>\n\n"
                f"Lu ga perlu pusing, lu bisa langsung pakai nomor <code>{ph}</code> ini.\n"
                f"Kalau mau beli nomor yang <b>PASTI AMAN & GA PERLU DI CEK</b>, disarankan beli <b>Kopken Asoy</b> ya!"
            )
            await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML)
        else:
            promo_txt = f"💡 Nomor <code>{ph}</code> siap. Mau cek status Kopkennya sekarang?"
            cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{ph}")]])
            await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)

    ctx.user_data['buy_state'] = None
    ctx.user_data['is_processing'] = False

async def expire_timer(bot, chat_id, msg_id, order_id, service_name, phone, price, expire_time):
    now = datetime.now()
    time_diff = (expire_time - now).total_seconds()
    
    if time_diff > 0:
        await asyncio.sleep(time_diff)
        
    order = db.get_otp_order(order_id)
    if order and order['status'] == 'waiting':
        ui_text = f"⏰ <b>Waktu Habis</b>\n\nOTP untuk nomor <code>{phone}</code> tidak masuk. Balance Rp{price:,} dikembalikan."
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=None)
        except Exception:
            await bot.send_message(chat_id=chat_id, text=ui_text, parse_mode=ParseMode.HTML)
            
        db.update_otp_order_status(order_id, 'expired')
        db.add_saldo(chat_id, price)
        cxl_res, cxl_status = await wahub_client.cancel_order(order_id)

async def poll_otp_bg(bot, user_id, order_id, token, service_name, msg_id, phone):
    db_order = db.get_otp_order(order_id)
    if not db_order or db_order['status'] != 'waiting':
        return 

    start_time = time.time()
    while time.time() - start_time < 1200: 
        db_order = db.get_otp_order(order_id)
        if not db_order or db_order['status'] != 'waiting':
            return 

        res, status = await wahub_client.check_otp(token, timeout=15)
        if status == 200 and res.get('state') == 'success':
            otp = res.get('otp')
            db.update_otp_order_status(order_id, 'success', otp)
            await wahub_client.mark_done(order_id)
            
            ui_text = f"✅ <b>Pesanan Selesai</b>\n\n🆔 Order ID: <code>{order_id}</code>\n📱 Layanan: {service_name}\n🔑 Kode OTP: <code>{otp}</code>"
            try:
                await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception:
                await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML)
            return
            
        await asyncio.sleep(15)

async def handle_ganti_nomor_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    last_order = ctx.user_data.get('last_order')
    
    if not last_order:
        await query.message.reply_text("❌ Tidak ada pesanan aktif untuk diganti.")
        return
        
    order_id_lama = last_order['order_id']
    display_name = last_order['service']
    harga = last_order['price']
    
    map_back = {
        "kopken gacha": "kopken",
        "kopken asoy": "kopken filter",
        "shope gacha": "shopee",
        "shope asoy": "shopee filter"
    }
    api_name = map_back.get(display_name.lower(), display_name.lower())
    
    services = await wahub_client.get_services()
    target_service = next((s for s in services if s['name'].strip().lower() == api_name), None)
    
    if not target_service or target_service['stock'] < 1:
        await query.message.reply_text(f"❌ Maaf, stok {display_name} lagi kosong. Coba lagi nanti.")
        return

    try:
        await query.delete_message()
    except Exception:
        pass
        
    await query.message.reply_text("⏳ Membatalkan nomor lama & memproses nomor baru...")
    
    cxl_res, cxl_code = await wahub_client.cancel_order(order_id_lama)
    res, code = await wahub_client.rent_number(target_service['id'])
    
    if code == 200 and res.get('order_id'):
        new_oid = res['order_id']
        new_tkn = res.get('token')
        new_ph = res.get('phone')
        expire_time = datetime.now() + timedelta(minutes=20)
        
        db.create_otp_order(user.id, new_oid, new_tkn, new_ph, display_name, harga)
        ctx.user_data['last_order'] = {"order_id": new_oid, "token": new_tkn, "phone": new_ph, "service": display_name, "price": harga, "expire_time": expire_time}
        
        ui_text = build_order_ui(new_oid, display_name, new_ph, harga, "Menunggu OTP", expire_time)
        
        msg_ui = await query.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP"))
        ctx.user_data['ui_msg_id'] = msg_ui.message_id
        ctx.user_data['last_order']['msg_id'] = msg_ui.message_id
        
        asyncio.create_task(expire_timer(ctx.bot, user.id, msg_ui.message_id, new_oid, display_name, new_ph, harga, expire_time))
        asyncio.create_task(poll_otp_bg(ctx.bot, user.id, new_oid, new_tkn, display_name, msg_ui.message_id, new_ph))
        
        if "kopken" in api_name.lower() and "filter" not in api_name.lower():
            promo_txt = f"💡 Nomor <code>{new_ph}</code> siap. Mau cek status Kopkennya sekarang?"
            cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{new_ph}")]])
            await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)
        
    else:
        err_msg = res.get('message') or res.get('error') or "Unknown error"
        await query.message.reply_text(f"❌ Gagal mendapatkan nomor baru.\n<b>Alasan:</b> <code>{err_msg}</code>", parse_mode=ParseMode.HTML)

async def handle_order_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action = query.data.replace("otp_act_", "")
    user = update.effective_user
    last_order = ctx.user_data.get('last_order')
    
    if action == "selesai":
        try:
            await query.delete_message()
        except Exception:
            pass
        return
        
    if not last_order:
        await query.message.reply_text("❌ Tidak ada pesanan aktif.")
        return
        
    if action == "batal":
        try:
            await query.delete_message()
        except Exception:
            pass
            
        db_order = db.get_otp_order(last_order['order_id'])
        if not db_order or db_order['status'] != 'waiting':
            await query.message.reply_text("❌ Pesanan sudah selesai atau dibatalkan sebelumnya.")
            return
            
        res, code = await wahub_client.cancel_order(last_order['order_id'])
        
        if code == 200:
            refund = last_order.get('price', 0)
            db.add_saldo(user.id, refund)
            db.update_otp_order_status(last_order['order_id'], 'cancelled')
            ctx.user_data['last_order'] = None
            
            await query.message.reply_text(f"✅ Pesanan dibatalkan. Balance Rp {refund:,} dikembalikan.")
            kb = await get_otp_keyboard()
            await query.message.reply_text("Menu OTP:", reply_markup=kb)
        else:
            await query.message.reply_text("❌ Gagal membatalkan pesanan.")
            
    elif action == "ganti":
        await handle_ganti_nomor_inline(update, ctx)
        
    elif action == "resend":
        res, code = await wahub_client.retry_otp(last_order['token'])
        if code == 200:
            await query.message.reply_text("✅ Permintaan OTP ulang dikirim. Mohon tunggu SMS/WA masuk.")
        else:
            await query.message.reply_text("❌ Gagal minta ulang OTP. Mungkin batas retry habis atau waktu sewa habis.")

async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = "💰 <b>Deposit Balance</b>\n\nMinimal deposit Rp 5.000.\nKetik jumlah deposit yang diinginkan (contoh: 10000):"
    ctx.user_data['deposit_state'] = 'WAITING_AMOUNT'
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=None)

async def process_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    if amount < 5000:
        await update.message.reply_text("❌ Minimal deposit Rp 5.000.")
        return
    ctx.user_data['deposit_state'] = None 
    msg = await update.message.reply_text(f"⏳ Memproses QRIS untuk Rp {amount:,}...", parse_mode=ParseMode.HTML)
    asyncio.create_task(process_qris_payment(ctx.bot, update.effective_chat.id, amount, ctx, is_direct_buy=False))

async def process_qris_payment(bot, chat_id, amount, ctx, is_direct_buy=False):
    status, result = await dehuyz_automation.request_qris(amount)
    if status == "error":
        await bot.send_message(chat_id, f"❌ Gagal membuat QRIS: {result}")
        return
        
    qris_bytes = result["image"]
    caption_dehuy = result["caption"]
    qris_msg_id = result["msg_id"] 
    
    keyboard = [[InlineKeyboardButton("🔄 Cek Status", callback_data=f"cekstatus_{amount}")]]
    qris_msg = await bot.send_photo(
        chat_id=chat_id, photo=InputFile(qris_bytes, filename="qris.jpg"), 
        caption=caption_dehuy, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    async def bg_wait_and_process():
        is_paid_status = await dehuyz_automation.wait_for_payment(amount, qris_msg_id)
        if is_paid_status == "success":
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=qris_msg.message_id, caption=f"✅ <b>Topup Rp{amount:,} berhasil, balance sudah masuk.</b>", parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception: pass
                
            if is_direct_buy:
                display_name = ctx.user_data.get('buy_service')
                service_id = ctx.user_data.get('buy_id')
                qty = ctx.user_data.get('buy_qty', 1)
                
                res, code = await wahub_client.rent_number(service_id)
                
                if code == 200 and res.get('order_id'):
                    oid = res['order_id']
                    tkn = res['token']
                    ph = res['phone']
                    harga_satuan = amount // qty
                    expire_time = datetime.now() + timedelta(minutes=20)
                    
                    db.deduct_saldo(chat_id, amount)
                    db.create_otp_order(chat_id, oid, tkn, ph, display_name, harga_satuan)
                    ctx.bot_data[f"poll_{oid}"] = True
                    
                    ui_text = build_order_ui(oid, display_name, ph, harga_satuan, "Menunggu OTP", expire_time)
                    
                    msg_ui = await bot.send_message(chat_id, ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP"))
                    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name, "price": amount, "msg_id": msg_ui.message_id, "expire_time": expire_time}
                    
                    asyncio.create_task(expire_timer(bot, chat_id, msg_ui.message_id, oid, display_name, ph, harga_satuan, expire_time))
                    asyncio.create_task(poll_otp_bg(bot, chat_id, oid, tkn, display_name, msg_ui.message_id, ph))
                else:
                    err_msg = res.get('message') or res.get('error') or "Unknown error"
                    db.add_saldo(chat_id, amount)
                    await bot.send_message(chat_id, f"❌ Gagal menyewa nomor setelah bayar.\n<b>Alasan:</b> <code>{err_msg}</code>\n\nBalance Rp {amount:,} dikembalikan.", parse_mode=ParseMode.HTML)
                
            else:
                db.add_saldo(chat_id, amount)
                u = db.get_user(chat_id)
                kb = await get_otp_keyboard()
                await bot.send_message(chat_id, f"✅ Deposit Berhasil! Balance masuk Rp {amount:,}.\nTotal Balance: Rp {u['saldo']:,}", reply_markup=kb)
                
        elif is_paid_status == "timeout":
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=qris_msg.message_id, caption=f"❌ <b>QRIS Kadaluarsa</b>", parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception: pass

    asyncio.create_task(bg_wait_and_process())

async def handle_cekstatus_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Sistem sedang mengecek pembayaran Anda secara otomatis di background. Mohon tunggu.", show_alert=True)

async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data_parts = query.data.split("_")
    if len(data_parts) < 4 or data_parts[1] == "none": return
    service_id = int(data_parts[2])
    harga_jual = int(data_parts[3])
    service_name = data_parts[4]
    ctx.user_data['buy_service'] = service_name
    ctx.user_data['buy_qty'] = 1
    ctx.user_data['buy_total'] = harga_jual
    ctx.user_data['buy_id'] = service_id
    await query.edit_message_text(f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", parse_mode=ParseMode.HTML)
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
        promo_txt = (
            f"⚠️ <b>Sistem Cek Kopken Sedang Maintenance.</b>\n\n"
            f"Lu ga perlu pusing, lu bisa langsung pakai nomor <code>{phone}</code> ini.\n"
            f"Kalau mau beli nomor yang <b>PASTI AMAN & GA PERLU DI CEK</b>, disarankan beli <b>Kopken Asoy</b> ya!"
        )
        await query.edit_message_text(promo_txt, parse_mode=ParseMode.HTML)
        return
    d = result["data"]
    if d["status"] == "fresh":
        if saldo < 200:
            await query.edit_message_text("❌ Balance tidak cukup buat bayar biaya cek (Rp 200).")
            return
        db.deduct_saldo(user.id, 200)
        reply = f"❌ Nomor `{phone}` BELUM TERDAFTAR (FRESH).\n\n💳 Balance terpotong: Rp 200."
    elif d["status"] == "registered":
        reply = f"✅ Nomor `{phone}` TERDAFTAR di Kopken.\nNama: {d.get('customer_name') or '-'}\n\n💳 Cek GRATIS."
    else:
        reply = f"❓ Status tidak dikenal: `{d['status']}`"
    await query.edit_message_text(reply, parse_mode=ParseMode.MARKDOWN)