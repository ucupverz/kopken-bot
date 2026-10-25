import html

from telegram import Update, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
from sfood_client import (
    parse_tracker_url, fetch_tracking, parse_tracking,
    fetch_tracking_auto, is_shptools_url,
)
from sfood_shptools import parse_shptools_url
from map_generator import generate_map_driver_resto, generate_map_single


def sfood_keyboard():
    return ReplyKeyboardMarkup([
        ["🍔 Tambah Order", "📋 List Order"],
        ["❌ Stop Order", "🔙 Menu Utama"],
    ], resize_keyboard=True)


async def cmd_sfood_stop_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    orders = db.sfood_get_orders_by_user(user.id)
    if not orders:
        await update.message.reply_text("📋 Tidak ada order aktif yang bisa dihentikan.")
        return

    ctx.user_data["sfood_stop_state"] = "WAITING_ORDER_ID"
    order_lines = [f"• <code>{html.escape(str(order['order_id']))}</code> — {html.escape(str(order['label'] or '-'))}" for order in orders]
    await update.message.reply_text(
        "Kirim Order ID yang ingin dihentikan:\n\n" + "\n".join(order_lines),
        parse_mode=ParseMode.HTML,
    )


async def handle_sfood_stop_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("sfood_stop_state") != "WAITING_ORDER_ID":
        return False

    user = update.effective_user
    order_id = (update.message.text or "").strip()
    active_orders = db.sfood_get_orders_by_user(user.id)
    active_ids = {str(order["order_id"]) for order in active_orders}
    if order_id not in active_ids:
        await update.message.reply_text(
            "❌ Order ID tidak ditemukan di daftar aktif kamu. Kirim salah satu ID di atas, atau tekan 🔙 Menu Utama untuk batal."
        )
        return True

    ctx.user_data.pop("sfood_stop_state", None)
    if db.sfood_stop_order(order_id, user_id=user.id):
        await update.message.reply_text(
            f"✅ Order <code>{html.escape(order_id)}</code> dihentikan.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("❌ Order tidak ditemukan atau sudah dihentikan.")
    return True


async def cmd_sfood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🍔 <b>ShopeeFood Tracker</b>\n\n"
        "Aku bisa lacak pesanan ShopeeFood kamu realtime.\n\n"
        "<b>Cara pakai:</b>\n"
        "1. Copy link pesanan dari:\n"
        "   • App ShopeeFood (tombol <b>Bagikan</b>)\n"
        "   • Atau dari <b>shptools.my.id</b>\n"
        "2. Kirim link ke bot ini\n\n"
        "<b>Format:</b>\n"
        "<code>&lt;URL&gt; &lt;label opsional&gt;</code>\n\n"
        "<b>Contoh ShopeeFood:</b>\n"
        "<code>https://www.shopeefood.co.id/tracker?code=xxx&amp;orderId=yyy</code>\n\n"
        "<b>Contoh shptools:</b>\n"
        "<code>https://shptools.my.id/sfood?view=orders&amp;id=330...</code>\n\n"
        "<b>Fitur otomatis:</b>\n"
        "🚗 Update status realtime\n"
        "🗺️ Map posisi kurir & restoran\n"
        "⚡ Notif saat kurir hampir tiba\n"
        "📸 Bukti pengiriman saat selesai\n"
        "📡 Pantau Kurir (alert dekat, stuck, statistik)\n\n"
        "<b>Commands:</b>\n"
        "/sfood — Menu ini\n"
        "/sfood_list — List order aktif\n"
        "/sfood_stop &lt;order_id&gt; — Stop 1 order\n"
        "/sfood_stopall — Stop semua order\n"
        "/chat &lt;pesan&gt; — Chat ke kurir (link shptools)\n"
        "/telemetry — Menu Pantau Kurir"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=sfood_keyboard()
    )


