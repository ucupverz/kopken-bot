import asyncio
import httpx
import os
import time
import logging
import datetime
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter

import database as db
import wahub_client
import dehuyz_automation

log = logging.getLogger("otp")

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

# === SESI ORDER (SATU PESAN UI PER ORDER, DI-EDIT TERUS) ===
SESSIONS = {}          # order_id -> dict sesi
RENT_SECONDS = 1200    # 20 menit


def _bar(time_left):
    blocks = max(0, min(20, int((time_left / RENT_SECONDS) * 20)))
    return "█" * blocks + "░" * (20 - blocks)


def build_order_ui(order_id, service_name, phone, otps, waiting, time_left, closed_text=None):
    head = (
        f"📦 <b>Pesanan Aktif</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Order ID: <code>{order_id}</code>\n"
        f"📞 No. HP: <code>{phone}</code>\n"
        f"📱 Layanan: {service_name}\n"
    )
    body = ""
    for i, code in enumerate(otps, 1):
        body += f"🔑 Kode OTP {i}: <code>{code}</code>\n"
    if closed_text:
        body += closed_text + "\n"
    elif waiting:
        m, d = divmod(max(time_left, 0), 60)
        label = "Menunggu OTP" if not otps else f"Menunggu OTP ke-{len(otps) + 1}"
        body += f"⏳ <b>{label}</b>\n<code>[{_bar(time_left)}] {m:02d}:{d:02d}</code>\n"
    else:
        body += "✅ <b>OTP diterima</b>\n"
    return head + body + "━━━━━━━━━━━━━━━━━━━━\n<i>Jangan bagikan kode ini ke siapa pun.</i>"


def get_order_inline_keyboard(order_id, otps, waiting, closed=False):
    if closed:
        return None
    if not otps:
        # belum ada OTP sama sekali
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Ganti Nomor", callback_data=f"otp_act_ganti_{order_id}"),
             InlineKeyboardButton("❌ Batal & Refund", callback_data=f"otp_act_batal_{order_id}")],
            [InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data=f"otp_act_resend_{order_id}")],
        ])
    if waiting:
        # lagi nunggu OTP ke-2, ke-3, dst
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Selesai", callback_data=f"otp_act_selesai_{order_id}")]
        ])
    # OTP sudah masuk: cuma Kirim Ulang OTP + Selesai
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data=f"otp_act_resend_{order_id}"),
        InlineKeyboardButton("✅ Selesai", callback_data=f"otp_act_selesai_{order_id}"),
    ]])

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
    
    await update.message.reply_text(f"⏳ Sedang membuat pesanan...", parse_mode=ParseMode.HTML)
    
    numbers_bought = []
    for i in range(qty):
        if not db.deduct_saldo(user.id, harga_satuan, f"Beli {display_name}"):
            u_skrg = db.get_user(user.id)
            saldo_skrg = u_skrg['saldo'] if u_skrg and 'saldo' in u_skrg.keys() else 0
            await update.message.reply_text(f"❌ Gagal memotong saldo.\n\n💰 Saldo kamu: Rp {saldo_skrg:,}\n💸 Butuh: Rp {harga_satuan:,}", parse_mode=ParseMode.HTML)
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
            db.add_saldo(user.id, harga_satuan, f"Refund gagal sewa {display_name}")
            await update.message.reply_text(f"❌ Gagal membuat pesanan ke-{i+1}.\n<b>Alasan:</b> <code>{err_msg}</code>\n\nBalance dikembalikan.", parse_mode=ParseMode.HTML)
            break
            
    if not numbers_bought:
        ctx.user_data['is_processing'] = False
        return

    oid, tkn, ph = numbers_bought[0]
    ns = await start_order_ui(ctx.bot, user.id, oid, tkn, display_name, ph, harga_satuan)
    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name,
                                   "price": harga_satuan, "msg_id": ns["msg_id"]}
    ctx.user_data['ui_msg_id'] = ns["msg_id"]
    
    if len(numbers_bought) > 1:
        info_txt = f"✅ Berhasil membeli {len(numbers_bought)} nomor.\n\n"
        for idx, (o, t, p) in enumerate(numbers_bought, 1):
            info_txt += f"{idx}. <code>{p}</code>\n"
            if idx > 1:
                await asyncio.sleep(3)
                await start_order_ui(ctx.bot, user.id, o, t, display_name, p, harga_satuan)
        await ctx.bot.send_message(chat_id=user.id, text=info_txt, parse_mode=ParseMode.HTML)

    if "kopken" in api_name.lower() and "filter" not in api_name.lower():
        promo_txt = f"💡 Nomor <code>{ph}</code> siap. Mau cek status Kopkennya sekarang?\n"
        promo_txt += f"*(Kamu punya 2x Free Trial cek gratis!)*"
        cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{ph}")]])
        await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)

    ctx.user_data['buy_state'] = None
    ctx.user_data['is_processing'] = False

