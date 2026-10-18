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
BIAYA_API_BOOSTLY = 100

BOOSTLY_BASE = "https://api.boostly.id"
BOOSTLY_API_KEY = os.getenv("BOOSTLY_API_KEY", "kpk_live_b3fa7a15fd240b44cb12938be0c7774f")

def get_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["📱 WhatsApp OTP", "💰 Cek Balance"],
        ["🔙 Kembali ke Menu"]
    ], resize_keyboard=True)

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

async def boostly_cek_single(nomor):
    phone = nomor
    if phone.startswith("62"):
        phone = "0" + phone[2:]
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{BOOSTLY_BASE}/api/v1/check", 
                headers={"X-API-Key": BOOSTLY_API_KEY, "Content-Type": "application/json"}, 
                json={"phone": phone}
            )
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
    menit = time_left // 60
    detik = time_left % 60
    
    if status == "Menunggu OTP":
        total_blocks = 20
        sisa_blocks = int((time_left / 1200) * total_blocks)
        if sisa_blocks < 0: sisa_blocks = 0
        progress_bar = "█" * sisa_blocks + "░" * (total_blocks - sisa_blocks)
        
        status_line = f"⏳ <b>Menunggu OTP</b>\n<code>[{progress_bar}] {menit:02d}:{detik:02d}</code>"
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
        f"{status_line}\n"
        f"{otp_line}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Jangan bagikan kode ini ke siapa pun.</i>"
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
        if not db.deduct_saldo(user.id, harga_satuan, f"Beli {display_name}"):
            break 
        res, code = await wahub_client.rent_number(target_service['id'])
        
        if code == 200 and res.get('order_id'):
            order_id = res['order_id']
            token = res.get('token')
            phone = res.get('phone')
            db.create_otp_order(user.id, order_id, token, phone, display_name, harga_satuan)
            numbers_bought.append((order_id, token, phone))
        else:
            err_msg = res.get('message') or res.get('error') or res.get('msg') or f"HTTP {code}"
            db.add_saldo(user.id, harga_satuan, "refund", f"Gagal beli {display_name}")
            await update.message.reply_text(f"❌ Gagal membuat pesanan ke-{i+1}.\n<b>Alasan:</b> <code>{err_msg}</code>\n\nBalance dikembalikan.", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
            break
            
    if not numbers_bought:
        ctx.user_data['is_processing'] = False
        return

    oid, tkn, ph = numbers_bought[0]
    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name, "price": harga_satuan, "time_left": 1200}
    
    ui_text = build_order_ui(oid, display_name, ph, "Menunggu OTP", 1200)
    msg_ui = await update.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP"))
    
    ctx.user_data['last_order']['msg_id'] = msg_ui.message_id
    ctx.user_data['ui_msg_id'] = msg_ui.message_id
    
    asyncio.create_task(run_timer(ctx.bot, user.id, msg_ui.message_id, oid, display_name, ph, 1200))
    asyncio.create_task(poll_otp_bg(ctx.bot, user.id, oid, tkn, display_name, msg_ui.message_id, ph))
    
    if len(numbers_bought) > 1:
        info_txt = f"✅ Berhasil membeli {len(numbers_bought)} nomor.\n\n"
        for idx, (o, t, p) in enumerate(numbers_bought, 1):
            info_txt += f"{idx}. <code>{p}</code>\n"
            if idx > 1:
                await asyncio.sleep(3) # JEDA BIAR GA LIMIT
                sub_msg = await ctx.bot.send_message(
                    chat_id=user.id, 
                    text=build_order_ui(o, display_name, p, "Menunggu OTP", 1200), 
                    parse_mode=ParseMode.HTML, 
                    reply_markup=get_order_inline_keyboard("Menunggu OTP")
                )
                asyncio.create_task(run_timer(ctx.bot, user.id, sub_msg.message_id, o, display_name, p, 1200))
                asyncio.create_task(poll_otp_bg(ctx.bot, user.id, o, t, display_name, sub_msg.message_id, p))
        await ctx.bot.send_message(chat_id=user.id, text=info_txt, parse_mode=ParseMode.HTML)

    if "kopken" in api_name.lower() and "filter" not in api_name.lower():
        promo_txt = f"💡 Nomor <code>{ph}</code> siap. Mau cek status Kopkennya sekarang?\n"
        promo_txt += f"*(Kamu punya 2x Free Trial cek gratis!)*"
        cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{ph}")]])
        await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)

    ctx.user_data['buy_state'] = None
    ctx.user_data['is_processing'] = False


