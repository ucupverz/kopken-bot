import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

import database as db
import wahub_client

# States
CHOOSE_SERVICE, WAITING_OTP = range(2)

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    text = (
        f"📱 <b>Menu Beli Nomor OTP</b>\n\n"
        f"💰 Saldo Anda: <b>Rp {saldo:,}</b>\n\n"
        f"Silakan pilih layanan di bawah ini. Harga sudah termasuk markup."
    )
    
    services = await wahub_client.get_services()
    if not services:
        await update.message.reply_text("❌ Gagal mengambil daftar layanan. Coba lagi nanti.")
        return
        
    keyboard = []
    for s in services:
        if s['stock'] > 0:
            # Markup example: + Rp 1000
            harga_jual = s['price'] + 1000 
            btn_text = f"{s['name']} | Rp {harga_jual:,} (Stok: {s['stock']})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"otp_buy_{s['id']}_{harga_jual}_{s['name']}")])
        else:
            keyboard.append([InlineKeyboardButton(f"{s['name']} (Habis)", callback_data="otp_none")])
            
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    
    data_parts = query.data.split("_")
    if data_parts[1] == "none":
        return
        
    service_id = int(data_parts[2])
    harga_jual = int(data_parts[3])
    service_name = data_parts[4]
    
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    if saldo < harga_jual:
        await query.edit_message_text(f"❌ Saldo tidak cukup. Sisa saldo: Rp {saldo:,}. Minimal butuh Rp {harga_jual:,}.")
        return
        
    if not db.deduct_saldo(user.id, harga_jual):
        await query.edit_message_text("❌ Gagal memotong saldo. Coba lagi.")
        return
        
    await query.edit_message_text("⏳ Sedang menyewa nomor...")
    
    res, status_code = await wahub_client.rent_number(service_id)
    if status_code != 200:
        # Refund
        db.add_saldo(user.id, harga_jual)
        error_msg = res.get('detail', res.get('message', 'Stok habis atau error sistem.'))
        await query.edit_message_text(f"❌ Gagal menyewa nomor: {error_msg}\n\nSaldo Anda telah dikembalikan.")
        return
        
    order_id = res['order_id']
    token = res['token']
    phone = res['phone']
    
    db.create_otp_order(user.id, order_id, token, phone, service_name, harga_jual)
    
    text = (
        f"✅ <b>Nomor Berhasil Disewa!</b>\n\n"
        f"📱 Layanan: {service_name}\n"
        f"📞 Nomor: <code>{phone}</code>\n\n"
        f"⏳ Silakan gunakan nomor ini untuk menerima OTP. Bot akan otomatis mendeteksi kode yang masuk (maks 2 menit)."
    )
    
    keyboard = [[InlineKeyboardButton("❌ Batalkan & Refund", callback_data=f"otp_cancel_{order_id}")]]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Start polling in background
    asyncio.create_task(poll_otp_bg(ctx.bot, user.id, order_id, token, service_name))

async def poll_otp_bg(bot, user_id, order_id, token, service_name):
    # Poll 1: 60s
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
        
    # If timeout, check if cancelled manually by user
    db_order = db.get_otp_order(order_id)
    if db_order and db_order['status'] != 'waiting':
        return # User manually cancelled
        
    # Poll 2 (optional, if user didn't cancel): another 60s
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
        # Auto-cancel & refund
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
        refund_amount = db_order['price'] # Full refund to user
        db.add_saldo(db_order['user_id'], refund_amount)
        await query.edit_message_text(
            f"✅ Pesanan dibatalkan.\nSaldo Rp {refund_amount:,} telah dikembalikan ke akun Anda.",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ Gagal membatalkan pesanan di server. Coba lagi nanti.")