# === SESI ORDER: UI, TIMER, POLLING ===
async def refresh_ui(bot, s, closed_text=None):
    """Edit pesan UI yang sama (tanpa hapus/kirim ulang)."""
    left = int(s["deadline"] - time.time())
    closed = closed_text is not None
    text = build_order_ui(s["order_id"], s["service"], s["phone"], s["otps"], s["waiting"], left, closed_text)
    kb = get_order_inline_keyboard(s["order_id"], s["otps"], s["waiting"], closed)
    try:
        await bot.edit_message_text(
            chat_id=s["chat_id"], message_id=s["msg_id"], text=text,
            parse_mode=ParseMode.HTML, reply_markup=kb
        )
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("edit UI order %s gagal: %s", s["order_id"], e)
    except Exception:
        log.exception("refresh_ui order %s", s["order_id"])


async def start_order_ui(bot, chat_id, order_id, token, service, phone, price):
    """Kirim UI order + jalankan timer & polling. Pengganti run_timer/poll_otp_bg."""
    s = {
        "order_id": order_id, "token": token, "service": service, "phone": phone,
        "price": price, "chat_id": chat_id, "otps": [], "waiting": True,
        "closed": False, "busy": False, "deadline": time.time() + RENT_SECONDS,
    }
    msg = await bot.send_message(
        chat_id=chat_id,
        text=build_order_ui(order_id, service, phone, [], True, RENT_SECONDS),
        parse_mode=ParseMode.HTML,
        reply_markup=get_order_inline_keyboard(order_id, [], True),
    )
    s["msg_id"] = msg.message_id
    SESSIONS[order_id] = s
    asyncio.create_task(_timer_loop(bot, s))
    asyncio.create_task(_poll_loop(bot, s))
    return s


async def _timer_loop(bot, s):
    try:
        while not s["closed"] and time.time() < s["deadline"]:
            await asyncio.sleep(5)
            if s["closed"]:
                break
            if s["waiting"]:
                await refresh_ui(bot, s)
    except Exception:
        log.exception("timer order %s", s["order_id"])


async def _poll_loop(bot, s):
    oid = s["order_id"]
    try:
        while not s["closed"] and time.time() < s["deadline"]:
            if s["waiting"]:
                try:
                    res, status = await wahub_client.check_otp(s["token"], timeout=15)
                except Exception:
                    log.exception("check_otp %s", oid)
                    await asyncio.sleep(5)
                    continue
                if status == 200 and res.get("state") == "success":
                    code = res.get("otp")
                    if code and code not in s["otps"] and not s["closed"]:
                        s["otps"].append(code)
                        s["waiting"] = False
                        if len(s["otps"]) == 1:
                            db.update_otp_order_status(oid, "success", code)
                        await refresh_ui(bot, s)
                        continue
            await asyncio.sleep(3)

        if s["closed"]:
            return

        # waktu habis
        s["closed"] = True
        SESSIONS.pop(oid, None)
        if not s["otps"]:
            cxl_res, cxl_code = await wahub_client.cancel_order(oid)
            if cxl_code == 200:
                db.update_otp_order_status(oid, "expired")
                db.add_saldo(s["chat_id"], s["price"], "Refund OTP Timeout")
                await refresh_ui(bot, s, f"⏰ <b>Waktu Habis</b>\nOTP tidak masuk. Balance Rp {s['price']:,} dikembalikan.")
            else:
                log.error("cancel timeout order %s gagal: %s", oid, cxl_code)
                await refresh_ui(bot, s, "⏰ <b>Waktu Habis</b>\nOTP tidak masuk. Refund otomatis gagal, hubungi admin.")
        else:
            try:
                await wahub_client.mark_done(oid)
            except Exception:
                log.exception("mark_done %s", oid)
            await refresh_ui(bot, s, "⏰ <b>Sesi berakhir</b>")
    except Exception:
        log.exception("poll order %s", oid)

