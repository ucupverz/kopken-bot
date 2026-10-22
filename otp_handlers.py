import asyncio
import logging
import html
import httpx
import os
import time
import datetime
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
import wahub_client
import dehuyz_automation
from invite_access import require_whatsapp_access

logger = logging.getLogger(__name__)

QUICK_SERVICES = ["Kopken Gacha", "Kopken Asoy", "Shope Gacha", "Shope Asoy"] 
MARGIN = 700
BIAYA_CEK_FRESH = 200
BIAYA_API_BOOSTLY = 100

BOOSTLY_BASE = "https://api.boostly.id"
BOOSTLY_API_KEY = os.getenv("BOOSTLY_API_KEY", "")

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

# === UI PESANAN AKTIF ===
def build_order_ui(order_id, service_name, phone, status="Menunggu OTP", time_left=1200, otp="-", otp2=None, otp_history=None):
    menit = time_left // 60
    detik = time_left % 60
    total_blocks = 20
    sisa_blocks = max(0, min(total_blocks, int((time_left / 1200) * total_blocks)))
    progress_bar = "█" * sisa_blocks + "░" * (total_blocks - sisa_blocks)
    timer_line = f"<code>[{progress_bar}] {menit:02d}:{detik:02d}</code>"
    if status == "Menunggu OTP":
        status_line = f"⏳ <b>Menunggu OTP</b>\n{timer_line}"
        otp_line = ""
    elif status == "Menunggu OTP 2":
        status_line = f"🔄 <b>Menunggu OTP #2</b>\n{timer_line}"
        history = otp_history or ([otp] if otp != "-" else [])
        otp_line = "".join(
            f"🔑 Kode OTP #{idx}: <code>{html.escape(str(code))}</code>\n"
            for idx, code in enumerate(history, 1)
        )
    elif status == "Selesai":
        status_line = f"✅ <b>Pesanan Selesai</b>"
        history = otp_history or [code for code in (otp, otp2) if code and code != "-"]
        otp_line = "".join(
            f"🔑 Kode OTP #{idx}: <code>{html.escape(str(code))}</code>\n"
            for idx, code in enumerate(history, 1)
        )
    elif status == "OTP diterima":
        status_line = f"✅ <b>OTP diterima</b>\n⏳ {timer_line}"
        if otp_history is None:
            otp_history = [otp] if otp != "-" else []
        otp_line = "".join(
            f"🔑 Kode OTP #{idx}: <code>{html.escape(str(code))}</code>\n"
            for idx, code in enumerate(otp_history, 1)
        )
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

def get_order_inline_keyboard(status="Menunggu OTP", order_id=None):
    order_suffix = f":{order_id}" if order_id is not None else ""
    if status == "Menunggu OTP":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Ganti Nomor", callback_data=f"otp_act_ganti{order_suffix}"),
                InlineKeyboardButton("❌ Batal & Refund", callback_data=f"otp_act_batal{order_suffix}")
            ],
            [
                InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data=f"otp_act_resend{order_suffix}")
            ]
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Kirim Ulang OTP", callback_data=f"otp_act_resend{order_suffix}"),
                InlineKeyboardButton("✅ Selesai", callback_data=f"otp_act_selesai{order_suffix}")
            ]
        ])


def _runtime_orders(ctx):
    # Application-scoped: menu navigation and user_data cleanup must not detach
    # a live timer/poller from its order.
    return ctx.bot_data.setdefault("otp_runtime_orders", {})


def _start_order_workers(ctx, user_id, msg_id, order_id, service_name, phone, total_time=1200):
    order_id = str(order_id)
    db.set_order_msg_id(order_id, msg_id)
    runtime = _runtime_orders(ctx)
    previous = runtime.get(order_id)
    if previous and previous.get("stop_event"):
        previous["stop_event"].set()
    order_state = {
        "started_at": time.monotonic(),
        "otp_history": db.get_otp_history(order_id),
        "waiting_for_next_otp": False,
        "stop_event": asyncio.Event(),
        "tasks": [],
    }
    runtime[order_id] = order_state
    order_state["tasks"] = [
        asyncio.create_task(run_timer(ctx.bot, user_id, msg_id, order_id, service_name, phone, total_time, order_state)),
        asyncio.create_task(poll_otp_bg(ctx.bot, user_id, order_id, db.get_otp_order(order_id)["token"], service_name, msg_id, phone, order_state)),
    ]
    return order_state


