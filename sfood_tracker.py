import asyncio

import database as db
from sfood_client import fetch_tracking, parse_tracking
from sfood_config import SFOOD_POLL_INTERVAL


def fmt_gmaps_link(lat, lng):
    """Bikin link Google Maps dari koordinat."""
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
    """Bangun section peta dari data koordinat."""
    links = []

    # 1. Driver (posisi terakhir diketahui)
    if data.get("driver_lat") and data.get("driver_lng"):
        link = fmt_gmaps_link(data["driver_lat"], data["driver_lng"])
        if link:
            links.append(f"   🚗 <a href='{link}'>Posisi Driver</a>")

    # 2. Restoran
    if data.get("store_lat") and data.get("store_lng"):
        link = fmt_gmaps_link(data["store_lat"], data["store_lng"])
        if link:
            links.append(f"   🏪 <a href='{link}'>Lokasi Restoran</a>")

    # 3. Alamat pengiriman
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
        f"⏱️ ETA: {data['eta_text']}",
        f"🏪 {data['store_name']}",
    ]

    if data.get('driver_name') and data['driver_name'] != "-":
        lines.append("")
        lines.append(f"🧑 Driver: {data['driver_name']}")
        if data.get('driver_phone') and data['driver_phone'] != "-":
            lines.append(f"📞 HP: <code>{data['driver_phone']}</code>")
        if data.get('driver_plate') and data['driver_plate'] != "-":
            lines.append(f"🏍️ Plat: {data['driver_plate']}")

    lines.extend(build_maps_section(data))

    return "\n".join(lines)


def fmt_arriving(order, data):
    label = order["label"]
    lines = [
        f"⚡ <b>PENGEMUDI HAMPIR TIBA!</b>",
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
    lines.append("Segera siap-siap terima pesananmu!")
    return "\n".join(lines)


def fmt_done(order, data):
    label = order["label"]
    total_str = f"Rp {data['total_rp']:,}".replace(",", ".")
    return (
        f"✅ <b>PESANAN SELESAI</b>\n\n"
        f"📦 {label}\n"
        f"🏪 {data['store_name']}\n"
        f"📝 {data['item_count']} item\n"
        f"💰 {total_str}\n\n"
        f"Tracking dihentikan otomatis."
    )


def fmt_cancelled(order, data):
    label = order["label"]
    return (
        f"❌ <b>PESANAN DIBATALKAN</b>\n\n"
        f"📦 {label}\n"
        f"🏪 {data['store_name']}\n\n"
        f"Tracking dihentikan."
    )


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
        text = fmt_done(order, parsed)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim notif done: {e}")
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
        db.sfood_stop_order(order_id)
        print(f"[SFOOD] ❌ Order {order_id} DIBATALKAN")
        return

    # === HAMPIR TIBA ===
    if parsed["is_arriving"]:
        if order.get("last_flag") != "arriving":
            text = fmt_arriving(order, parsed)
            try:
                await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
            except Exception as e:
                print(f"[SFOOD] ❌ Gagal kirim notif arriving: {e}")
            db.sfood_update_flag(order_id, "arriving")

        db.sfood_update_status(order_id, current_status, parsed["eta_text"],
                                parsed["driver_name"], parsed["driver_phone"])
        return

    # === PERUBAHAN STATUS ===
    if current_status != last_status:
        text = fmt_status_update(order, parsed)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim notif status: {e}")

        db.sfood_update_status(order_id, current_status, parsed["eta_text"],
                                parsed["driver_name"], parsed["driver_phone"])
        db.sfood_add_log(order_id, current_status, parsed["status_text"])
        print(f"[SFOOD] 🔔 Order {order_id}: {last_status} → {current_status} ({parsed['status_text']})")


async def tracker_worker(bot):
    """Background worker: polling semua order aktif setiap N detik."""
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