# === FITUR CEK RIWAYAT ===
async def handle_history_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    
    trxs = db.get_user_transactions(user.id, limit=15)
    
    text = "📜 <b>Riwayat Transaksi Kamu</b>\n\n"
    
    if not trxs:
        text += "<i>Belum ada transaksi.</i>"
    else:
        import datetime as dtmod
        for t in trxs:
            try:
                dt = dtmod.datetime.strptime(t['created_at'], "%Y-%m-%d %H:%M:%S")
                jam = dt.strftime("%d/%m %H:%M")
            except:
                jam = "-"
                
            if t['amount'] > 0:
                text += f"🟢 +Rp {t['amount']:,} | {t['description']} ({jam})\n"
            else:
                text += f"🔴 -Rp {abs(t['amount']):,} | {t['description']} ({jam})\n"
                
    await query.answer()
    await query.message.reply_text(text, parse_mode=ParseMode.HTML)

# === AKSI TOMBOL ORDER (callback_data: otp_act_<aksi>_<order_id>) ===
async def handle_order_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split("_")            # ["otp", "act", aksi, order_id]
    action = parts[2] if len(parts) > 2 else ""
    try:
        order_id = int(parts[3]) if len(parts) > 3 else None
    except ValueError:
        order_id = None
    if order_id is None:                     # tombol lama tanpa order_id
        lo = ctx.user_data.get("last_order") or {}
        order_id = lo.get("order_id")

    s = SESSIONS.get(order_id)
    if not s or s["closed"] or s["chat_id"] != user.id:
        await query.answer("Sesi pesanan ini sudah berakhir.", show_alert=True)
        return
    if s["busy"]:
        await query.answer("Sedang diproses, tunggu sebentar.")
        return

    try:
        if action == "resend":
            if s["otps"] and s["waiting"]:
                await query.answer("Masih menunggu OTP berikutnya.")
                return
            s["busy"] = True
            try:
                res, code = await wahub_client.retry_otp(s["token"])
            finally:
                s["busy"] = False
            if code == 200:
                if s["otps"]:
                    s["waiting"] = True
                    await refresh_ui(ctx.bot, s)      # tampil "Menunggu OTP ke-2"
                await query.answer("✅ Permintaan OTP ulang dikirim.")
            else:
                msg = (res or {}).get("message") or (res or {}).get("error") or f"HTTP {code}"
                await query.answer(f"❌ Gagal minta ulang: {msg}"[:190], show_alert=True)

        elif action == "selesai":
            s["closed"] = True
            SESSIONS.pop(order_id, None)
            try:
                await wahub_client.mark_done(order_id)
            except Exception:
                log.exception("mark_done %s", order_id)
            await refresh_ui(ctx.bot, s, "✅ <b>Pesanan Selesai</b>")
            await query.answer("Selesai.")

        elif action == "batal":
            if s["otps"]:
                await query.answer("OTP sudah masuk, pesanan tidak bisa dibatalkan.", show_alert=True)
                return
            s["busy"] = True
            try:
                res, code = await wahub_client.cancel_order(order_id)
                if code != 200:
                    await query.answer("❌ Gagal membatalkan, coba lagi.", show_alert=True)
                    return
                s["closed"] = True
                SESSIONS.pop(order_id, None)
                db.add_saldo(user.id, s["price"], "Refund Batal Pesanan")
                db.update_otp_order_status(order_id, "cancelled")
                ctx.user_data["last_order"] = None
            finally:
                s["busy"] = False
            await refresh_ui(ctx.bot, s, f"❌ <b>Dibatalkan</b>\nBalance Rp {s['price']:,} dikembalikan.")
            await query.answer("Pesanan dibatalkan.")
            await ctx.bot.send_message(user.id, "Menu OTP:", reply_markup=await get_otp_keyboard())

        elif action == "ganti":
            if s["otps"]:
                await query.answer("OTP sudah masuk, tidak bisa ganti nomor.", show_alert=True)
                return
            s["busy"] = True
            try:
                await handle_ganti_nomor(ctx, query, s)
            finally:
                s["busy"] = False
        else:
            await query.answer()
    except Exception:
        log.exception("order action %s %s", action, order_id)
        try:
            await query.answer("❌ Terjadi error, coba lagi.", show_alert=True)
        except Exception:
            pass


