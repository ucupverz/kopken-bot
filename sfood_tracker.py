import asyncio
from io import BytesIO

from telegram import InputFile

import database as db
from sfood_client import fetch_tracking, parse_tracking
from sfood_config import SFOOD_POLL_INTERVAL
from map_generator import generate_map_driver_resto, generate_map_single
from sfood_telemetry import haversine_distance


def fmt_gmaps_link(lat, lng):
    if not lat or not lng:
        return None
    try:
        lat_f = float(lat)
        lng_f = float(lng)
        if lat_f == 0 and lng_f == 0:
            return None
        return f"https://www.google.com/maps?q={lat_f},{lng_f}"
    except Exception:
        return None


def build_maps_section(data):
    links = []
    if data.get("driver_lat") and data.get("driver_lng"):
        link = fmt_gmaps_link(data["driver_lat"], data["driver_lng"])
        if link:
            links.append(f"   🚗 <a href='{link}'>Posisi Kurir</a>")
    if data.get("store_lat") and data.get("store_lng"):
        link = fmt_gmaps_link(data["store_lat"], data["store_lng"])
        if link:
            links.append(f"   🏪 <a href='{link}'>Lokasi Restoran</a>")
    if data.get("deliv_lat") and data.get("deliv_lng"):
        link = fmt_gmaps_link(data["deliv_lat"], data["deliv_lng"])
        if link:
            links.append(f"   🏠 <a href='{link}'>Alamat Pengiriman</a>")
    if not links:
        return []
    return ["", "🗺️ <b>Peta:</b>"] + links


def fmt_status_update(order, data):
    label = order["label"]
    lines = [
        f"🍔 <b>Update ShopeeFood</b>",
        f"📦 {label}",
        "",
        f"🚗 Status: <b>{data['status_text']}</b>",
        f"⏱️ Estimasi tiba: {data['eta_text']}",
        f"🏪 {data['store_name']}",
    ]
    if data.get('driver_name') and data['driver_name'] != "-":
        lines.append("")
        lines.append(f"🧑 Kurir: {data['driver_name']}")
        if data.get('driver_phone') and data['driver_phone'] != "-":
            lines.append(f"📞 HP: <code>{data['driver_phone']}</code>")
        if data.get('driver_plate') and data['driver_plate'] != "-":
            lines.append(f"🏍️ Plat: {data['driver_plate']}")
    lines.extend(build_maps_section(data))
    return "\n".join(lines)


def fmt_arriving(order, data):
    label = order["label"]
    lines = [
        f"⚡ <b>KURIR HAMPIR TIBA!</b>",
        "",
        f"📦 {label}",
        f"🏪 {data['store_name']}",
        "",
    ]
    if data.get('driver_name') and data['driver_name'] != "-":
        lines.append(f"🧑 {data['driver_name']}")
    if data.get('driver_phone') and data['driver_phone'] != "-":
        lines.append(f"📞 <code>{data['driver_phone']}</code>")
    if data.get('driver_plate') and data['driver_plate'] != "-":
        lines.append(f"🏍️ {data['driver_plate']}")
    lines.extend(build_maps_section(data))
    lines.append("")
    lines.append("Siap-siap terima pesanan!")
    return "\n".join(lines)


def fmt_done(order, data):
    label = order["label"]
    total_str = f"Rp {data['total_rp']:,}".replace(",", ".")
    text = (
        f"✅ <b>PESANAN SELESAI</b>\n\n"
        f"📦 {label}\n"
        f"🏪 {data['store_name']}\n"
        f"📝 {data['item_count']} item\n"
        f"💰 {total_str}\n"
    )
    if data.get("proof_urls"):
        text += f"\n📸 Bukti pengiriman: {len(data['proof_urls'])} foto."
    text += "\n\nTracking dihentikan otomatis."
    return text


def fmt_cancelled(order, data):
    label = order["label"]
    return (
        f"❌ <b>PESANAN DIBATALKAN</b>\n\n"
        f"📦 {label}\n"
        f"🏪 {data['store_name']}\n\n"
        f"Tracking dihentikan."
    )


