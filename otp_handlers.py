import asyncio
import httpx
import os
import time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
import wahub_client
import dehuyz_automation

QUICK_SERVICES = ["Kopken", "Kopken Filter", "Shopee", "Shopee Filter"] 
MARGIN = 700
BIAYA_CEK_FRESH = 200
NASKY_BASE = "https://nasky.web.id"
NASKY_API_KEY = os.getenv("NASKY_API_KEY", "")

async def get_otp_keyboard():
    services = await wahub_client.get_services()
    stock_map = {s['name']: s for s in services if s['name'] in QUICK_SERVICES}
    
    def format_btn(name):
        if name in stock_map:
            s = stock_map[name]
            return f"🛒 {name} ({s['stock']}) - {s['price']+MARGIN:,}"
        return f"🛒 {name} (0) - 0"

    return ReplyKeyboardMarkup([
        [format_btn("Kopken"), format_btn("Kopken Filter")],
        [format_btn("Shopee"), format_btn("Shopee Filter")],
        ["📋 Cek Pesanan", "💰 Deposit Saldo"],
        ["🔙 Kembali ke Menu"]
    ], resize_keyboard=True)

def build_all_services_inline(services):
    keyboard = []
    row = []
    for s in services:
        if s['stock'] > 0:
            harga_jual = s['price'] + MARGIN
            btn_text = f"{s['name']} | {harga_jual:,} (Stok: {s['stock']})"
            row.append(InlineKeyboardButton(btn_text, callback_data=f"otp_buy_{s['id']}_{harga_jual}_{s['name']}"))
        else:
            row.append(InlineKeyboardButton(f"{s['name']} (Habis)", callback_data="otp_none"))
            
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

# --- FUNGSI UI BOX PESANAN DENGAN PROGRESS BAR ---
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
        status_line = f"✅ Status: <b>Selesai</b>"
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
        f"<i>Gunakan tombol di bawah jika perlu.</i>"
    )

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    kb_bawah = await get_otp_keyboard()
    services = await wahub_client.get_services()
    
    if not services:
        await update.message.reply_text("❌ Gagal mengambil daftar layanan.")
        return

    text = (
        f"📱 <b>Menu WhatsApp OTP</b>\n\n"
        f"💰 Saldo Anda: <b>Rp {saldo:,}</b>\n\n"
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

    service_name = ctx.user_data.get('buy_service')
    if not service_name:
        ctx.user_data['is_processing'] = False
        return
        
    services = await wahub_client.get_services()
    target_service = next((s for s in services if s['name'].lower() == service_name.lower()), None)
    
    if not target_service:
        await update.message.reply_text("❌ Layanan tidak ditemukan.")
        ctx.user_data['is_processing'] = False
        ctx.user_data.clear()
        await update.message.reply_text("Menu OTP:", reply_markup=await get_otp_keyboard())
        return

    if target_service['stock'] < qty:
        await update.message.reply_text(f"❌ Maaf, stok {service_name} sedang habis atau tidak cukup (Sisa: {target_service['stock']}).", reply_markup=await get_otp_keyboard())
        ctx.user_data['is_processing'] = False
        ctx.user_data.clear()
        return
        
    harga_satuan = target_service['price'] + MARGIN
    total = harga_satuan * qty
    
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    kb_bawah = await get_otp_keyboard()
    
    if saldo >= total:
        if not db.deduct_saldo(user.id, total):
            await update.message.reply_text("❌ Gagal memotong saldo.")
            ctx.user_data['is_processing'] = False
            return

        await update.message.reply_text(f"⏳ Sedang membuat pesanan...", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
        
        numbers_bought = []
        for _ in range(qty):
            res, code = await wahub_client.rent_number(target_service['id'])
            
            if code == 200 and res.get('order_id'):
                order_id = res['order_id']
                token = res.get('token')
                phone = res.get('phone')
                
                db.create_otp_order(user.id, order_id, token, phone, service_name, harga_satuan)
                numbers_bought.append((order_id, token, phone))
            else:
                db.add_saldo(user.id, total)
                await update.message.reply_text("❌ Gagal membuat pesanan (Stok habis / API error). Saldo Anda dikembalikan.", reply_markup=kb_bawah)
                ctx.user_data['is_processing'] = False
                ctx.user_data.clear()
                return
                
        oid, tkn, ph = numbers_bought[0]
        ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": service_name, "price": total, "time_left": 1200}
        
        ui_text = build_order_ui(oid, service_name, ph, "Menunggu OTP", 1200)
        order_kb = ReplyKeyboardMarkup([
            ["❌ Batal & Refund", "🔄 Ganti Nomor"],
            ["🔄 Kirim Ulang OTP", "📋 Cek Pesanan"],
            ["🔙 Kembali ke Menu"]
        ], resize_keyboard=True)
        
        msg_ui = await update.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=order_kb)
        
        ctx.user_data['ui_msg_id'] = msg_ui.message_id
        
        # Jalanin Timer
        asyncio.create_task(run_timer(ctx.bot, user.id, msg_ui.message_id, oid, service_name, ph, 1200))
        asyncio.create_task(poll_otp_bg(ctx.bot, user.id, oid, tkn, service_name, msg_ui.message_id, ph))
        
        # === FITUR TOMBOL CEK KOPKEN ===
        if "kopken" in service_name.lower() and "filter" not in service_name.lower():
            promo_txt = f"💡 Nomor <code>{ph}</code> siap. Mau cek status Kopkennya sekarang?\n(Biaya Rp 200 jika FRESH, GRATIS jika sudah terdaftar)."
            cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{ph}")]])
            await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)
        
    else:
        await update.message.reply_text(
            f"💰 Saldo Anda: Rp {saldo:,}\n"
            f"💸 Total Harga: Rp {total:,}\n\n"
            f"Saldo tidak cukup. Silakan bayar via QRIS di bawah ini untuk melanjutkan.", 
            parse_mode=ParseMode.HTML, reply_markup=kb_bawah
        )
        ctx.user_data['buy_qty'] = qty
        ctx.user_data['buy_total'] = total
        ctx.user_data['buy_id'] = target_service['id']
        asyncio.create_task(process_qris_payment(ctx.bot, update.effective_chat.id, total, ctx, is_direct_buy=True))

    ctx.user_data['buy_state'] = None
    ctx.user_data['is_processing'] = False

