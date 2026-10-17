import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
import wahub_client

# Sesuaikan dengan nama persis di API WAHub
QUICK_SERVICES = ["Kopken", "Kopken Filter", "Shopee", "Shopee Filter"] 
MARGIN = 700

# Link web dehuyz untuk pembayaran QRIS
WAHUB_TOPUP_URL = "https://dehuyzotp.shop/dashboard" 

def otp_keyboard():
    return ReplyKeyboardMarkup([
        ["🛒 Kopken", "🛒 Kopken Filter"],
        ["🛒 Shopee", "🛒 Shopee Filter"],
        ["💰 Deposit Saldo", "🔙 Kembali ke Menu"]
    ], resize_keyboard=True)

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    text = (
        f"📱 <b>Menu WhatsApp OTP</b>\n\n"
        f"Silakan pilih layanan di keyboard bawah.\n"
        f"• <b>Direct Buy</b>: Langsung bayar via QRIS sesuai jumlah nomor.\n"
        f"• <b>Deposit</b>: Isi saldo dulu (min Rp 5.000), lalu beli."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=otp_keyboard())

async def handle_direct_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE, service_name: str):
    user = update.effective_user
    await update.message.reply_text(
        f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", 
        parse_mode=ParseMode.HTML,
        reply_markup=None # Hilangkan keyboard sementara
    )
    ctx.user_data['buy_service'] = service_name
    ctx.user_data['buy_state'] = 'WAITING_QTY'

async def process_direct_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE, qty: int):
    user = update.effective_user
    service_name = ctx.user_data.get('buy_service')
    if not service_name:
        return
        
    services = await wahub_client.get_services()
    target_service = next((s for s in services if s['name'].lower() == service_name.lower()), None)
    
    if not target_service:
        await update.message.reply_text("❌ Layanan tidak ditemukan.")
        ctx.user_data.clear()
        return
        
    harga_satuan = target_service['price'] + MARGIN
    total = harga_satuan * qty
    
    ctx.user_data['buy_qty'] = qty
    ctx.user_data['buy_total'] = total
    ctx.user_data['buy_id'] = target_service['id']
    
    text = (
        f"🧾 <b>Detail Pembelian</b>\n\n"
        f"Layanan: {service_name}\n"
        f"Jumlah: {qty} nomor\n"
        f"Harga: Rp {harga_satuan:,}/nomor\n"
        f"<b>Total: Rp {total:,}</b>\n\n"
        f"Silakan scan QRIS di link bawah. Setelah bayar, klik <b>Saya Sudah Bayar</b>."
    )
    
    keyboard = [
        [InlineKeyboardButton("💳 Buka Halaman QRIS", url=WAHUB_TOPUP_URL)],
        [InlineKeyboardButton("✅ Saya Sudah Bayar", callback_data=f"otp_checkpay_{qty}_{total}")]
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "💰 <b>Deposit Saldo OTP</b>\n\n"
        "Minimal deposit Rp 5.000.\n"
        "Ketik jumlah deposit yang diinginkan (contoh: 10000):"
    )
    ctx.user_data['deposit_state'] = 'WAITING_AMOUNT'
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=None)