async def send_map(bot, user_id, parsed, caption=""):
    driver_lat = parsed.get("driver_lat")
    driver_lng = parsed.get("driver_lng")
    store_lat = parsed.get("store_lat")
    store_lng = parsed.get("store_lng")

    if not (driver_lat or store_lat):
        return

    try:
        bio = None
        if driver_lat and store_lat:
            bio = generate_map_driver_resto(driver_lat, driver_lng, store_lat, store_lng)
        elif driver_lat:
            bio = generate_map_single(driver_lat, driver_lng)
        elif store_lat:
            bio = generate_map_single(store_lat, store_lng)
        if bio:
            await bot.send_photo(
                chat_id=user_id,
                photo=InputFile(bio, filename="map.png"),
                caption=caption or "🗺️ Peta lokasi"
            )
    except Exception as e:
        print(f"[SFOOD] ❌ Map error: {e}")


async def send_proof_images(bot, user_id, parsed, label):
    proof_urls = parsed.get("proof_urls") or []
    if not proof_urls:
        return
    for i, url in enumerate(proof_urls[:3], 1):
        try:
            caption = f"📸 <b>Bukti Pengiriman #{i}</b>\n📦 {label}"
            await bot.send_photo(
                chat_id=user_id,
                photo=url,
                caption=caption,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim bukti #{i}: {e}")


async def process_telemetry(bot, order, parsed):
    """Proses fitur pantau kurir."""
    user_id = order["user_id"]
    order_id = order["order_id"]
    settings = db.tel_get_settings(user_id)

    driver_lat = parsed.get("driver_lat", 0)
    driver_lng = parsed.get("driver_lng", 0)
    deliv_lat = parsed.get("deliv_lat", 0)
    deliv_lng = parsed.get("deliv_lng", 0)

    bot.bot_data[f"driver_lat_{order_id}"] = driver_lat
    bot.bot_data[f"driver_lng_{order_id}"] = driver_lng

    # 1. Alert Kurir Dekat
    if settings["proximity_enabled"] and driver_lat and deliv_lat:
        dist = haversine_distance(driver_lat, driver_lng, deliv_lat, deliv_lng)
        if dist is not None and dist <= settings["proximity_km"]:
            last_flag = order.get("last_flag") or ""
            flag_key = f"proximity_{int(settings['proximity_km']*100)}"
            if flag_key not in last_flag:
                await send_proximity_alert(bot, user_id, order, parsed, dist)
                new_flag = f"{last_flag},{flag_key}".strip(",")
                db.sfood_update_flag(order_id, new_flag)

    # 2. Alert Kurir Stuck
    if settings["stagnation_enabled"]:
        await check_stagnation(bot, user_id, order, settings)


async def send_proximity_alert(bot, user_id, order, parsed, dist):
    label = order["label"]
    driver_name = parsed.get("driver_name") or "-"
    driver_lat = parsed.get("driver_lat", 0)
    driver_lng = parsed.get("driver_lng", 0)
    deliv_lat = parsed.get("deliv_lat", 0)
    deliv_lng = parsed.get("deliv_lng", 0)

    try:
        if driver_lat and deliv_lat:
            bio = generate_map_driver_resto(driver_lat, driver_lng, deliv_lat, deliv_lng)
            if bio:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=InputFile(bio, filename="proximity.png"),
                    caption=f"🚨 Kurir dekat! Jarak: {dist:.2f} km"
                )
    except Exception as e:
        print(f"[SFOOD] ❌ Proximity map error: {e}")

    text = (
        f"🚨 <b>KURIR DEKAT!</b>\n\n"
        f"📦 {label}\n"
        f"🏍️ {driver_name}\n"
        f"📏 Jarak ke lokasi: <b>{dist:.2f} km</b>\n\n"
        f"Siap-siap terima pesanan!"
    )
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"[SFOOD] ❌ Proximity alert error: {e}")


async def check_stagnation(bot, user_id, order, settings):
    from datetime import datetime, timezone, timedelta
    WIB = timezone(timedelta(hours=7))

    order_id = order["order_id"]
    last_status = order["last_status"]

    stag = db.stag_get(order_id)
    now_ts = int(datetime.now(tz=WIB).timestamp())

    if not stag or stag["last_status"] != last_status:
        db.stag_set(order_id, last_status, now_ts)
        return

    elapsed_min = (now_ts - stag["last_change_at"]) // 60
    threshold = settings["stagnation_min"]

    if elapsed_min >= threshold and not stag["alerted_at"]:
        text = (
            f"⏸️ <b>KURIR LAMA DIAM?</b>\n\n"
            f"📦 {order['label']}\n"
            f"⏱️ Status terakhir: <b>{elapsed_min} menit lalu</b>\n"
            f"📍 Posisi terakhir: {order['last_status_text'] or '-'}\n\n"
            f"<i>Kemungkinan kurir:</i>\n"
            f"• Sedang antar pesanan pembeli lain\n"
            f"• Terjebak macet\n"
            f"• Istirahat / ada kendala\n\n"
            f"Tunggu lagi ya. Kalau lebih dari 30 menit, coba hubungi kurir langsung."
        )
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
            db.stag_mark_alerted(order_id)
            print(f"[SFOOD] ⚠️ Stagnation alert untuk {order_id} ({elapsed_min} menit)")
        except Exception as e:
            print(f"[SFOOD] ❌ Stagnation alert error: {e}")


