"""
Modul pantau kurir ShopeeFood (bahasa umum).
"""

import math
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db


WIB = timezone(timedelta(hours=7))


def haversine_distance(lat1, lng1, lat2, lng2):
    """Hitung jarak (km) antara 2 koordinat pakai Haversine."""
    try:
        lat1, lng1, lat2, lng2 = float(lat1), float(lng1), float(lat2), float(lng2)
    except Exception:
        return None
    if lat1 == 0 and lng1 == 0:
        return None
    if lat2 == 0 and lng2 == 0:
        return None

    R = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) *
         math.sin(dlng / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def ts_to_wib(ts):
    if not ts:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=WIB)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return "-"


# ======================================================================
# MENU PANTAU KURIR
# ======================================================================

async def cmd_telemetry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    s = db.tel_get_settings(user.id)

    dekat_status = "🟢 Aktif" if s["proximity_enabled"] else "⚪ Mati"
    stuck_status = "🟢 Aktif" if s["stagnation_enabled"] else "⚪ Mati"
    lokasi_status = "🟢 Aktif" if s["live_loc_enabled"] else "⚪ Mati"

    text = (
        "📡 <b>Pantau Kurir</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Fitur biar kamu makin gampang pantau pesanan:\n\n"
        "🚨 <b>Alert Kurir Dekat</b> — "
        f"{dekat_status}\n"
        f"   📏 Jarak alert: <b>{s['proximity_km']} km</b>\n"
        "   <i>Bot kirim notif + map saat kurir dekat lokasi kamu.</i>\n\n"
        "⏸️ <b>Alert Kurir Stuck</b> — "
        f"{stuck_status}\n"
        f"   ⏱️ Waktu tunggu: <b>{s['stagnation_min']} menit</b>\n"
        "   <i>Bot kasih tau kalau kurir diam terlalu lama.</i>\n\n"
        "📍 <b>Lokasi Saya</b> — "
        f"{lokasi_status}\n"
        "   <i>Share lokasi kamu, bot hitung jarak ke kurir.</i>\n\n"
        "📊 <b>Statistik Order</b>\n"
        "   <i>Info durasi & timeline pesanan kamu.</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Cara pakai:</b>\n"
        "<code>/pantau_on dekat</code> — Aktifkan Alert Dekat\n"
        "<code>/pantau_off dekat</code> — Matikan\n"
        "<code>/pantau_on stuck</code> — Aktifkan Alert Stuck\n"
        "<code>/jarak_set 1.5</code> — Ubah jarak alert\n"
        "<code>/stuck_set 20</code> — Ubah waktu alert\n"
        "<code>/sfood_metrics &lt;order_id&gt;</code> — Statistik\n"
        "<code>/lokasi_saya</code> — Share lokasi"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_pantau_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Aktifkan fitur pantau kurir."""
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "Pilih fitur:\n"
            "<code>/pantau_on dekat</code> — Alert kurir dekat\n"
            "<code>/pantau_on stuck</code> — Alert kurir stuck\n"
            "<code>/pantau_on lokasi</code> — Share lokasi",
            parse_mode=ParseMode.HTML
        )
        return

    feat = ctx.args[0].lower()
    if feat in ("dekat", "proximity"):
        db.tel_update_settings(user.id, proximity_enabled=1)
        await update.message.reply_text(
            "🚨 <b>Alert Kurir Dekat: AKTIF</b>\n\n"
            "Bot akan kirim notif + map saat kurir dekat lokasi kamu.",
            parse_mode=ParseMode.HTML
        )
    elif feat in ("stuck", "stagnation"):
        db.tel_update_settings(user.id, stagnation_enabled=1)
        await update.message.reply_text(
            "⏸️ <b>Alert Kurir Stuck: AKTIF</b>\n\n"
            "Bot akan kasih tau kalau kurir diam terlalu lama.",
            parse_mode=ParseMode.HTML
        )
    elif feat in ("lokasi", "loc", "live_loc"):
        db.tel_update_settings(user.id, live_loc_enabled=1)
        await update.message.reply_text(
            "📍 <b>Lokasi Saya: AKTIF</b>\n\n"
            "Sekarang kamu bisa share lokasi ke bot:\n"
            "<code>/lokasi_saya</code> — instruksi lengkap",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("❌ Pilihan: dekat / stuck / lokasi")


async def cmd_pantau_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Matikan fitur pantau kurir."""
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "Pilih fitur:\n"
            "<code>/pantau_off dekat</code>\n"
            "<code>/pantau_off stuck</code>\n"
            "<code>/pantau_off lokasi</code>",
            parse_mode=ParseMode.HTML
        )
        return

    feat = ctx.args[0].lower()
    if feat in ("dekat", "proximity"):
        db.tel_update_settings(user.id, proximity_enabled=0)
        await update.message.reply_text("⚪ Alert Kurir Dekat: <b>MATI</b>", parse_mode=ParseMode.HTML)
    elif feat in ("stuck", "stagnation"):
        db.tel_update_settings(user.id, stagnation_enabled=0)
        await update.message.reply_text("⚪ Alert Kurir Stuck: <b>MATI</b>", parse_mode=ParseMode.HTML)
    elif feat in ("lokasi", "loc", "live_loc"):
        db.tel_update_settings(user.id, live_loc_enabled=0)
        await update.message.reply_text("⚪ Lokasi Saya: <b>MATI</b>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Pilihan: dekat / stuck / lokasi")


