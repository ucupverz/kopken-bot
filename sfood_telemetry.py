"""
Modul telemetri ShopeeFood.
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


async def cmd_telemetry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    s = db.tel_get_settings(user.id)

    proxim_status = "🟢 ON" if s["proximity_enabled"] else "⚪ OFF"
    stag_status = "🟢 ON" if s["stagnation_enabled"] else "⚪ OFF"
    live_status = "🟢 ON" if s["live_loc_enabled"] else "⚪ OFF"

    text = (
        "📡 <b>Telemetri ShopeeFood</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Fitur:</b>\n\n"
        "1️⃣ <b>Proximity Alert</b> — "
        f"{proxim_status}\n"
        f"   📏 Threshold: <b>{s['proximity_km']} km</b>\n"
        "   <i>Notif + map otomatis saat driver dekat lokasi kamu.</i>\n\n"
        "2️⃣ <b>Stagnation Detector</b> — "
        f"{stag_status}\n"
        f"   ⏱️ Threshold: <b>{s['stagnation_min']} menit</b>\n"
        "   <i>Alert kalau driver stuck lama (macet / antar order lain).</i>\n\n"
        "3️⃣ <b>Live Location</b> — "
        f"{live_status}\n"
        "   <i>Share lokasi kamu ke bot, bot hitung jarak ke driver.</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Command:</b>\n"
        "<code>/telemetry_on proximity</code> — Aktifkan\n"
        "<code>/telemetry_off proximity</code> — Matikan\n"
        "<code>/proximity_set 1.5</code> — Ubah threshold km\n"
        "<code>/stagnation_set 20</code> — Ubah threshold menit\n"
        "<code>/sfood_metrics &lt;order_id&gt;</code> — Metrik\n"
        "<code>/live_loc</code> — Share lokasi"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_telemetry_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: <code>/telemetry_on proximity|stagnation|live_loc</code>",
                                         parse_mode=ParseMode.HTML)
        return
    feat = ctx.args[0].lower()
    if feat == "proximity":
        db.tel_update_settings(user.id, proximity_enabled=1)
        await update.message.reply_text("✅ Proximity Alert: <b>ON</b>", parse_mode=ParseMode.HTML)
    elif feat == "stagnation":
        db.tel_update_settings(user.id, stagnation_enabled=1)
        await update.message.reply_text("✅ Stagnation Detector: <b>ON</b>", parse_mode=ParseMode.HTML)
    elif feat in ("live_loc", "liveloc"):
        db.tel_update_settings(user.id, live_loc_enabled=1)
        await update.message.reply_text("✅ Live Location: <b>ON</b>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Fitur tidak dikenal.")


async def cmd_telemetry_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: <code>/telemetry_off proximity|stagnation|live_loc</code>",
                                         parse_mode=ParseMode.HTML)
        return
    feat = ctx.args[0].lower()
    if feat == "proximity":
        db.tel_update_settings(user.id, proximity_enabled=0)
        await update.message.reply_text("⚪ Proximity Alert: <b>OFF</b>", parse_mode=ParseMode.HTML)
    elif feat == "stagnation":
        db.tel_update_settings(user.id, stagnation_enabled=0)
        await update.message.reply_text("⚪ Stagnation Detector: <b>OFF</b>", parse_mode=ParseMode.HTML)
    elif feat in ("live_loc", "liveloc"):
        db.tel_update_settings(user.id, live_loc_enabled=0)
        await update.message.reply_text("⚪ Live Location: <b>OFF</b>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Fitur tidak dikenal.")


async def cmd_proximity_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: <code>/proximity_set 1.5</code>", parse_mode=ParseMode.HTML)
        return
    try:
        km = float(ctx.args[0])
        if km < 0.1 or km > 20:
            await update.message.reply_text("❌ Threshold harus antara 0.1 - 20 km.")
            return
        db.tel_update_settings(user.id, proximity_km=km)
        await update.message.reply_text(f"✅ Proximity threshold: <b>{km} km</b>", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("❌ Format salah. Contoh: <code>/proximity_set 1.5</code>",
                                         parse_mode=ParseMode.HTML)


async def cmd_stagnation_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text("Usage: <code>/stagnation_set 20</code>", parse_mode=ParseMode.HTML)
        return
    try:
        min_val = int(ctx.args[0])
        if min_val < 5 or min_val > 120:
            await update.message.reply_text("❌ Threshold harus antara 5 - 120 menit.")
            return
        db.tel_update_settings(user.id, stagnation_min=min_val)
        await update.message.reply_text(f"✅ Stagnation threshold: <b>{min_val} menit</b>", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("❌ Format salah.")


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
            f"📊 Belum ada metrik untuk order ini.\n\n"
            "Metrik dicatat otomatis saat status berubah.",
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
        f"📊 <b>METRIK PERJALANAN</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>{order['label']}</b>\n"
        f"🆔 <code>{order_id}</code>\n\n"
    )

    dur_pickup = (picked_up - confirmed) // 60 if picked_up and confirmed else None
    dur_delivery = (arrived - picked_up) // 60 if arrived and picked_up else None
    dur_total = (arrived - confirmed) // 60 if arrived and confirmed else None

    if dur_pickup is not None:
        text += f"⏱️ <b>Durasi:</b>\n"
        text += f"   • Konfirmasi → Pickup: {dur_pickup} menit\n"
        if dur_delivery is not None:
            text += f"   • Pickup → Sampai: {dur_delivery} menit\n"
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
        text += f"   {icon} {ts_to_wib(e['timestamp'])} — {e['event']}\n"

    text += f"\n⏰ Dibuat: {ts_to_wib(created)}\n"
    if done:
        text += f"✅ Selesai: {ts_to_wib(done)}\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_live_loc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📍 <b>Live Location</b>\n\n"
        "Untuk tracking jarak ke driver, share <b>Live Location</b> kamu ke bot ini:\n\n"
        "1. Tap ikon 📎 (attachment)\n"
        "2. Pilih <b>Location</b>\n"
        "3. Pilih <b>Share My Live Location</b>\n"
        "4. Pilih durasi\n"
        "5. Kirim ke bot\n\n"
        "Bot akan hitung jarak dari posisi kamu ke driver secara periodik.\n\n"
        "<i>Catatan: Driver tidak expose GPS realtime. Jarak dihitung dari snapshot posisi terakhir driver.</i>"
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
            f"⚠️ Posisi driver belum tersedia. Tunggu update berikutnya.",
            parse_mode=ParseMode.HTML
        )
        return

    dist = haversine_distance(lat, lng, driver_lat, driver_lng)
    if dist is not None:
        text = (
            f"📍 <b>Live Location Update</b>\n\n"
            f"📦 {order['label']}\n"
            f"🧑 Kamu: <code>{lat:.6f}, {lng:.6f}</code>\n"
            f"🏍️ Driver: <code>{driver_lat:.6f}, {driver_lng:.6f}</code>\n"
            f"📏 Jarak: <b>{dist:.2f} km</b>"
        )
        await msg.reply_text(text, parse_mode=ParseMode.HTML)