async def run_timer(bot, chat_id, msg_id, order_id, service_name, phone, total_time):
    start_time = time.time()
    current_msg_id = msg_id
    
    while True:
        await asyncio.sleep(5) # Jeda 5 detik tiap update
        elapsed = int(time.time() - start_time)
        time_left = total_time - elapsed
        
        if time_left <= 0:
            try: await bot.delete_message(chat_id=chat_id, message_id=current_msg_id)
            except: pass
            break
            
        order = db.get_otp_order(order_id)
        if not order or order['status'] != 'waiting':
            break
            
        ui_text = build_order_ui(order_id, service_name, phone, "Menunggu OTP", time_left)
        
        try:
            # HAPUS PESAN LAMA, KIRIM PESAN BARU BIAR GA STUCK
            await bot.delete_message(chat_id=chat_id, message_id=current_msg_id)
            new_msg = await bot.send_message(
                chat_id=chat_id, 
                text=ui_text, 
                parse_mode=ParseMode.HTML,
                reply_markup=get_order_inline_keyboard("Menunggu OTP")
            )
            current_msg_id = new_msg.message_id
        except Exception:
            pass


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
            
            ui_text = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp)
            try:
                await bot.delete_message(chat_id=user_id, message_id=msg_id)
                await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Selesai"))
            except Exception:
                pass
            return
            
        await asyncio.sleep(15)
        
    res, status = await wahub_client.check_otp(token, timeout=15)
    if status == 200 and res.get('state') == 'success':
        otp = res.get('otp')
        db.update_otp_order_status(order_id, 'success', otp)
        await wahub_client.mark_done(order_id)
        ui_text = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp)
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
            await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Selesai"))
        except Exception:
            pass
        return
        
    db_order = db.get_otp_order(order_id)
    if db_order and db_order['status'] == 'waiting':
        cxl_res, cxl_status = await wahub_client.cancel_order(order_id)
        if cxl_status == 200:
            db.update_otp_order_status(order_id, 'expired')
            db.add_saldo(user_id, db_order['price'], "refund", "Waktu habis (OTP tidak masuk)")
            
            ui_text = f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk. Balance Rp {db_order['price']:,} dikembalikan."
            try:
                await bot.delete_message(chat_id=user_id, message_id=msg_id)
                await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Selesai"))
            except Exception:
                pass


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
            
        res, code = await wahub_client.cancel_order(last_order['order_id'])
        
        if code == 200:
            refund = last_order.get('price', 0)
            db.add_saldo(user.id, refund, "refund", f"Batal pesanan {last_order['service']}")
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
        
        db.create_otp_order(user.id, new_oid, new_tkn, new_ph, display_name, harga)
        ctx.user_data['last_order'] = {"order_id": new_oid, "token": new_tkn, "phone": new_ph, "service": display_name, "price": harga}
        
        ui_text = build_order_ui(new_oid, display_name, new_ph, "Menunggu OTP", 1200)
        
        msg_ui = await query.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP"))
        ctx.user_data['ui_msg_id'] = msg_ui.message_id
        ctx.user_data['last_order']['msg_id'] = msg_ui.message_id
        
        asyncio.create_task(run_timer(ctx.bot, user.id, msg_ui.message_id, new_oid, display_name, new_ph, 1200))
        asyncio.create_task(poll_otp_bg(ctx.bot, user.id, new_oid, new_tkn, display_name, msg_ui.message_id, new_ph))
        
        if "kopken" in api_name.lower() and "filter" not in api_name.lower():
            promo_txt = f"💡 Nomor <code>{new_ph}</code> siap. Mau cek status Kopkennya sekarang?\n"
            promo_txt += f"*(Kamu punya 2x Free Trial cek gratis!)*"
            cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{new_ph}")]])
            await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)
        
    else:
        err_msg = res.get('message') or res.get('error') or "Unknown error"
        await query.message.reply_text(f"❌ Gagal mendapatkan nomor baru.\n<b>Alasan:</b> <code>{err_msg}</code>", parse_mode=ParseMode.HTML)


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
                    
                    db.create_otp_order(chat_id, oid, tkn, ph, display_name, harga_satuan)
                    ctx.bot_data[f"poll_{oid}"] = True
                    
                    ui_text = build_order_ui(oid, display_name, ph, "Menunggu OTP", 1200)
                    
                    msg_ui = await bot.send_message(chat_id, ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP"))
                    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name, "price": amount, "msg_id": msg_ui.message_id}
                    
                    asyncio.create_task(run_timer(bot, chat_id, msg_ui.message_id, oid, display_name, ph, 1200))
                    asyncio.create_task(poll_otp_bg(bot, chat_id, oid, tkn, display_name, msg_ui.message_id, ph))
                else:
                    err_msg = res.get('message') or res.get('error') or "Unknown error"
                    db.add_saldo(chat_id, amount, "refund", "Gagal sewa nomor")
                    await bot.send_message(chat_id, f"❌ Gagal menyewa nomor setelah bayar.\n<b>Alasan:</b> <code>{err_msg}</code>\n\nBalance Rp {amount:,} dikembalikan.", parse_mode=ParseMode.HTML)
                
            else:
                db.add_saldo(chat_id, amount, "topup", "Topup via QRIS")
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
    
    trial_tersisa = db.get_trial_kopken(user.id)
    
    await query.edit_message_text(f"🔍 Mengecek nomor `{phone}` ke Kopi Kenangan...", parse_mode=ParseMode.MARKDOWN)
    
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
        await query.edit_message_text(promo_txt, parse_mode=ParseMode.HTML)
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
                await query.edit_message_text(f"❌ Balance tidak cukup buat bayar biaya cek (Rp {BIAYA_CEK_FRESH:,}).")
                return
            db.deduct_saldo(user.id, BIAYA_CEK_FRESH, "Cek Nomor Kopken")
            reply = f"❌ Nomor `{phone}` BELUM TERDAFTAR (FRESH).\n\n💳 Balance terpotong: Rp {BIAYA_CEK_FRESH:,}."
            
    elif status == "registered":
        reply = f"✅ Nomor `{phone}` TERDAFTAR di Kopken.\nNama: {result.get('name') or '-'}\n\n💳 Cek GRATIS."
    else:
        reply = f"❓ Status tidak dikenal: `{status}`"
        
    await query.edit_message_text(reply, parse_mode=ParseMode.MARKDOWN)


