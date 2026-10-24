import asyncio
from html import escape
from telegram.constants import ParseMode

import database as db
from sfood_client import fetch_tracking, parse_tracking, fetch_tracking_by_id

POLL_INTERVAL = 30


async def _fetch_parsed(order):
    order_id = order["order_id"]
    code = order["code"] or ""
    if code == "shptools":
        return await fetch_tracking_by_id(order_id, "shptools")
    if not code:
        return None
    data = await fetch_tracking(order_id, code)
    return parse_tracking(data) if data else None


def _build_message(order, parsed, finished, cancelled):
    if cancelled:
        title = "❌ <b>PESANAN DIBATALKAN</b>"
    elif finished:
        title = "✅ <b>PESANAN SELESAI</b>"
    else:
        title = "🔔 <b>UPDATE PESANAN</b>"
    label = escape(str(order["label"] or parsed.get("store_name") or order["order_id"]))
    return (
        f"{title}\n\n"
        f"📦 {label}\n"
        f"🚗 Status: {escape(str(parsed['status_text']))}\n"
        f"⏱️ Estimasi tiba: {escape(str(parsed['eta_text']))}\n"
        f"🧑 Kurir: {escape(str(parsed['driver_name']))}\n"
        f"📞 HP: <code>{escape(str(parsed['driver_phone']))}</code>"
    )


async def tracker_worker(bot):
    print("[SFOOD] 🚀 Tracker worker started")
    while True:
        try:
            for order in db.sfood_get_active_orders():
                try:
                    parsed = await _fetch_parsed(order)
                except Exception as e:
                    print(f"[SFOOD] ❌ Poll error {order['order_id']}: {e}")
                    continue
                if not parsed:
                    # Gagal fetch: jangan tandai selesai, coba lagi siklus berikutnya
                    continue

                finished = bool(parsed.get("is_done"))
                cancelled = bool(parsed.get("is_cancelled"))
                changed = (
                    parsed["status_code"] != order["last_status"]
                    or parsed["status_text"] != (order["last_status_text"] or "")
                    or parsed["driver_name"] != (order["last_driver"] or "")
                    or parsed["eta_text"] != (order["last_eta"] or "")
                )

                if changed or finished or cancelled:
                    try:
                        await bot.send_message(
                            chat_id=order["user_id"],
                            text=_build_message(order, parsed, finished, cancelled),
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception as e:
                        print(f"[SFOOD] ❌ Gagal kirim notif: {e}")

                if finished or cancelled:
                    db.sfood_mark_order_finished(order["order_id"])
                    print(f"[SFOOD] Pesanan {order['order_id']} selesai & dihapus dari antrean.")
                else:
                    db.sfood_update_order_state(
                        order["order_id"], parsed["status_code"], parsed["status_text"],
                        parsed["eta_text"], parsed["driver_name"], parsed["driver_phone"],
                    )

            await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"[SFOOD] ❌ Worker error: {e}")
            await asyncio.sleep(10)