async def record_metrics(order_id, status_code, status_text):
    event_map = {
        100: "created", 200: "confirmed", 300: "preparing", 310: "preparing",
        410: "preparing", 420: "picked_up", 425: "picked_up", 430: "in_transit",
        435: "picked_up", 440: "in_transit", 445: "arriving", 450: "arrived",
        460: "arrived", 470: "arrived", 500: "arrived", 550: "arrived",
        600: "done", 900: "done", 999: "cancelled",
    }
    event_name = event_map.get(status_code, f"status_{status_code}")
    db.metrics_add(order_id, event_name, status_code, status_text)


async def check_one_order(bot, order):
    order_id = order["order_id"]
    code = order["code"]
    user_id = order["user_id"]
    last_status = order["last_status"]

    data = await fetch_tracking(order_id, code)
    if not data:
        print(f"[SFOOD] ⚠️ Gagal fetch order {order_id}")
        return

    parsed = parse_tracking(data)
    if not parsed:
        return

    current_status = parsed["status_code"]

    # === SELESAI ===
    if parsed["is_done"]:
        await send_proof_images(bot, user_id, parsed, order["label"])
        text = fmt_done(order, parsed)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim notif done: {e}")
        db.metrics_add(order_id, "done", 0, "Pesanan selesai")
        db.sfood_stop_order(order_id)
        print(f"[SFOOD] ✅ Order {order_id} SELESAI")
        return

    # === DIBATALKAN ===
    if parsed["is_cancelled"]:
        text = fmt_cancelled(order, parsed)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim notif cancel: {e}")
        db.metrics_add(order_id, "cancelled", 0, "Pesanan dibatalkan")
        db.sfood_stop_order(order_id)
        print(f"[SFOOD] ❌ Order {order_id} DIBATALKAN")
        return

    # === HAMPIR TIBA ===
    if parsed["is_arriving"]:
        if order.get("last_flag") != "arriving":
            await send_map(bot, user_id, parsed,
                           caption=f"⚡ Kurir hampir tiba: {parsed.get('driver_name') or '-'}")
            text = fmt_arriving(order, parsed)
            try:
                await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
            except Exception as e:
                print(f"[SFOOD] ❌ Gagal kirim notif arriving: {e}")
            db.sfood_update_flag(order_id, "arriving")
            db.metrics_add(order_id, "arriving", current_status, parsed["status_text"])

        db.sfood_update_status(order_id, current_status, parsed["eta_text"],
                                parsed["driver_name"], parsed["driver_phone"])
        await process_telemetry(bot, order, parsed)
        return

    # === PERUBAHAN STATUS ===
    if current_status != last_status:
        await send_map(bot, user_id, parsed,
                       caption=f"🗺️ Update: {parsed.get('status_text') or '-'}")
        text = fmt_status_update(order, parsed)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim notif status: {e}")

        db.sfood_update_status(order_id, current_status, parsed["eta_text"],
                                parsed["driver_name"], parsed["driver_phone"])
        db.sfood_add_log(order_id, current_status, parsed["status_text"])
        await record_metrics(order_id, current_status, parsed["status_text"])
        await process_telemetry(bot, order, parsed)
        print(f"[SFOOD] 🔔 Order {order_id}: {last_status} → {current_status}")


async def tracker_worker(bot):
    print("[SFOOD] 🚀 Tracker worker started")
    while True:
        try:
            orders = db.sfood_get_active_orders()
            if orders:
                print(f"[SFOOD] 🔍 Polling {len(orders)} order aktif...")
                for order in orders:
                    try:
                        await check_one_order(bot, order)
                    except Exception as e:
                        print(f"[SFOOD] ❌ Error check order {order['order_id']}: {e}")
                    await asyncio.sleep(1)
            await asyncio.sleep(SFOOD_POLL_INTERVAL)
        except Exception as e:
            print(f"[SFOOD] ❌ Worker error: {e}")
            await asyncio.sleep(30)