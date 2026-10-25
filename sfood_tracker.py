import asyncio
import os
import time
from html import escape
from telegram import InputFile
from telegram.constants import ParseMode

import database as db
import sfood_shptools
from sfood_client import fetch_tracking, parse_tracking, fetch_tracking_by_id
from sfood_telemetry import haversine_distance
from map_generator import generate_map_driver_resto, generate_map_single

POLL_INTERVAL = 30
NEAR_KM = 1.0
ADMIN_ALERT_INTERVAL = 3600
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

_near_alerted = set()
_chat_cursor = {}
_chat_retry_at = {}
_last_admin_alert = 0.0


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
        f"🏍️ Plat: {escape(str(parsed.get('driver_plate', '-')))}\n"
        f"📞 HP: <code>{escape(str(parsed['driver_phone']))}</code>"
    )


def _build_map(parsed):
    d_lat, d_lng = parsed.get("driver_lat"), parsed.get("driver_lng")
    s_lat, s_lng = parsed.get("store_lat"), parsed.get("store_lng")
    if d_lat and s_lat:
        return generate_map_driver_resto(d_lat, d_lng, s_lat, s_lng)
    if d_lat:
        return generate_map_single(d_lat, d_lng)
    if s_lat:
        return generate_map_single(s_lat, s_lng)
    return None


async def _notify(bot, order, parsed, finished, cancelled):
    chat_id = order["user_id"]
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_build_message(order, parsed, finished, cancelled),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print(f"[SFOOD] ❌ Gagal kirim notif: {e}")
        return
    if finished or cancelled:
        return
    try:
        bio = _build_map(parsed)
        if bio:
            await bot.send_photo(chat_id=chat_id, photo=InputFile(bio, filename="map.png"))
    except Exception as e:
        print(f"[SFOOD] ❌ Map error: {e}")


async def _check_near(bot, order, parsed):
    oid = order["order_id"]
    if oid in _near_alerted:
        return
    dist = haversine_distance(
        parsed.get("driver_lat"), parsed.get("driver_lng"),
        parsed.get("deliv_lat"), parsed.get("deliv_lng"),
    )
    if dist is None or dist > NEAR_KM:
        return
    _near_alerted.add(oid)
    try:
        await bot.send_message(
            chat_id=order["user_id"],
            text=(
                f"🚨 <b>KURIR DEKAT</b>\n\n"
                f"{escape(str(parsed['driver_name']))} sekitar <b>{dist:.2f} km</b> dari alamatmu.\n"
                f"Siap-siap ya!"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print(f"[SFOOD] ❌ Gagal kirim alert dekat: {e}")


async def _poll_chat(bot, order, parsed):
    """Teruskan pesan kurir (shptools) ke Telegram."""
    oid = order["order_id"]
    if time.time() < _chat_retry_at.get(oid, 0):
        return
    driver_id = parsed.get("driver_id", "")
    if not driver_id:
        # Order dari link shopeefood.co.id: ambil driver id lewat shptools (order_id sama)
        detail = await sfood_shptools.fetch_order_detail(oid)
        driver_id = str(((detail or {}).get("driver") or {}).get("id") or "")
    if not await sfood_shptools.chat_open(oid, driver_id):
        # Kurir belum ada / order tidak ada di akun shptools: coba lagi 2 menit lagi
        _chat_retry_at[oid] = time.time() + 120
        return
    after = _chat_cursor.get(oid, "0")
    messages = await sfood_shptools.chat_fetch(oid, after)
    if not messages:
        return
    for m in messages:
        mid = str(m.get("id") or "")
        if mid:
            _chat_cursor[oid] = mid
        if m.get("from") != "driver":
            continue
        if m.get("type") == "text":
            body = escape(str(m.get("text") or ""))
        else:
            link = m.get("url") or m.get("thumb") or ""
            body = f"[{escape(str(m.get('type') or 'media'))}] {escape(str(link))}"
        try:
            await bot.send_message(
                chat_id=order["user_id"],
                text=(
                    f"💬 <b>PESAN KURIR</b> ({escape(str(parsed['driver_name']))})\n\n"
                    f"{body}\n\n"
                    f"<i>Reply pesan ini untuk membalas.</i>\n"
                    f"Order ID: <code>{oid}</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal teruskan chat: {e}")


async def _alert_admin_if_auth_failed(bot):
    global _last_admin_alert
    if not sfood_shptools.AUTH_FAILED:
        return
    if time.time() - _last_admin_alert < ADMIN_ALERT_INTERVAL:
        return
    _last_admin_alert = time.time()
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    "⚠️ <b>Cookie shptools ditolak</b>\n\n"
                    "Tracking order shptools berhenti. Login ulang di shptools.my.id, "
                    "update <code>SHPTOOLS_WEB_AUTH</code> di VPS, lalu restart bot."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[SFOOD] ❌ Gagal kirim alert admin: {e}")


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
                # Notif hanya saat status/kurir berubah (ETA sering bergeser, tidak dihitung)
                changed = (
                    parsed["status_code"] != order["last_status"]
                    or parsed["status_text"] != (order["last_status_text"] or "")
                    or parsed["driver_name"] != (order["last_driver"] or "")
                )

                if changed or finished or cancelled:
                    await _notify(bot, order, parsed, finished, cancelled)

                if finished or cancelled:
                    db.sfood_mark_order_finished(order["order_id"])
                    _near_alerted.discard(order["order_id"])
                    _chat_cursor.pop(order["order_id"], None)
                    _chat_retry_at.pop(order["order_id"], None)
                    print(f"[SFOOD] Pesanan {order['order_id']} selesai & dihapus dari antrean.")
                else:
                    db.sfood_update_order_state(
                        order["order_id"], parsed["status_code"], parsed["status_text"],
                        parsed["eta_text"], parsed["driver_name"], parsed["driver_phone"],
                    )
                    await _check_near(bot, order, parsed)
                    await _poll_chat(bot, order, parsed)

            await _alert_admin_if_auth_failed(bot)
            await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"[SFOOD] ❌ Worker error: {e}")
            await asyncio.sleep(10)