async def run_timer(bot, chat_id, msg_id, order_id, service_name, phone, total_time):
    start_time = time.time()
    while True:
        await asyncio.sleep(5) # Update tiap 5 detik
        elapsed = int(time.time() - start_time)
        time_left = total_time - elapsed
        
        if time_left <= 0:
            break
            
        # Cek status pesanan dari DB
        order = db.get_otp_order(order_id)
        if not order or order['status'] != 'waiting':
            break
            
        # Update UI dengan waktu yang beneran mundur
        ui_text = build_order_ui(order_id, service_name, phone, "Menunggu OTP", time_left)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=ui_text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

async def poll_otp_bg(bot, user_id, order_id, token, service_name, msg_id, phone):
    db_order = db.get_otp_order(order_id)
    if not db_order or db_order['status'] != 'waiting':
        return 

    res, status = await wahub_client.check_otp(token, timeout=60)
    if status == 200 and res.get('state') == 'success':
        otp = res.get('otp')
        db.update_otp_order_status(order_id, 'success', otp)
        await wahub_client.mark_done(order_id)
        
        ui_text = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp)
        try:
            await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=ui_text, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(chat_id=user_id, text=f"🎉 <b>OTP DITERIMA!</b>\n\nKode: <code>{otp}</code>", parse_mode=ParseMode.HTML)
        return
        
    db_order = db.get_otp_order(order_id)
    if db_order and db_order['status'] != 'waiting': return 
        
    res2, status2 = await wahub_client.check_otp(token, timeout=60)
    if status2 == 200 and res2.get('state') == 'success':
        otp = res2.get('otp')
        db.update_otp_order_status(order_id, 'success', otp)
        await wahub_client.mark_done(order_id)
        
        ui_text = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp)
        try:
            await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=ui_text, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(chat_id=user_id, text=f"🎉 <b>OTP DITERIMA!</b>\n\nKode: <code>{otp}</code>", parse_mode=ParseMode.HTML)
    else:
        db_order = db.get_otp_order(order_id)
        if db_order and db_order['status'] == 'waiting':
            cxl_res, cxl_status = await wahub_client.cancel_order(order_id)
            if cxl_status == 200:
                db.update_otp_order_status(order_id, 'expired')
                db.add_saldo(user_id, db_order['price'])
                
                ui_text = f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk. Saldo Rp {db_order['price']:,} dikembalikan."
                try:
                    await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=ui_text, parse_mode=ParseMode.HTML)
                except Exception:
                    await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML)