async def process_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    if amount < 5000:
        await update.message.reply_text("❌ Minimal deposit Rp 5.000.")
        return
        
    qris_link = f"{WAHUB_TOPUP_URL}?amount={amount}" 
    
    text = (
        f"🧾 <b>Deposit Saldo</b>\n\n"
        f"Jumlah: <b>Rp {amount:,}</b>\n\n"
        f"Silakan scan QRIS di link bawah ini. Setelah bayar, tunggu 1-5 menit, saldo otomatis masuk."
    )
    
    keyboard = [[InlineKeyboardButton("💳 Buka Halaman Pembayaran QRIS", url=qris_link)]]
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("otp_checkpay_"):
        parts = data.split("_")
        qty = int(parts[2])
        total = int(parts[3])
        service_name = ctx.user_data.get('buy_service')
        service_id = ctx.user_data.get('buy_id')
        
        if not service_id:
            await query.edit_message_text("❌ Sesi pembelian habis. Silakan ulangi.")
            return
            
        await query.edit_message_text("⏳ Memverifikasi pembayaran dan menyewa nomor...")
        
        numbers_bought = []
        for _ in range(qty):
            res, status_code = await wahub_client.rent_number(service_id)
            if status_code == 200:
                order_id = res['order_id']
                token = res['token']
                phone = res['phone']
                
                db.create_otp_order(query.from_user.id, order_id, token, phone, service_name, total // qty)
                numbers_bought.append((order_id, token, phone))
            else:
                await query.edit_message_text("❌ Gagal menyewa nomor. Saldo provider mungkin habis atau pembayaran belum masuk.")
                return
                
        text = f"✅ <b>Pembelian Berhasil!</b>\n\n"
        text += f"Layanan: {service_name}\n"
        for i, (oid, tkn, ph) in enumerate(numbers_bought, 1):
            text += f"{i}. Nomor: <code>{ph}</code>\n"
        text += f"\nSilakan gunakan nomor ini. OTP akan dikirim otomatis."
        
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        
        for oid, tkn, ph in numbers_bought:
            asyncio.create_task(poll_otp_bg(ctx.bot, query.from_user.id, oid, tkn, service_name))
            
        ctx.user_data.clear()

async def handle_all_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Fitur lihat semua layanan bisa ditambahkan nanti.")

async def handle_back_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Kembali ke menu...")

async def poll_otp_bg(bot, user_id, order_id, token, service_name):
    res, status = await wahub_client.check_otp(token, timeout=60)
    
    if status == 200 and res.get('state') == 'success':
        otp = res.get('otp')
        db.update_otp_order_status(order_id, 'success', otp)
        await wahub_client.mark_done(order_id)
        
        await bot.send_message(
            chat_id=user_id,
            text=f"🎉 <b>OTP DITERIMA!</b>\n\n📱 Layanan: {service_name}\n🔑 Kode OTP: <code>{otp}</code>",
            parse_mode=ParseMode.HTML
        )
        return
        
    db_order = db.get_otp_order(order_id)
    if db_order and db_order['status'] != 'waiting':
        return 
        
    res2, status2 = await wahub_client.check_otp(token, timeout=60)
    if status2 == 200 and res2.get('state') == 'success':
        otp = res2.get('otp')
        db.update_otp_order_status(order_id, 'success', otp)
        await wahub_client.mark_done(order_id)
        await bot.send_message(
            chat_id=user_id,
            text=f"🎉 <b>OTP DITERIMA!</b>\n\n📱 Layanan: {service_name}\n🔑 Kode OTP: <code>{otp}</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        db_order = db.get_otp_order(order_id)
        if db_order and db_order['status'] == 'waiting':
            cxl_res, cxl_status = await wahub_client.cancel_order(order_id)
            if cxl_status == 200:
                db.update_otp_order_status(order_id, 'expired')
                db.add_saldo(user_id, db_order['price'])
                await bot.send_message(
                    chat_id=user_id,
                    text=f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk dalam 2 menit. Pesanan dibatalkan dan saldo Rp {db_order['price']:,} telah dikembalikan 100%.",
                    parse_mode=ParseMode.HTML
                )

async def handle_cancel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split("_")[2])
    
    db_order = db.get_otp_order(order_id)
    if not db_order or db_order['status'] != 'waiting':
        await query.edit_message_text("❌ Pesanan sudah selesai atau tidak valid.")
        return
        
    await query.edit_message_text("⏳ Membatalkan pesanan...")
    res, status = await wahub_client.cancel_order(order_id)
    
    if status == 200:
        db.update_otp_order_status(order_id, 'cancelled')
        refund_amount = db_order['price'] 
        db.add_saldo(db_order['user_id'], refund_amount)
        await query.edit_message_text(
            f"✅ Pesanan dibatalkan.\nSaldo Rp {refund_amount:,} telah dikembalikan ke akun Anda.",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ Gagal membatalkan pesanan di server. Coba lagi nanti.")