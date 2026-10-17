import asyncio
import httpx
import os
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
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(f"{NASKY_BASE}/api/cek", headers={"X-API-Key": NASKY_API_KEY}, json={"code": "kopken", "nomor": nomor})
            return r.json()
        except Exception as e:
            return {"success": False, "detail": f"Network error: {e}"}

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
    service_name = ctx.user_data.get('buy_service')
    if not service_name:
        return
        
    services = await wahub_client.get_services()
    # Validasi nama layanan persis
    target_service = next((s for s in services if s['name'].lower() == service_name.lower()), None)
    
    if not target_service:
        await update.message.reply_text("❌ Layanan tidak ditemukan atau sedang tidak tersedia.")
        ctx.user_data.clear()
        # Balikin keyboard
        kb_bawah = await get_otp_keyboard()
        await update.message.reply_text("Menu OTP:", reply_markup=kb_bawah)
        return
        
    harga_satuan = target_service['price'] + MARGIN
    total = harga_satuan * qty
    
    u = db.get_user(user.id)
    saldo = u['saldo'] if u and 'saldo' in u.keys() else 0
    
    kb_bawah = await get_otp_keyboard()
    
    if saldo >= total:
        if not db.deduct_saldo(user.id, total):
            await update.message.reply_text("❌ Gagal memotong saldo.")
            return
            
        msg = await update.message.reply_text(f"⏳ Saldo cukup. Memproses pembelian {qty} nomor...", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
        
        numbers_bought = []
        for _ in range(qty):
            res, code = await wahub_client.rent_number(target_service['id'])
            if code == 200:
                db.create_otp_order(user.id, res['order_id'], res['token'], res['phone'], service_name, harga_satuan)
                numbers_bought.append((res['order_id'], res['token'], res['phone']))
            else:
                await msg.edit_text("❌ Gagal menyewa nomor dari provider. Saldo Anda telah dikembalikan.")
                db.add_saldo(user.id, total)
                ctx.user_data.clear()
                return
                
        txt = f"✅ <b>Pembelian Berhasil! Nomor Anda:</b>\n\n"
        for i, (oid, tkn, ph) in enumerate(numbers_bought, 1):
            txt += f"{i}. <code>{ph}</code>\n"
            ctx.bot_data[f"poll_{oid}"] = True
            asyncio.create_task(poll_otp_bg(ctx.bot, user.id, oid, tkn, service_name))
        await msg.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
        
        oid, tkn, ph = numbers_bought[0]
        ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": service_name, "price": total}
        
        order_kb = ReplyKeyboardMarkup([["❌ Batal & Refund", "🔄 Kirim Ulang OTP"], ["📋 Cek Pesanan", "🔙 Kembali ke Menu"]], resize_keyboard=True)
        
        if "kopken" in service_name.lower():
            cek = await nasky_cek_single(ph)
            if not cek.get("success"):
                promo_txt = (
                    f"⚠️ <b>Gagal Cek Kopken (Sistem Maintenance)</b>\n\n"
                    f"Nomor <code>{ph}</code> bisa langsung dipakai.\n"
                    f"Kalau mau beli nomor yang <b>PASTI AMAN & GA PERLU DI CEK</b>, disarankan beli <b>Kopken Filter</b> ya!"
                )
                await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML)
            else:
                promo_txt = f"💡 Nomor <code>{ph}</code> siap. Mau cek status Kopkennya? (Biaya Rp 200 jika FRESH)."
                cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{ph}")]])
                await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)
                
        await ctx.bot.send_message(chat_id=user.id, text="Pilih aksi di bawah:", reply_markup=order_kb)
        
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
                
                numbers_bought = []
                for _ in range(qty):
                    res, code = await wahub_client.rent_number(service_id)
                    if code == 200:
                        db.create_otp_order(chat_id, res['order_id'], res['token'], res['phone'], service_name, amount // qty)
                        numbers_bought.append((res['order_id'], res['token'], res['phone']))
                
                txt = f"✅ <b>Pembayaran Berhasil! Nomor Anda:</b>\n\n"
                for i, (oid, tkn, ph) in enumerate(numbers_bought, 1):
                    txt += f"{i}. <code>{ph}</code>\n"
                    ctx.bot_data[f"poll_{oid}"] = True
                    asyncio.create_task(poll_otp_bg(bot, chat_id, oid, tkn, service_name))
                await bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
                
                oid, tkn, ph = numbers_bought[0]
                ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": service_name, "price": amount}
                
                order_kb = ReplyKeyboardMarkup([["❌ Batal & Refund", "🔄 Kirim Ulang OTP"], ["📋 Cek Pesanan", "🔙 Kembali ke Menu"]], resize_keyboard=True)
                
                if "kopken" in service_name.lower():
                    cek = await nasky_cek_single(ph)
                    if not cek.get("success"):
                        promo_txt = (
                            f"⚠️ <b>Gagal Cek Kopken (Sistem Maintenance)</b>\n\n"
                            f"Nomor <code>{ph}</code> bisa langsung dipakai.\n"
                            f"Kalau mau beli nomor yang <b>PASTI AMAN & GA PERLU DI CEK</b>, disarankan beli <b>Kopken Filter</b> ya!"
                        )
                        await bot.send_message(chat_id=chat_id, text=promo_txt, parse_mode=ParseMode.HTML)
                    else:
                        promo_txt = f"💡 Nomor <code>{ph}</code> siap. Mau cek status Kopkennya? (Biaya Rp 200 jika FRESH)."
                        cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{ph}")]])
                        await bot.send_message(chat_id=chat_id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)
                        
                await bot.send_message(chat_id=chat_id, text="Pilih aksi di bawah:", reply_markup=order_kb)
                
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

async def poll_otp_bg(bot, user_id, order_id, token, service_name):
    if not bot.bot_data.get(f"poll_{order_id}", True):
        return

    res, status = await wahub_client.check_otp(token, timeout=60)
    if status == 200 and res.get('state') == 'success':
        otp = res.get('otp')
        db.update_otp_order_status(order_id, 'success', otp)
        await wahub_client.mark_done(order_id)
        await bot.send_message(chat_id=user_id, text=f"🎉 <b>OTP DITERIMA!</b>\n\n📱 Layanan: {service_name}\n🔑 Kode OTP: <code>{otp}</code>", parse_mode=ParseMode.HTML)
        return
        
    db_order = db.get_otp_order(order_id)
    if db_order and db_order['status'] != 'waiting': return 
        
    res2, status2 = await wahub_client.check_otp(token, timeout=60)
    if status2 == 200 and res2.get('state') == 'success':
        otp = res2.get('otp')
        db.update_otp_order_status(order_id, 'success', otp)
        await wahub_client.mark_done(order_id)
        await bot.send_message(chat_id=user_id, text=f"🎉 <b>OTP DITERIMA!</b>\n\n📱 Layanan: {service_name}\n🔑 Kode OTP: <code>{otp}</code>", parse_mode=ParseMode.HTML)
    else:
        db_order = db.get_otp_order(order_id)
        if db_order and db_order['status'] == 'waiting':
            cxl_res, cxl_status = await wahub_client.cancel_order(order_id)
            if cxl_status == 200:
                db.update_otp_order_status(order_id, 'expired')
                db.add_saldo(user_id, db_order['price'])
                await bot.send_message(chat_id=user_id, text=f"⏰ <b>Waktu Habis</b>\n\nPesanan dibatalkan dan saldo Rp {db_order['price']:,} dikembalikan.", parse_mode=ParseMode.HTML)

async def handle_cekstatus_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Sistem sedang mengecek pembayaran Anda secara otomatis di background. Mohon tunggu.", show_alert=True)