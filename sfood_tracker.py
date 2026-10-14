import asyncio

import database as db
from sfood_client import fetch_tracking, parse_tracking
from sfood_config import SFOOD_POLL_INTERVAL


def fmt_status_update(order, data):
    label = order["label"]
    return (
        f"🍔 *Update ShopeeFood*\n"
        f"📦 {label}\n\n"
        f"🚗 Status: *{data['status_text']}*\n"
        f"⏱️ ETA: {data['eta_text']}\n"
        f"🏪 {data['store_name']}\n\n"
        f"🧑 Driver: {data['driver_name']}\n"
        f"📞 HP: `{data['driver_phone']}`\n"
        f"🏍️ Plat: {data['driver_plate']}"
    )


def fmt_arriving(order, data):
    label = order["label"]
    return (
        f"⚡ *PENGEMUDI HAMPIR TIBA!*\n\n"
        f"📦 {label}\n"
        f"🏪 {data['store_name']}\n\n"
        f"🧑 {data['driver_name']}\n"
        f"📞 `{data['driver_phone']}`\n"
        f"🏍️ {data['driver_plate']}\n\n"
        f"Segera siap-siap terima pesananmu!"
    )


def fmt_done(order, data):
    label = order["label"]
    return (
        f"✅ *PESANAN SELESAI*\n\n"
        f"📦 {label}\n"
        f"🏪 {data['store_name']}\n"
        f"📝 {data['item_count']} item\n"
        f"💰 Rp {data['total_rp']:,}".replace(",", ".") + "\n\n"
        f"Tracking dihentikan otomatis."
    )


def fmt_cancelled(order, data):
    label = order["label"]
    return (
        f"❌ *PESANAN DIBATALKAN*\n\n"
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
            await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim notif done: {e}")
        db.sfood_stop_order(order_id)
        print(f"[SFOOD] ✅ Order {order_id} SELESAI")
        return

    # === DIBATALKAN ===
    if parsed["is_cancelled"]:
        text = fmt_cancelled(order, parsed)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
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
                await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
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
            await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim notif status: {e}")

        db.sfood_update_status(order_id, current_status, parsed["eta_text"],
                                parsed["driver_name"], parsed["driver_phone"])
        db.sfood_add_log(order_id, current_status, parsed["status_text"])
        print(f"[SFOOD] 🔔 Order {order_id}: {last_status} → {current_status} ({parsed['status_text']})")


async def tracker_worker(bot):
    """Background worker: polling order aktif setiap N detik."""
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