async def cmd_sfood_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    orders = db.sfood_get_orders_by_user(user.id)

    if not orders:
        await update.message.reply_text(
            "📋 Belum ada order yang dilacak.\n\n"
            "Kirim link tracker ShopeeFood untuk mulai."
        )
        return

    lines = [f"📋 <b>Order Aktif ({len(orders)})</b>\n"]
    for o in orders:
        lines.append(
            f"• <code>{o['order_id']}</code>\n"
            f"  📦 {o['label']}\n"
            f"  Status: {o['last_status_text'] or '-'}\n"
            f"  Estimasi tiba: {o['last_eta'] or '-'}\n"
        )
    lines.append("\nStop 1 order: <code>/sfood_stop &lt;order_id&gt;</code>")
    lines.append("Stop semua: <code>/sfood_stopall</code>")
    lines.append("Statistik: <code>/sfood_metrics &lt;order_id&gt;</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_sfood_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "Usage: <code>/sfood_stop &lt;order_id&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return
    order_id = ctx.args[0]
    ok = db.sfood_stop_order(order_id, user_id=user.id)
    if ok:
        await update.message.reply_text(
            f"✅ Order <code>{order_id}</code> dihentikan.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f"❌ Order <code>{order_id}</code> tidak ditemukan atau sudah stop.",
            parse_mode=ParseMode.HTML
        )


async def cmd_sfood_stopall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    count = db.sfood_stop_all_user(user.id)
    await update.message.reply_text(f"✅ {count} order dihentikan.")


async def handle_sfood_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    # Deteksi sumber: ShopeeFood atau shptools
    is_shopeefood = "shopeefood.co.id/tracker" in text
    is_shptools = "shptools.my.id/sfood" in text

    if not (is_shopeefood or is_shptools):
        return

    parts = text.split(maxsplit=1)
    url = parts[0]
    label = parts[1].strip() if len(parts) > 1 else None

    # Extract order_id
    if is_shptools:
        order_id = parse_shptools_url(url)
        code = "shptools"
    else:
        order_id, code = parse_tracker_url(url)

    if not order_id:
        await update.message.reply_text(
            "❌ Link tidak valid.\n\n"
            "Pastikan link dari:\n"
            "• App ShopeeFood (tombol <b>Bagikan</b>)\n"
            "• Shptools.my.id (halaman order)",
            parse_mode=ParseMode.HTML
        )
        return

    if db.sfood_order_exists(order_id):
        await update.message.reply_text(
            f"⚠️ Order <code>{order_id}</code> sudah dilacak.",
            parse_mode=ParseMode.HTML
        )
        return

    msg = await update.message.reply_text("🔍 Verifikasi link...")

    # Fetch otomatis dari sumber manapun
    parsed, order_id_ret, code_or_source = await fetch_tracking_auto(url)

    if not parsed:
        await msg.edit_text(
            "❌ Gagal akses link tracker.\n\n"
            "Pastikan link valid. Kalau dari shptools, cek login masih aktif."
        )
        return

    if order_id_ret:
        order_id = order_id_ret

    if not label:
        label = parsed["store_name"]

    # Cek jumlah order user SEBELUM tambah
    prev_count = db.sfood_count_orders_by_user(user.id)

    db.sfood_add_order(
        user_id=user.id,
        order_id=order_id,
        code=code,
        label=label,
        last_status=parsed["status_code"],
        last_status_text=parsed["status_text"],
        last_eta=parsed["eta_text"],
        last_driver=parsed["driver_name"],
        last_driver_phone=parsed["driver_phone"],
    )

    # Kirim map kalau koordinat tersedia
    driver_lat = parsed.get("driver_lat")
    driver_lng = parsed.get("driver_lng")
    store_lat = parsed.get("store_lat")
    store_lng = parsed.get("store_lng")

    bio = None
    if driver_lat and store_lat:
        bio = generate_map_driver_resto(driver_lat, driver_lng, store_lat, store_lng)
    elif driver_lat:
        bio = generate_map_single(driver_lat, driver_lng)
    elif store_lat:
        bio = generate_map_single(store_lat, store_lng)

    if bio:
        try:
            await update.message.reply_photo(
                photo=InputFile(bio, filename="map.png"),
                caption=f"🗺️ {parsed['store_name']}"
            )
        except Exception as e:
            print(f"[SFOOD] ❌ Map error: {e}")

    await msg.edit_text(
        f"✅ <b>Order dilacak!</b>\n\n"
        f"📦 Label: {label}\n"
        f"🏪 Restoran: {parsed['store_name']}\n"
        f"🚗 Status: {parsed['status_text']}\n"
        f"⏱️ Estimasi tiba: {parsed['eta_text']}\n"
        f"🧑 Kurir: {parsed['driver_name']}\n"
        f"📞 HP: <code>{parsed['driver_phone']}</code>\n"
        f"🏍️ Plat: {parsed['driver_plate']}\n\n"
        f"Aku akan kirim notif setiap ada perubahan status.\n"
        f"Order ID: <code>{order_id}</code>",
        parse_mode=ParseMode.HTML
    )
    print(f"[SFOOD] ✅ New order tracked: {order_id} by user {user.id} (source: {'shptools' if is_shptools else 'shopee'})")

    # Info Pantau Kurir kalau first time
    if prev_count == 0:
        info_text = (
            "💡 <b>Fitur Tambahan: Pantau Kurir</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Biar makin gampang pantau pesanan:\n\n"
            "🚨 <b>Alert Kurir Dekat</b>\n"
            "   Bot kasih notif + map saat kurir dekat lokasi kamu\n\n"
            "⏸️ <b>Alert Kurir Stuck</b>\n"
            "   Bot kasih tau kalau kurir diam lama\n\n"
            "📊 <b>Statistik Order</b>\n"
            "   Info durasi & timeline pesanan kamu\n\n"
            "📍 <b>Lokasi Saya</b>\n"
            "   Share lokasi, bot hitung jarak ke kurir\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Commands:</b>\n"
            "<code>/telemetry</code> — Menu lengkap\n"
            "<code>/pantau_on dekat</code> — Aktifkan Alert Dekat\n"
            "<code>/pantau_off dekat</code> — Matikan\n"
            "<code>/jarak_set 1.5</code> — Ubah jarak (km)\n"
            "<code>/sfood_metrics &lt;order_id&gt;</code> — Statistik\n"
            "<code>/lokasi_saya</code> — Share lokasi\n\n"
            "<i>💡 Default: Alert Dekat (1 km), Alert Stuck (15 menit).</i>"
        )
        try:
            await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim info: {e}")