async def _stop_order_workers(ctx, order_id):
    order_state = _runtime_orders(ctx).get(str(order_id))
    if not order_state:
        return
    order_state["stop_event"].set()
    tasks = [task for task in order_state.get("tasks", []) if task and not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _runtime_orders(ctx).pop(str(order_id), None)


def _callback_order(query, ctx, user):
    """Resolve the order from callback data and the card message, never the current last_order."""
    data = query.data or ""
    action_part, separator, order_id = data.partition(":")
    if not separator or not order_id:
        # Legacy buttons are accepted only when the current card message matches.
        last_order = ctx.user_data.get("last_order") or {}
        if not last_order or last_order.get("msg_id") != getattr(query.message, "message_id", None):
            return None, None
        order_id = str(last_order.get("order_id", ""))
    order = db.get_otp_order(order_id)
    if not order or int(order["user_id"]) != int(user.id):
        return None, None
    card_msg_id = getattr(query.message, "message_id", None)
    stored_msg_id = order["msg_id"] if "msg_id" in order.keys() else None
    if stored_msg_id is not None and card_msg_id != stored_msg_id:
        return None, None
    return action_part.replace("otp_act_", "", 1), {
        "order_id": str(order["order_id"]),
        "token": order["token"],
        "phone": order["phone"],
        "service": order["service_name"],
        "price": order["price"],
        "msg_id": stored_msg_id or card_msg_id,
        "status": order["status"],
        "otp": order["otp"],
        "user_id": order["user_id"],
    }

async def cmd_otp_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_whatsapp_access(update, ctx):
        return
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
    if not await require_whatsapp_access(update, ctx):
        return
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
    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name, "price": harga_satuan, "time_left": 1200}
    
    ui_text = build_order_ui(oid, display_name, ph, "Menunggu OTP", 1200)
    msg_ui = await update.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP", oid))
    
    ctx.user_data['last_order']['msg_id'] = msg_ui.message_id
    ctx.user_data['ui_msg_id'] = msg_ui.message_id
    
    _start_order_workers(ctx, user.id, msg_ui.message_id, oid, display_name, ph)
    
    if len(numbers_bought) > 1:
        info_txt = f"✅ Berhasil membeli {len(numbers_bought)} nomor.\n\n"
        for idx, (o, t, p) in enumerate(numbers_bought, 1):
            info_txt += f"{idx}. <code>{p}</code>\n"
            if idx > 1:
                await asyncio.sleep(3)
                sub_msg = await ctx.bot.send_message(
                    chat_id=user.id, 
                    text=build_order_ui(o, display_name, p, "Menunggu OTP", 1200), 
                    parse_mode=ParseMode.HTML, 
                    reply_markup=get_order_inline_keyboard("Menunggu OTP", o)
                )
                _start_order_workers(ctx, user.id, sub_msg.message_id, o, display_name, p)
        await ctx.bot.send_message(chat_id=user.id, text=info_txt, parse_mode=ParseMode.HTML)

    if "kopken" in api_name.lower() and "filter" not in api_name.lower():
        promo_txt = f"💡 Nomor <code>{ph}</code> siap. Mau cek status Kopkennya sekarang?\n"
        promo_txt += f"*(Kamu punya 2x Free Trial cek gratis!)*"
        cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{ph}")]])
        await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)

    ctx.user_data['buy_state'] = None
    ctx.user_data['is_processing'] = False