async def cmd_jarak_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Set jarak alert kurir dekat (dalam km)."""
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "Berapa jarak alert? Contoh:\n"
            "<code>/jarak_set 1.5</code>\n"
            "<i>Alert saat kurir 1.5 km dari lokasi kamu.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        km = float(ctx.args[0])
        if km < 0.1 or km > 20:
            await update.message.reply_text("❌ Jarak harus antara 0.1 - 20 km.")
            return
        db.tel_update_settings(user.id, proximity_km=km)
        await update.message.reply_text(
            f"✅ Alert Kurir Dekat: <b>{km} km</b>\n\n"
            f"Bot akan notif saat kurir {km} km dari lokasi kamu.",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text("❌ Format salah. Contoh: <code>/jarak_set 1.5</code>",
                                         parse_mode=ParseMode.HTML)


async def cmd_stuck_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Set waktu alert kurir stuck (dalam menit)."""
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "Berapa menit? Contoh:\n"
            "<code>/stuck_set 20</code>\n"
            "<i>Alert kalau kurir diam 20 menit.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        min_val = int(ctx.args[0])
        if min_val < 5 or min_val > 120:
            await update.message.reply_text("❌ Waktu harus antara 5 - 120 menit.")
            return
        db.tel_update_settings(user.id, stagnation_min=min_val)
        await update.message.reply_text(
            f"✅ Alert Kurir Stuck: <b>{min_val} menit</b>\n\n"
            f"Bot akan kasih tau kalau kurir diam {min_val} menit.",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text("❌ Format salah.")


# ======================================================================
# STATISTIK ORDER
# ======================================================================

async def cmd_sfood_metrics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: <code>/sfood_metrics &lt;order_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    order_id = ctx.args[0]
    with db.get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM sfood_orders WHERE order_id = ?", (order_id,))
        order = c.fetchone()

    if not order:
        await update.message.reply_text(f"❌ Order <code>{order_id}</code> tidak ditemukan.", parse_mode=ParseMode.HTML)
        return

    events = db.metrics_get(order_id)
    if not events:
        await update.message.reply_text(
            f"📊 Belum ada statistik untuk order ini.\n\n"
            "Statistik dicatat otomatis saat status berubah.",
            parse_mode=ParseMode.HTML
        )
        return

    event_times = {}
    for e in events:
        if e["event"] not in event_times:
            event_times[e["event"]] = e["timestamp"]

    created = order["created_at"]
    confirmed = event_times.get("confirmed", created)
    picked_up = event_times.get("picked_up", 0)
    arrived = event_times.get("arrived", 0)
    done = event_times.get("done", 0)

    text = (
        f"📊 <b>STATISTIK ORDER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>{order['label']}</b>\n"
        f"🆔 <code>{order_id}</code>\n\n"
    )

    dur_pickup = (picked_up - confirmed) // 60 if picked_up and confirmed else None
    dur_delivery = (arrived - picked_up) // 60 if arrived and picked_up else None
    dur_total = (arrived - confirmed) // 60 if arrived and confirmed else None

    if dur_pickup is not None:
        text += f"⏱️ <b>Durasi:</b>\n"
        text += f"   • Restoran siap → Kurir ambil: {dur_pickup} menit\n"
        if dur_delivery is not None:
            text += f"   • Kurir ambil → Sampai: {dur_delivery} menit\n"
        if dur_total is not None:
            text += f"   • <b>Total: {dur_total} menit</b>\n"
        text += "\n"

    text += f"📅 <b>Timeline:</b>\n"
    for e in events:
        icon = {
            "created": "🆕",
            "confirmed": "✅",
            "preparing": "🍳",
            "picked_up": "🏍️",
            "in_transit": "🚗",
            "arriving": "⚡",
            "arrived": "📍",
            "done": "🏁",
            "cancelled": "❌",
        }.get(e["event"], "•")
        label = {
            "created": "Order dibuat",
            "confirmed": "Restoran konfirmasi",
            "preparing": "Restoran siapkan",
            "picked_up": "Kurir ambil pesanan",
            "in_transit": "Kurir jalan",
            "arriving": "Kurir hampir tiba",
            "arrived": "Pesanan sampai",
            "done": "Selesai",
            "cancelled": "Dibatalkan",
        }.get(e["event"], e["event"])
        text += f"   {icon} {ts_to_wib(e['timestamp'])} — {label}\n"

    text += f"\n⏰ Dibuat: {ts_to_wib(created)}\n"
    if done:
        text += f"✅ Selesai: {ts_to_wib(done)}\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ======================================================================
# LOKASI SAYA
# ======================================================================

async def cmd_lokasi_saya(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📍 <b>Share Lokasi Kamu</b>\n\n"
        "Untuk tracking jarak ke kurir, share <b>Live Location</b> kamu ke bot:\n\n"
        "1. Tap ikon 📎 (attachment)\n"
        "2. Pilih <b>Location</b>\n"
        "3. Pilih <b>Share My Live Location</b>\n"
        "4. Pilih durasi (mis: 15 menit)\n"
        "5. Kirim ke bot\n\n"
        "Bot akan hitung jarak dari kamu ke kurir secara periodik.\n\n"
        "<i>Catatan: Bot hanya bisa hitung jarak berdasarkan data terakhir dari ShopeeFood. Real GPS kurir tidak tersedia untuk bot.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def handle_live_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.location:
        return

    user = update.effective_user
    lat = msg.location.latitude
    lng = msg.location.longitude

    live_locs = ctx.bot_data.get("live_locs", {})
    live_locs[user.id] = (lat, lng)
    ctx.bot_data["live_locs"] = live_locs

    orders = db.sfood_get_orders_by_user(user.id)
    if not orders:
        await msg.reply_text("📍 Lokasi diterima, tapi kamu tidak punya order aktif.")
        return

    active = [o for o in orders if o["active"] == 1]
    if not active:
        await msg.reply_text("📍 Lokasi diterima, tapi tidak ada order aktif.")
        return

    order = active[0]
    order_id = order["order_id"]

    driver_lat = ctx.bot_data.get(f"driver_lat_{order_id}", 0)
    driver_lng = ctx.bot_data.get(f"driver_lng_{order_id}", 0)

    if not driver_lat or not driver_lng:
        await msg.reply_text(
            f"📍 Lokasi kamu: <code>{lat:.6f}, {lng:.6f}</code>\n\n"
            f"⚠️ Posisi kurir belum tersedia. Tunggu update berikutnya.",
            parse_mode=ParseMode.HTML
        )
        return

    dist = haversine_distance(lat, lng, driver_lat, driver_lng)
    if dist is not None:
        text = (
            f"📍 <b>Jarak ke Kurir</b>\n\n"
            f"📦 {order['label']}\n"
            f"📏 Jarak: <b>{dist:.2f} km</b>\n"
            f"🏍️ Posisi kurir: <code>{driver_lat:.4f}, {driver_lng:.4f}</code>"
        )
        await msg.reply_text(text, parse_mode=ParseMode.HTML)