async def handle_ganti_nomor(ctx, query, s):
    bot, uid = ctx.bot, s["chat_id"]
    display_name, harga = s["service"], s["price"]
    map_back = {
        "kopken gacha": "kopken",
        "kopken asoy": "kopken filter",
        "shope gacha": "shopee",
        "shope asoy": "shopee filter",
    }
    api_name = map_back.get(display_name.lower(), display_name.lower())
    services = await wahub_client.get_services()
    target = next((x for x in services if x["name"].strip().lower() == api_name), None)
    if not target or target["stock"] < 1:
        await query.answer(f"❌ Stok {display_name} kosong.", show_alert=True)
        return

    await query.answer("Memproses nomor baru...")

    # batalkan nomor lama dulu; kalau gagal, order lama tetap jalan
    cxl_res, cxl_code = await wahub_client.cancel_order(s["order_id"])
    if cxl_code != 200:
        await bot.send_message(uid, "❌ Gagal membatalkan nomor lama. Pesanan lama tetap berjalan.")
        return

    s["closed"] = True
    SESSIONS.pop(s["order_id"], None)
    db.update_otp_order_status(s["order_id"], "cancelled")
    db.add_saldo(uid, harga, "Refund Ganti Nomor")
    await refresh_ui(bot, s, "🔄 <b>Nomor diganti, memproses nomor baru...</b>")

    if not db.deduct_saldo(uid, harga, f"Ganti Nomor {display_name}"):
        await bot.send_message(uid, "❌ Balance tidak cukup untuk nomor baru. Balance lama sudah dikembalikan.")
        return

    res, code = await wahub_client.rent_number(target["id"])
    if code == 200 and res.get("order_id"):
        new_oid = res["order_id"]
        new_tkn = res.get("token")
        new_ph = res.get("phone")
        db.create_otp_order(uid, new_oid, new_tkn, new_ph, display_name, harga)
        ns = await start_order_ui(bot, uid, new_oid, new_tkn, display_name, new_ph, harga)
        ctx.user_data["last_order"] = {
            "order_id": new_oid, "token": new_tkn, "phone": new_ph,
            "service": display_name, "price": harga, "msg_id": ns["msg_id"],
        }
        if "kopken" in api_name.lower() and "filter" not in api_name.lower():
            promo_txt = f"💡 Nomor <code>{new_ph}</code> siap. Mau cek status Kopkennya sekarang?\n"
            promo_txt += "*(Kamu punya 2x Free Trial cek gratis!)*"
            cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{new_ph}")]])
            await bot.send_message(chat_id=uid, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)
    else:
        db.add_saldo(uid, harga, "Refund Gagal Ganti Nomor")
        err = res.get("message") or res.get("error") or "Unknown error"
        await bot.send_message(
            uid,
            f"❌ Gagal mendapatkan nomor baru.\n<b>Alasan:</b> <code>{err}</code>\n\nBalance dikembalikan.",
            parse_mode=ParseMode.HTML,
        )

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
                harga_satuan = amount // qty
                
                if not db.deduct_saldo(chat_id, amount, f"Beli {display_name}"):
                    await bot.send_message(chat_id, "❌ Gagal memotong saldo setelah pembayaran QRIS. Sistem sibuk.")
                    return

                res, code = await wahub_client.rent_number(service_id)
                
                if code == 200 and res.get('order_id'):
                    oid = res['order_id']
                    tkn = res['token']
                    ph = res['phone']
                    
                    db.create_otp_order(chat_id, oid, tkn, ph, display_name, harga_satuan)
                    
                    ns = await start_order_ui(bot, chat_id, oid, tkn, display_name, ph, harga_satuan)
                    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name, "price": harga_satuan, "msg_id": ns["msg_id"]}
                else:
                    err_msg = res.get('message') or res.get('error') or "Unknown error"
                    db.add_saldo(chat_id, amount, "Refund Gagal Order")
                    await bot.send_message(chat_id, f"❌ Gagal menyewa nomor setelah bayar.\n<b>Alasan:</b> <code>{err_msg}</code>\n\nBalance Rp {amount:,} dikembalikan.", parse_mode=ParseMode.HTML)
                
            else:
                db.add_saldo(chat_id, amount, f"Topup QRIS Rp {amount:,}")
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
    
    await query.message.reply_text(
        f"Berapa nomor <b>{service_name}</b> yang mau dibeli?\n\nKetik angka (contoh: 1)", 
        parse_mode=ParseMode.HTML
    )
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
        s_old = SESSIONS.get(last_order['order_id'])
        if s_old and s_old['otps']:
            pass  # OTP sudah masuk, pesanan lama tidak dibatalkan
        else:
            await query.edit_message_text("⏳ Membatalkan pesanan sebelumnya...")
            res, code = await wahub_client.cancel_order(last_order['order_id'])
            if code == 200:
                refund = last_order.get('price', 0)
                db.add_saldo(user.id, refund, "Refund Batal (Beli Filter)")
                db.update_otp_order_status(last_order['order_id'], 'cancelled')
                ctx.user_data['last_order'] = None
                if s_old:
                    s_old['closed'] = True
                    SESSIONS.pop(last_order['order_id'], None)
                    await refresh_ui(ctx.bot, s_old, "❌ <b>Dibatalkan</b>")
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