# === TIMER COUNTDOWN: edit pesan yang sama agar tombol tetap aktif ===
async def run_timer(bot, chat_id, msg_id, order_id, service_name, phone, total_time, order_state=None):
    if order_state is None:
        order_state = {}
    order_state.setdefault("started_at", time.monotonic())
    order_state.setdefault("otp_history", [])
    order_state.setdefault("waiting_for_next_otp", False)
    stop_event = order_state.get("stop_event")
    start_time = order_state["started_at"]

    while not (stop_event and stop_event.is_set()):
        await asyncio.sleep(5)
        if stop_event and stop_event.is_set():
            return
        order = db.get_otp_order(order_id)
        if not order or order['status'] not in ('waiting', 'success'):
            return

        elapsed = int(time.monotonic() - start_time)
        time_left = max(0, total_time - elapsed)
        order_state["otp_history"] = db.get_otp_history(order_id)
        status = (
            "Menunggu OTP 2" if order['status'] == 'success' and order_state["waiting_for_next_otp"]
            else "OTP diterima" if order['status'] == 'success'
            else "Menunggu OTP"
        )
        ui_text = build_order_ui(
            order_id, service_name, phone, status, time_left,
            otp_history=order_state["otp_history"]
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=ui_text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_order_inline_keyboard(status, order_id),
            )
        except Exception as exc:
            # Jangan diam-diam membuat UI hilang; polling OTP tetap berjalan.
            logger.warning("Gagal memperbarui timer order %s: %s", order_id, exc)

        # Biarkan pesan 00:00 terlihat sampai proses timeout mengonfirmasi
        # pembatalan dan refund, alih-alih menghapus UI tanpa penjelasan.
        if time_left <= 0:
            return

# === POLLING OTP ===
async def poll_otp_bg(bot, user_id, order_id, token, service_name, msg_id, phone, order_state=None):
    db_order = db.get_otp_order(order_id)
    if not db_order or db_order['status'] not in ('waiting', 'success'):
        return

    if order_state is None:
        order_state = {}
    order_state.setdefault("started_at", time.monotonic())
    order_state.setdefault("otp_history", [])
    order_state.setdefault("waiting_for_next_otp", False)
    stop_event = order_state.get("stop_event")
    order_state["otp_history"] = db.get_otp_history(order_id)
    otp_history = order_state["otp_history"]
    start_time = order_state["started_at"]

    while time.monotonic() - start_time < 1200 and not (stop_event and stop_event.is_set()):
        db_order = db.get_otp_order(order_id)
        if not db_order or db_order['status'] not in ('waiting', 'success'):
            return

        try:
            res, status = await wahub_client.check_otp(token, timeout=15)
        except Exception as exc:
            logger.warning("Gagal cek OTP order %s: %s", order_id, exc)
            await asyncio.sleep(15)
            continue

        if status == 200 and res.get('state') == 'success':
            otp_baru = res.get('otp')
            if not otp_baru:
                await asyncio.sleep(15)
                continue

            otp_baru = str(otp_baru)
            otp_history[:] = db.get_otp_history(order_id)
            if otp_baru not in otp_history:
                first_otp = not otp_history
                # CAS mencegah OTP yang datang bersamaan dengan pembatalan
                # mengubah order yang sudah dibatalkan kembali menjadi sukses.
                if first_otp and not db.mark_success_if_waiting(order_id, otp_baru):
                    return
                if first_otp:
                    otp_history[:] = db.get_otp_history(order_id)
                else:
                    otp_history[:] = db.append_otp_history(order_id, otp_baru)
                order_state["waiting_for_next_otp"] = False

                elapsed = int(time.monotonic() - start_time)
                time_left = max(0, 1200 - elapsed)
                ui_text = build_order_ui(
                    order_id, service_name, phone, "OTP diterima", time_left,
                    otp_history=otp_history
                )
                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=ui_text,
                        parse_mode=ParseMode.HTML,
                    reply_markup=get_order_inline_keyboard("OTP diterima", order_id),
                    )
                except Exception as exc:
                    logger.warning("Gagal memperbarui pesan OTP order %s: %s", order_id, exc)

        await asyncio.sleep(15)

    if stop_event and stop_event.is_set():
        return

    db_order = db.get_otp_order(order_id)
    if not db_order or db_order['status'] != 'waiting':
        return

    try:
        cxl_res, cxl_status = await asyncio.wait_for(
            wahub_client.cancel_order(order_id), timeout=25
        )
    except Exception as exc:
        logger.exception("Gagal mengonfirmasi timeout order %s", order_id)
        cxl_res, cxl_status = {"message": str(exc)}, None

    if cxl_status == 200:
        try:
            refunded, refund = db.finish_otp_order_and_refund(
                order_id, user_id, 'expired', "Refund OTP Timeout"
            )
        except Exception as exc:
            logger.exception("Provider membatalkan order %s tetapi refund database gagal", order_id)
            refunded, refund = False, 0
            ui_text = (
                "⚠️ <b>Provider sudah menerima pembatalan, tetapi refund belum tercatat</b>\n\n"
                "Jangan tekan batal lagi. Status disimpan untuk pemeriksaan ulang; silakan hubungi admin jika balance belum masuk."
            )
        else:
            if refunded:
                ui_text = (
                    f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk. "
                    f"Balance Rp {refund:,} dikembalikan."
                )
            else:
                ui_text = (
                    f"ℹ️ <b>Order sudah diproses</b>\n\n"
                    "Status pesanan berubah sebelum refund timeout; saldo tidak dikreditkan dua kali."
                )
    else:
        payload = cxl_res if isinstance(cxl_res, dict) else {}
        detail = payload.get('message') or payload.get('error') or f"HTTP {cxl_status}"
        ui_text = (
            f"⚠️ <b>Waktu OTP habis, pembatalan belum terkonfirmasi</b>\n\n"
            f"Alasan: <code>{html.escape(str(detail))}</code>\n"
            "Saldo belum dikembalikan. Sistem akan mencoba pemeriksaan ulang."
        )

    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=msg_id,
            text=ui_text,
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        try:
            await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.warning("Gagal mengirim status akhir order %s: %s", order_id, exc)

# === FITUR CEK RIWAYAT ===
async def handle_history_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_whatsapp_access(update, ctx):
        return
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

# === CALLBACK ORDER: status selalu terlihat dan refund idempotent ===
async def handle_order_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_whatsapp_access(update, ctx):
        return
    query = update.callback_query
    user = update.effective_user
    action, order = _callback_order(query, ctx, user)
    if not order or action not in ("batal", "ganti", "resend", "selesai"):
        await query.answer("Pesanan ini tidak lagi tersedia.", show_alert=True)
        return

    if action in ("batal", "ganti") and ctx.user_data.get('otp_action_busy'):
        await query.answer("Aksi pesanan sedang diproses.", show_alert=True)
        return
    await query.answer()

    order_id = order["order_id"]
    runtime = _runtime_orders(ctx).get(order_id)
    if runtime is None and order["status"] in ("waiting", "success"):
        runtime = _start_order_workers(
            ctx, user.id, order["msg_id"], order_id, order["service"], order["phone"]
        )
        created_at = db.get_otp_order(order_id)["created_at"]
        if created_at:
            elapsed = max(0, int(time.time() - float(created_at)))
            runtime["started_at"] = time.monotonic() - elapsed

    if action == "selesai":
        current_order = db.get_otp_order(order_id)
        if current_order and current_order["status"] == "success":
            try:
                await wahub_client.mark_done(order_id)
            except Exception as exc:
                logger.warning("Gagal menandai order %s selesai di provider: %s", order_id, exc)
            db.update_otp_order_status(order_id, "completed")
        if runtime:
            runtime["otp_history"] = db.get_otp_history(order_id)
        history = runtime["otp_history"] if runtime else db.get_otp_history(order_id)
        try:
            await query.edit_message_text(
                build_order_ui(
                    order_id, order["service"], order["phone"], "Selesai",
                    otp_history=history,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception as exc:
            logger.warning("Gagal memperbarui kartu order selesai %s: %s", order_id, exc)
        await _stop_order_workers(ctx, order_id)
        if ctx.user_data.get("last_order", {}).get("order_id") == order_id:
            ctx.user_data["last_order"] = None
        await ctx.bot.send_message(
            chat_id=user.id,
            text="Pilih menu berikutnya:",
            reply_markup=ReplyKeyboardMarkup(
                [["🛒 Beli Lagi", "📋 Cek Pesanan"], ["🔙 Menu Utama"]],
                resize_keyboard=True,
            ),
        )
        return

    if action == "resend":
        if not runtime:
            runtime = _runtime_orders(ctx).get(order_id)
        if runtime:
            runtime["waiting_for_next_otp"] = True
            runtime["otp_history"] = db.get_otp_history(order_id)
        waiting_text = build_order_ui(
            order_id, order["service"], order["phone"], "Menunggu OTP 2",
            max(0, 1200 - int(time.monotonic() - runtime["started_at"])) if runtime else 1200,
            otp_history=runtime["otp_history"] if runtime else db.get_otp_history(order_id),
        )
        try:
            await query.edit_message_text(
                waiting_text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_order_inline_keyboard("Menunggu OTP 2", order_id),
            )
        except Exception as exc:
            logger.warning("Gagal menampilkan status resend order %s: %s", order_id, exc)
        try:
            res, code = await wahub_client.retry_otp(order["token"])
        except Exception as exc:
            logger.exception("Gagal meminta OTP ulang order %s", order_id)
            code = None
            if runtime:
                runtime["waiting_for_next_otp"] = False
            text = f"❌ Permintaan OTP ulang gagal: <code>{html.escape(str(exc))}</code>"
        else:
            if code == 200:
                text = "🔄 Menunggu OTP #2. Kode pertama tetap tersimpan di kartu ini."
            else:
                if runtime:
                    runtime["waiting_for_next_otp"] = False
                detail = (res or {}).get("message") or (res or {}).get("error") or "Batas retry mungkin habis atau waktu sewa selesai."
                text = f"❌ Gagal meminta OTP ulang: <code>{html.escape(str(detail))}</code>"
        if code != 200:
            status = "OTP diterima" if (runtime and runtime["otp_history"]) else "Menunggu OTP"
            try:
                await query.edit_message_text(
                    build_order_ui(
                        order_id, order["service"], order["phone"], status,
                        max(0, 1200 - int(time.monotonic() - runtime["started_at"])) if runtime else 1200,
                        otp_history=runtime["otp_history"] if runtime else db.get_otp_history(order_id),
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_order_inline_keyboard(status, order_id),
                )
            except Exception as exc:
                logger.warning("Gagal mengembalikan kartu order %s setelah resend gagal: %s", order_id, exc)
        return

    if action == "batal":
        ctx.user_data['otp_action_busy'] = True
        try:
            try:
                await query.edit_message_text(
                    "⏳ Sedang meminta pembatalan ke penyedia. Tunggu sebentar; status dan refund akan ditampilkan di sini."
                )
            except Exception as exc:
                logger.warning("Tidak bisa menampilkan status batal order %s: %s", order_id, exc)

            try:
                res, code = await asyncio.wait_for(wahub_client.cancel_order(order_id), timeout=25)
            except Exception as exc:
                logger.exception("Permintaan batal order %s gagal", order_id)
                res, code = {"message": str(exc)}, None

            if code != 200:
                payload = res if isinstance(res, dict) else {}
                detail = payload.get('message') or payload.get('error') or payload.get('msg') or f"HTTP {code}"
                text = (
                    "❌ Pembatalan belum dikonfirmasi oleh penyedia. Saldo belum dikembalikan.\n\n"
                    f"Alasan: <code>{html.escape(str(detail))}</code>\n"
                    "Pesanan tetap tercatat agar tidak terjadi refund palsu."
                )
                await query.edit_message_text(text, parse_mode=ParseMode.HTML)
                return

            try:
                changed, refund = db.finish_otp_order_and_refund(
                    order_id, user.id, 'cancelled', "Refund Batal Pesanan"
                )
            except Exception as exc:
                logger.exception("Provider menerima batal order %s, tetapi refund database gagal", order_id)
                text = (
                    "⚠️ Provider menerima pembatalan, tetapi pencatatan refund mengalami error. "
                    "Jangan tekan batal lagi; admin perlu memeriksa saldo/order.\n"
                    f"Detail: {html.escape(str(exc))}"
                )
                await query.edit_message_text(text, parse_mode=ParseMode.HTML)
                return
            if changed:
                await _stop_order_workers(ctx, order_id)
                if ctx.user_data.get('last_order', {}).get('order_id') == order_id:
                    ctx.user_data['last_order'] = None
                text = f"✅ Pesanan dibatalkan dan refund Rp {refund:,} sudah masuk ke balance."
            else:
                current = db.get_otp_order(order_id)
                status_now = current['status'] if current else "tidak ditemukan"
                text = (
                    "ℹ️ Penyedia menerima pembatalan, tetapi order sudah diproses sebelumnya. "
                    f"Status database: <code>{html.escape(str(status_now))}</code>. Tidak ada refund kedua."
                )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML)
            await ctx.bot.send_message(
                chat_id=user.id, text="Menu OTP:", reply_markup=await get_otp_keyboard()
            )
        finally:
            ctx.user_data['otp_action_busy'] = False
        return

    if action == "ganti":
        ctx.user_data['otp_action_busy'] = True
        try:
            await handle_ganti_nomor_inline(update, ctx, order=order)
        finally:
            ctx.user_data['otp_action_busy'] = False

async def handle_ganti_nomor_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE, order=None):
    if not await require_whatsapp_access(update, ctx):
        return
    query = update.callback_query
    user = update.effective_user
    last_order = order or ctx.user_data.get('last_order')

    async def show_status(text):
        try:
            await query.edit_message_text(text)
        except Exception:
            await ctx.bot.send_message(chat_id=user.id, text=text)

    if not last_order:
        await show_status("❌ Tidak ada pesanan aktif untuk diganti.")
        return

    order_id_lama = last_order['order_id']
    display_name = last_order['service']
    harga = last_order['price']

    map_back = {
        "kopken gacha": "kopken",
        "kopken asoy": "kopken filter",
        "shope gacha": "shopee",
        "shope asoy": "shopee filter",
    }
    api_name = map_back.get(display_name.lower(), display_name.lower())

    try:
        services = await wahub_client.get_services()
        target_service = next((s for s in services if s['name'].strip().lower() == api_name), None)
    except Exception as exc:
        logger.exception("Gagal mengambil stok saat ganti order %s", order_id_lama)
        await show_status(f"❌ Gagal mengecek stok: {exc}")
        return

    if not target_service or target_service['stock'] < 1:
        await show_status(f"❌ Stok {display_name} kosong. Order lama belum dibatalkan.")
        return

    try:
        await query.edit_message_text("⏳ Meminta pembatalan nomor lama. Statusnya akan ditampilkan di sini…")
    except Exception as exc:
        logger.warning("Tidak bisa menampilkan status ganti order %s: %s", order_id_lama, exc)

    try:
        _, cxl_code = await asyncio.wait_for(wahub_client.cancel_order(order_id_lama), timeout=25)
    except Exception as exc:
        logger.exception("Pembatalan order %s untuk ganti nomor gagal", order_id_lama)
        await show_status(f"❌ Pembatalan belum terkonfirmasi; order lama tetap aktif dan saldo belum berubah. Alasan: {exc}")
        return

    if cxl_code != 200:
        await show_status(f"❌ Provider belum mengonfirmasi pembatalan (HTTP {cxl_code}). Order lama tetap aktif dan saldo belum berubah.")
        return

    try:
        changed, refund = db.finish_otp_order_and_refund(
            order_id_lama, user.id, 'cancelled', "Refund Ganti Nomor"
        )
    except Exception as exc:
        logger.exception("Provider menerima batal order %s, tetapi refund ganti gagal tercatat", order_id_lama)
        await show_status(f"⚠️ Provider menerima pembatalan, tetapi refund belum tercatat. Jangan ulangi aksi; admin perlu memeriksa order. Detail: {exc}")
        return
    if not changed:
        current = db.get_otp_order(order_id_lama)
        status_now = current['status'] if current else "tidak ditemukan"
        await show_status(f"ℹ️ Order lama sudah diproses ({status_now}); nomor baru tidak dibuat dan tidak ada refund kedua.")
        return

    await _stop_order_workers(ctx, order_id_lama)

    if ctx.user_data.get('last_order', {}).get('order_id') == order_id_lama:
        ctx.user_data['last_order'] = None

    if not db.deduct_saldo(user.id, harga, f"Ganti Nomor {display_name}"):
        await show_status(f"❌ Order lama dibatalkan dan refund Rp {refund:,} masuk, tetapi saldo tidak cukup untuk membeli nomor pengganti.")
        return

    try:
        res, code = await wahub_client.rent_number(target_service['id'])
    except Exception as exc:
        db.add_saldo(user.id, harga, "Refund Gagal Ganti Nomor")
        await show_status(f"❌ Gagal membuat nomor pengganti; Rp {harga:,} dikembalikan. Alasan: {exc}")
        return

    if code == 200 and res.get('order_id'):
        new_oid = res['order_id']
        new_tkn = res.get('token')
        new_ph = res.get('phone')

        db.create_otp_order(user.id, new_oid, new_tkn, new_ph, display_name, harga)
        ctx.user_data['last_order'] = {"order_id": new_oid, "token": new_tkn, "phone": new_ph, "service": display_name, "price": harga}

        ui_text = build_order_ui(new_oid, display_name, new_ph, "Menunggu OTP", 1200)

        msg_ui = await ctx.bot.send_message(chat_id=user.id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP", new_oid))
        ctx.user_data['ui_msg_id'] = msg_ui.message_id
        ctx.user_data['last_order']['msg_id'] = msg_ui.message_id

        _start_order_workers(ctx, user.id, msg_ui.message_id, new_oid, display_name, new_ph)

        if "kopken" in api_name.lower() and "filter" not in api_name.lower():
            promo_txt = f"💡 Nomor <code>{new_ph}</code> siap. Mau cek status Kopkennya sekarang?\n"
            promo_txt += f"*(Kamu punya 2x Free Trial cek gratis!)*"
            cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{new_ph}")]])
            await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)

    else:
        db.add_saldo(user.id, harga, "Refund Gagal Ganti Nomor")
        err_msg = res.get('message') or res.get('error') or "Unknown error"
        await show_status(f"❌ Gagal mendapatkan nomor baru. Saldo Rp {harga:,} dikembalikan. Alasan: {err_msg}")

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
    asyncio.create_task(process_qris_payment(
        ctx.bot, update.effective_chat.id, amount, ctx,
        is_direct_buy=False, status_message=msg,
    ))

async def _report_qris_failure(bot, chat_id, status_message, detail):
    text = f"❌ Gagal membuat QRIS: {detail}"
    if status_message is not None:
        try:
            await status_message.edit_text(text)
            return
        except Exception:
            pass
    await bot.send_message(chat_id, text)

async def process_qris_payment(bot, chat_id, amount, ctx, is_direct_buy=False,
                               status_message=None):
    if is_direct_buy and not db.has_whatsapp_access(chat_id):
        await bot.send_message(chat_id, "Akses WhatsApp OTP belum aktif. Buka menu WhatsApp OTP dan redeem kode dari admin.")
        return

    try:
        # Provider startup/connect can hang or fail before its own error handling.
        # Bound this stage so a background task cannot leave the user at "Memproses" forever.
        status, result = await asyncio.wait_for(
            dehuyz_automation.request_qris(amount), timeout=90
        )
    except asyncio.TimeoutError:
        logger.error("QRIS provider timed out for chat %s (amount %s)", chat_id, amount)
        await _report_qris_failure(bot, chat_id, status_message,
                                   "provider QRIS tidak merespons dalam 90 detik.")
        return
    except Exception as exc:
        logger.exception("QRIS provider failed for chat %s", chat_id)
        await _report_qris_failure(bot, chat_id, status_message,
                                   f"terjadi kendala saat menghubungi provider ({exc}).")
        return

    if status != "success":
        await _report_qris_failure(bot, chat_id, status_message, str(result))
        return
    if not isinstance(result, dict) or not result.get("image") or not result.get("msg_id"):
        await _report_qris_failure(bot, chat_id, status_message,
                                   "provider mengembalikan data QRIS yang tidak lengkap.")
        return
        
    qris_bytes = result["image"]
    caption_dehuy = result["caption"]
    qris_msg_id = result["msg_id"] 
    
    keyboard = [[InlineKeyboardButton("🔄 Cek Status", callback_data=f"cekstatus_{amount}")]]
    try:
        qris_msg = await bot.send_photo(
            chat_id=chat_id, photo=InputFile(qris_bytes, filename="qris.jpg"),
            caption=caption_dehuy, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as exc:
        logger.exception("Failed to send generated QRIS to chat %s", chat_id)
        await _report_qris_failure(bot, chat_id, status_message,
                                   f"QRIS dibuat tetapi gagal dikirim ke chat ({exc}).")
        return
    
    async def bg_wait_and_process():
        try:
            is_paid_status = await asyncio.wait_for(
                dehuyz_automation.wait_for_payment(amount, qris_msg_id), timeout=330
            )
        except asyncio.TimeoutError:
            is_paid_status = "timeout"
        except Exception:
            logger.exception("QRIS payment status check failed for chat %s", chat_id)
            is_paid_status = "error"
        if is_paid_status == "success":
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=qris_msg.message_id, caption=f"✅ <b>Topup Rp{amount:,} berhasil, balance sudah masuk.</b>", parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception: pass
                
            if is_direct_buy:
                if not db.has_whatsapp_access(chat_id):
                    db.add_saldo(chat_id, amount, "Topup QRIS; akses OTP belum aktif")
                    await bot.send_message(chat_id, "Pembayaran masuk ke balance. Redeem undangan sebelum membeli OTP.")
                    return
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
                    
                    ui_text = build_order_ui(oid, display_name, ph, "Menunggu OTP", 1200)
                    
                    msg_ui = await bot.send_message(chat_id, ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP", oid))
                    ctx.user_data['last_order'] = {"order_id": oid, "token": tkn, "phone": ph, "service": display_name, "price": harga_satuan, "msg_id": msg_ui.message_id}
                    
                    _start_order_workers(ctx, chat_id, msg_ui.message_id, oid, display_name, ph)
                else:
                    err_msg = res.get('message') or res.get('error') or "Unknown error"
                    db.add_saldo(chat_id, amount, "Refund Gagal Order")
                    await bot.send_message(chat_id, f"❌ Gagal menyewa nomor setelah bayar.\n<b>Alasan:</b> <code>{err_msg}</code>\n\nBalance Rp {amount:,} dikembalikan.", parse_mode=ParseMode.HTML)
                
            else:
                db.add_saldo(chat_id, amount, f"Topup QRIS Rp {amount:,}")
                u = db.get_user(chat_id)
                kb = await get_otp_keyboard() if db.has_whatsapp_access(chat_id) else get_menu_keyboard()
                await bot.send_message(chat_id, f"✅ Deposit Berhasil! Balance masuk Rp {amount:,}.\nTotal Balance: Rp {u['saldo']:,}", reply_markup=kb)
                
        elif is_paid_status == "timeout":
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=qris_msg.message_id, caption=f"❌ <b>QRIS Kadaluarsa</b>", parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception: pass
        elif is_paid_status == "error":
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=qris_msg.message_id,
                    caption="⚠️ Status pembayaran belum dapat diverifikasi. Hubungi admin sebelum mencoba pembayaran ulang.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                pass

    asyncio.create_task(bg_wait_and_process())

async def handle_cekstatus_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Sistem sedang mengecek pembayaran Anda secara otomatis di background. Mohon tunggu.", show_alert=True)

async def handle_buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_whatsapp_access(update, ctx):
        return
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
    if not await require_whatsapp_access(update, ctx):
        return
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
    if not await require_whatsapp_access(update, ctx):
        return
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    last_order = ctx.user_data.get('last_order')
    
    if last_order:
        await query.edit_message_text("⏳ Membatalkan pesanan sebelumnya...")
        res, code = await wahub_client.cancel_order(last_order['order_id'])
        if code == 200:
            refund = last_order.get('price', 0)
            db.add_saldo(user.id, refund, "Refund Batal (Beli Filter)")
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