async def handle_buy_filter_auto_cancel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    last_order = ctx.user_data.get('last_order')
    
    if last_order:
        await query.edit_message_text("⏳ Membatalkan pesanan sebelumnya...")
        res, code = await wahub_client.cancel_order(last_order['order_id'])
        if code == 200:
            refund = last_order.get('price', 0)
            db.add_saldo(user.id, refund, "refund", "Batal pesanan (beli Filter)")
            db.update_otp_order_status(last_order['order_id'], 'cancelled')
            ctx.user_data['last_order'] = None
            await query.message.reply_text(f"✅ Pesanan lama dibatalkan. Balance Rp {refund:,} dikembalikan.")
        else:
            await query.message.reply_text("⚠️ Gagal membatalkan pesanan lama, tapi lanjut proses beli Kopken Asoy.")

    data_parts = query.data.split("_")
    service_id = int(data_parts[2])
    harga_jual = int(data_parts[3])
    service_name = data_parts[4]
    
    ctx.user_data['buy_service'] = service_name
    ctx.user_data['buy_qty'] = 1
    ctx.user_data['buy_total'] = harga_jual
    ctx.user_data['buy_id'] = service_id
    
    await query.message.reply_text(
        f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", 
        parse_mode=ParseMode.HTML
    )
    ctx.user_data['buy_state'] = 'WAITING_QTY'