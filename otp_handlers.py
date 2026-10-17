import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
import wahub_client
import dehuyz_automation

QUICK_SERVICES = ["Kopken", "Kopken Filter", "Shopee", "Shopee Filter"] 
MARGIN = 700

def otp_keyboard():
    return ReplyKeyboardMarkup([
        ["🛒 Kopken", "🛒 Kopken Filter"],
        ["🛒 Shopee", "🛒 Shopee Filter"],
        ["💰 Deposit Saldo", "🔙 Kembali ke Menu"]
    ], resize_keyboard=True)

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    text = (
        f"📱 <b>Menu WhatsApp OTP</b>\n\n"
        f"💰 Saldo Anda: <b>Rp {saldo:,}</b>\n\n"
        f"Silakan pilih layanan di keyboard bawah.\n"
        f"• <b>Direct Buy</b>: Langsung bayar via QRIS (jika saldo kurang).\n"
        f"• <b>Deposit</b>: Isi saldo dulu (min Rp 5.000)."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=otp_keyboard())

async def handle_direct_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE, service_name: str):
    await update.message.reply_text(
        f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", 
        parse_mode=ParseMode.HTML,
        reply_markup=None
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
    
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    # Cek apakah saldo user cukup
    if saldo >= total:
        # --- JIKA SALDO CUKUP: LANGSUNG BELI ---
        if not db.deduct_saldo(user.id, total):
            await update.message.reply_text("❌ Gagal memotong saldo.")
            return
            
        msg = await update.message.reply_text(f"⏳ Saldo cukup. Memproses pembelian {qty} nomor...", parse_mode=ParseMode.HTML)
        
        service_id = target_service['id']
        numbers_bought = []
        
        for _ in range(qty):
            res, code = await wahub_client.rent_number(service_id)
            if code == 200:
                db.create_otp_order(user.id, res['order_id'], res['token'], res['phone'], service_name, harga_satuan)
                numbers_bought.append((res['order_id'], res['token'], res['phone']))
            else:
                await msg.edit_text("❌ Gagal menyewa nomor dari provider. Saldo Anda telah dikembalikan.")
                db.add_saldo(user.id, total) # Refund total
                ctx.user_data.clear()
                return
                
        txt = f"✅ <b>Pembelian Berhasil! Nomor Anda:</b>\n\n"
        for i, (oid, tkn, ph) in enumerate(numbers_bought, 1):
            txt += f"{i}. <code>{ph}</code>\n"
            asyncio.create_task(poll_otp_bg(ctx.bot, user.id, oid, tkn, service_name))
        await msg.edit_text(txt, parse_mode=ParseMode.HTML)
        
    else:
        # --- JIKA SALDO KURANG: MINTA QRIS ---
        await update.message.reply_text(
            f"💰 Saldo Anda: Rp {saldo:,}\n"
            f"💸 Total Harga: Rp {total:,}\n\n"
            f"Saldo tidak cukup. Silakan bayar via QRIS di bawah ini untuk melanjutkan.", 
            parse_mode=ParseMode.HTML
        )
        
        ctx.user_data['buy_qty'] = qty
        ctx.user_data['buy_total'] = total
        ctx.user_data['buy_id'] = target_service['id']
        
        asyncio.create_task(process_qris_payment(ctx.bot, update.effective_chat.id, total, ctx, is_direct_buy=True))

    ctx.user_data['buy_state'] = None # Reset state

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
    qris_msg_id = result["msg_id"] # Ambil ID pesan dari dehuyz
    
    keyboard = [[InlineKeyboardButton("🔄 Cek Status", callback_data=f"cekstatus_{amount}")]]
    
    qris_msg = await bot.send_photo(
        chat_id=chat_id, 
        photo=InputFile(qris_bytes, filename="qris.jpg"), 
        caption=caption_dehuy, 
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    async def bg_wait_and_process():
        # Kirim qris_msg_id ke fungsi wait_for_payment
        is_paid_status = await dehuyz_automation.wait_for_payment(amount, qris_msg_id)
        
        if is_paid_status == "success":
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id, 
                    message_id=qris_msg.message_id, 
                    caption=f"✅ <b>Topup Rp{amount:,} berhasil, saldo sudah masuk.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
            except Exception:
                pass
                
            if is_direct_buy:
                service_name = ctx.user_data.get('buy_service')
                service_id = ctx.user_data.get('buy_id')
                qty = ctx.user_data.get('buy_qty', 1)
                
                numbers_bought = []
                for _ in range(qty):
                    res, code = await wahub_client.rent_number(service_id)
                    if code == 200:
                        db.create_otp_order(chat_id, res['order_id'], res['token'], res['phone'], service_name, amount // qty)
                        numbers_bought.append((res['order_id'], res['token'], res['phone']))
                
                txt = f"✅ <b>Pembayaran Berhasil! Nomor Anda:</b>\n\n"
                for i, (oid, tkn, ph) in enumerate(numbers_bought, 1):
                    txt += f"{i}. <code>{ph}</code>\n"
                    asyncio.create_task(poll_otp_bg(bot, chat_id, oid, tkn, service_name))
                await bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
            else:
                db.add_saldo(chat_id, amount)
                u = db.get_user(chat_id)
                await bot.send_message(chat_id, f"✅ Deposit Berhasil! Saldo masuk Rp {amount:,}.\nTotal Saldo: Rp {u['saldo']:,}")
                
        elif is_paid_status == "timeout":
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id, 
                    message_id=qris_msg.message_id, 
                    caption=f"❌ <b>QRIS Kadaluarsa</b>\n\nWaktu pembayaran untuk Rp {amount:,} telah habis.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
            except Exception:
                pass

    asyncio.create_task(bg_wait_and_process())

async def handle_cekstatus_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Sistem sedang mengecek pembayaran Anda secara otomatis di background. Mohon tunggu.", show_alert=True)

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

async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass

async def handle_cancel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass

async def handle_all_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass

async def handle_back_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass