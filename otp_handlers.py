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
        kb_bawah = await get_otp_keyboard()
        await update.message.reply_text("Menu OTP:", reply_markup=kb_bawah)
        return

    # Cek stok dulu
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
        # LOCK SALDO DULU
        if not db.deduct_saldo(user.id, total):
            await update.message.reply_text("❌ Gagal memotong saldo.")
            ctx.user_data['is_processing'] = False
            return

        msg = await update.message.reply_text(f"⏳ Sedang membuat pesanan...", parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
        
        numbers_bought = []
        for _ in range(qty):
            # Tembak API WAHub
            res, code = await wahub_client.rent_number(target_service['id'])
            
            if code == 200 and res.get('order_id'):
                order_id = res['order_id']
                token = res.get('token')
                phone = res.get('phone')
                
                # Jika API belum ngasih nomor, kita simpan order_id aja dulu.
                # Nanti nomor bakal dikirim otomatis via Webhook (FastAPI) ke UI
                db.create_otp_order(user.id, order_id, token, phone, service_name, harga_satuan)
                numbers_bought.append((order_id, token, phone))
            else:
                # Gagal tembak API, refund saldo
                db.add_saldo(user.id, total)
                await msg.edit_text("❌ Gagal membuat pesanan (Stok habis / API error). Saldo Anda dikembalikan.", reply_markup=kb_bawah)
                ctx.user_data['is_processing'] = False
                ctx.user_data.clear()
                return
                
        txt = f"✅ <b>Pesanan Dibuat! Menunggu Nomor dari Provider...</b>\n\n"
        for i, (oid, tkn, ph) in enumerate(numbers_bought, 1):
            txt += f"{i}. Order ID: <code>{oid}</code>\n"
            
        await msg.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb_bawah)
        
        oid, tkn, ph = numbers_bought[0]
        ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": service_name, "price": total}
        
        order_kb = ReplyKeyboardMarkup([["❌ Batal & Refund", "🔄 Kirim Ulang OTP"], ["📋 Cek Pesanan", "🔙 Kembali ke Menu"]], resize_keyboard=True)
        
        # Tampilkan UI Awal (Menunggu OTP)
        await ctx.bot.send_message(
            chat_id=user.id, 
            text=f"⏳ <b>Menunggu Nomor HP dari Provider...</b>\n\nOrder ID: <code>{oid}</code>\nLayanan: {service_name}", 
            parse_mode=ParseMode.HTML, 
            reply_markup=order_kb
        )
        
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