async def handle_ganti_nomor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    last_order = ctx.user_data.get('last_order')
    
    if not last_order:
        await update.message.reply_text("❌ Tidak ada pesanan aktif untuk diganti.")
        return
        
    order_id_lama = last_order['order_id']
    service_name = last_order['service']
    harga = last_order['price']
    
    services = await wahub_client.get_services()
    target_service = next((s for s in services if s['name'].lower() == service_name.lower()), None)
    
    if not target_service or target_service['stock'] < 1:
        await update.message.reply_text(f"❌ Maaf, stok {service_name} lagi kosong. Coba lagi nanti.")
        return

    await update.message.reply_text("⏳ Membatalkan nomor lama & memproses nomor baru...")
    
    cxl_res, cxl_code = await wahub_client.cancel_order(order_id_lama)
    res, code = await wahub_client.rent_number(target_service['id'])
    
    if code == 200 and res.get('order_id'):
        new_oid = res['order_id']
        new_tkn = res.get('token')
        new_ph = res.get('phone')
        
        db.create_otp_order(user.id, new_oid, new_tkn, new_ph, service_name, harga)
        ctx.user_data['last_order'] = {"order_id": new_oid, "token": new_tkn, "phone": new_ph, "service": service_name, "price": harga}
        
        ui_text = build_order_ui(new_oid, service_name, new_ph, "Menunggu OTP", 1200)
        order_kb = ReplyKeyboardMarkup([
            ["❌ Batal & Refund", "🔄 Ganti Nomor"],
            ["🔄 Kirim Ulang OTP", "📋 Cek Pesanan"],
            ["🔙 Kembali ke Menu"]
        ], resize_keyboard=True)
        
        msg_ui = await update.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=order_kb)
        ctx.user_data['ui_msg_id'] = msg_ui.message_id
        
        asyncio.create_task(run_timer(ctx.bot, user.id, msg_ui.message_id, new_oid, service_name, new_ph, 1200))
        asyncio.create_task(poll_otp_bg(ctx.bot, user.id, new_oid, new_tkn, service_name, msg_ui.message_id, new_ph))
        
    else:
        await update.message.reply_text("❌ Gagal mendapatkan nomor baru dari provider. Saldo Anda aman.")

async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = "💰 <b>Deposit Saldo OTP</b>\n\nMinimal deposit Rp 5.000.\nKetik jumlah deposit yang diinginkan (contoh: 10000):"
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
                await bot.edit_message_caption(chat_id=chat_id, message_id=qris_msg.message_id, caption=f"✅ <b>Topup Rp{amount:,} berhasil, saldo sudah masuk.</b>", parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception: pass
                
            if is_direct_buy:
                service_name = ctx.user_data.get('buy_service')
                service_id = ctx.user_data.get('buy_id')
                qty = ctx.user_data.get('buy_qty', 1)
                
                res, code = await wahub_client.rent_number(service_id)
                
                if code == 200 and res.get('order_id'):
                    oid = res['order_id']
                    tkn = res['token']
                    ph = res['phone']
                    harga_satuan = amount // qty
                    
                    db.create_otp_order(chat_id, oid, tkn, ph, service_name, harga_satuan)
                    ctx.bot_data[f"poll_{oid}"] = True
                    
                    ui_text = build_order_ui(oid, service_name, ph, "Menunggu OTP", 1200)
                    order_kb = ReplyKeyboardMarkup([
                        ["❌ Batal & Refund", "🔄 Ganti Nomor"],
                        ["🔄 Kirim Ulang OTP", "📋 Cek Pesanan"],
                        ["🔙 Kembali ke Menu"]
                    ], resize_keyboard=True)
                    
                    msg_ui = await bot.send_message(chat_id, ui_text, parse_mode=ParseMode.HTML, reply_markup=order_kb)
                    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": service_name, "price": amount, "msg_id": msg_ui.message_id}
                    
                    asyncio.create_task(run_timer(bot, chat_id, msg_ui.message_id, oid, service_name, ph, 1200))
                    asyncio.create_task(poll_otp_bg(bot, chat_id, oid, tkn, service_name, msg_ui.message_id, ph))
                else:
                    db.add_saldo(chat_id, amount)
                    await bot.send_message(chat_id, f"❌ Gagal menyewa nomor setelah bayar. Saldo Rp {amount:,} dikembalikan.")
                
            else:
                db.add_saldo(chat_id, amount)
                u = db.get_user(chat_id)
                kb = await get_otp_keyboard()
                await bot.send_message(chat_id, f"✅ Deposit Berhasil! Saldo masuk Rp {amount:,}.\nTotal Saldo: Rp {u['saldo']:,}", reply_markup=kb)
                
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
            f"Kalau mau beli nomor yang <b>PASTI AMAN & GA PERLU DI CEK</b>, disarankan beli <b>Kopken Filter</b> ya!"
        )
        await query.edit_message_text(promo_txt, parse_mode=ParseMode.HTML)
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