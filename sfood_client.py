import httpx
import re
from datetime import datetime, timezone, timedelta

from sfood_config import SFOOD_API_HEADERS, SFOOD_API_BASE
from sfood_shptools import (
    fetch_order_detail as shptools_fetch_order,
    fetch_driver_location as shptools_fetch_driver,
    parse_shptools_response,
    parse_shptools_url,
)


WIB = timezone(timedelta(hours=7))


STATUS_MAP = {
    100: "Order baru masuk",
    110: "Menunggu konfirmasi restoran",
    200: "Restoran konfirmasi pesanan",
    210: "Restoran menerima pesanan",
    300: "Restoran menyiapkan pesananmu",
    310: "Restoran menyiapkan pesananmu",
    400: "Mencari driver",
    410: "Restoran menyiapkan pesananmu",
    420: "Driver menuju restoran",
    425: "Driver menuju restoran",
    430: "Driver dalam perjalanan",
    435: "Driver mengambil pesanan",
    440: "Driver menuju alamatmu",
    445: "Driver hampir tiba",
    450: "Driver tiba di lokasi",
    460: "Pesanan sampai",
    470: "Pesanan sampai",
    500: "Pesanan sampai",
    550: "Pesanan sampai",
    600: "Pesanan selesai",
    900: "Pesanan selesai",
    999: "Pesanan dibatalkan",
}


def parse_tracker_url(url: str):
    """Extract order_id + code dari URL tracker ShopeeFood."""
    if not url:
        return None, None
    m = re.search(r"orderId=(\d+)", url)
    order_id = m.group(1) if m else None
    m = re.search(r"[?&]code=([^&\s]+)", url)
    code = m.group(1) if m else None
    return order_id, code


def ts_to_str(ts):
    if not ts or ts == "0" or ts == 0:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts) / 1000, tz=WIB)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return "-"


def _pick_coord(*candidates):
    for c in candidates:
        if not c or not isinstance(c, dict):
            continue
        lat = c.get("precise_latitude") or c.get("latitude") or 0
        lng = c.get("precise_longitude") or c.get("longitude") or 0
        try:
            if float(lat) != 0 and float(lng) != 0:
                return float(lat), float(lng)
        except Exception:
            pass
    return 0, 0


async def fetch_tracking(order_id: str, code: str):
    """Hit API ShopeeFood tracking."""
    url = f"{SFOOD_API_BASE}/{order_id}/tracing/{code}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=SFOOD_API_HEADERS)
            if r.status_code != 200:
                print(f"[SFOOD] ⚠️ HTTP {r.status_code} untuk {order_id}")
                return None
            data = r.json()
            if data.get("code") != 0:
                print(f"[SFOOD] ⚠️ API error: {data.get('msg')}")
                return None
            return data.get("data")
    except Exception as e:
        print(f"[SFOOD] ❌ Fetch error: {e}")
        return None


def parse_tracking(data: dict):
    """Extract field penting dari response API ShopeeFood."""
    if not data:
        return None

    order = data.get("order") or {}
    delivery = data.get("union_delivery") or {}
    deliv_order = delivery.get("delivery_order") or {}
    driver = delivery.get("driver") or {}
    store = order.get("store") or {}

    status_code = order.get("status", 0)
    delivery_status = deliv_order.get("delivery_status", 0)
    enroute_status = deliv_order.get("enroute_status", 0)

    status_text = STATUS_MAP.get(status_code)
    if not status_text:
        if enroute_status >= 300:
            status_text = "Pesanan sampai"
        elif enroute_status >= 200:
            status_text = "Driver menuju alamatmu"
        elif enroute_status >= 100:
            status_text = "Driver dalam perjalanan"
        elif delivery_status >= 5:
            status_text = "Pesanan sampai"
        elif delivery_status >= 4:
            status_text = "Driver mengambil pesanan"
        elif delivery_status >= 3:
            status_text = "Driver menuju restoran"
        elif delivery_status >= 1:
            status_text = "Restoran menyiapkan pesananmu"
        elif status_code >= 100:
            status_text = "Restoran konfirmasi pesanan"
        else:
            status_text = f"Status {status_code}"

    eta_obj = order.get("display_estimate_delivered_time")
    eta_text = "-"
    if eta_obj and isinstance(eta_obj, dict):
        eta_min = eta_obj.get("min")
        eta_max = eta_obj.get("max")
        if eta_min and eta_max:
            eta_text = f"{ts_to_str(eta_min).split(' ')[-1]} - {ts_to_str(eta_max).split(' ')[-1]}"

    driver_name = driver.get("full_name") or "-"
    driver_phone = driver.get("phone") or "-"
    driver_plate = driver.get("vehicle_plate_no") or "-"
    driver_rating = driver.get("rating_score") or "-"

    store_name = store.get("name") or "-"

    items = order.get("items", [])
    item_count = sum((it.get("cart_item") or {}).get("quantity", 0) for it in items)

    total_amount = order.get("amount", {}).get("total_amount", "0")
    try:
        total_rp = int(total_amount) // 100000
    except Exception:
        total_rp = 0

    complete_time = deliv_order.get("complete_time", "0")

    complete_certs = deliv_order.get("complete_certs") or []
    pickup_certs = deliv_order.get("pickup_certs") or []
    proof_urls = []
    for cert in complete_certs:
        if isinstance(cert, dict) and cert.get("image_url"):
            proof_urls.append(cert["image_url"])
    if not proof_urls:
        for cert in pickup_certs:
            if isinstance(cert, dict) and cert.get("image_url"):
                proof_urls.append(cert["image_url"])

    is_arriving = (enroute_status == 200 or status_code in (440, 445))
    is_done = (
        (complete_time and str(complete_time) != "0") or
        status_code in (500, 550, 600, 900) or
        delivery_status >= 5 or
        enroute_status >= 300
    )
    is_cancelled = status_code == 999

    geo = deliv_order.get("geo_tracking") or {}
    driver_lat, driver_lng = _pick_coord(
        geo.get("complete_geo"),
        geo.get("pickup_geo"),
        geo.get("arrive_geo"),
        geo.get("accept_geo"),
    )

    store_loc = store.get("location") or {}
    store_lat, store_lng = _pick_coord(store_loc)

    deliv_loc = order.get("delivery_address", {}).get("location") or {}
    deliv_lat, deliv_lng = _pick_coord(deliv_loc)

    return {
        "status_code": status_code,
        "status_text": status_text,
        "eta_text": eta_text,
        "driver_name": driver_name,
        "driver_phone": driver_phone,
        "driver_plate": driver_plate,
        "driver_rating": driver_rating,
        "delivery_status": delivery_status,
        "enroute_status": enroute_status,
        "store_name": store_name,
        "item_count": item_count,
        "total_rp": total_rp,
        "is_arriving": is_arriving,
        "is_done": is_done,
        "is_cancelled": is_cancelled,
        "proof_urls": proof_urls,
        "driver_lat": driver_lat,
        "driver_lng": driver_lng,
        "store_lat": store_lat,
        "store_lng": store_lng,
        "deliv_lat": deliv_lat,
        "deliv_lng": deliv_lng,
        "_source": "shopee",
    }


# ======================================================================
# ROUTING — MULTI SOURCE
# ======================================================================

def is_shptools_url(url: str) -> bool:
    """Cek apakah link dari shptools.my.id."""
    return "shptools.my.id" in (url or "")


async def fetch_tracking_auto(url: str):
    """
    Fetch tracking otomatis dari sumber mana pun.
    Return: (parsed_data, order_id, source_or_code)
    """
    if is_shptools_url(url):
        order_id = parse_shptools_url(url)
        if not order_id:
            return None, None, None

        order_data = await shptools_fetch_order(order_id)
        if not order_data:
            return None, None, None

        driver_data = await shptools_fetch_driver(order_id)
        parsed = parse_shptools_response(order_data, driver_data)
        return parsed, order_id, "shptools"

    else:
        order_id, code = parse_tracker_url(url)
        if not order_id or not code:
            return None, None, None

        data = await fetch_tracking(order_id, code)
        if not data:
            return None, None, None

        parsed = parse_tracking(data)
        return parsed, order_id, code


async def fetch_tracking_by_id(order_id: str, source: str = "shptools"):
    """Fetch tracking by order_id saja (untuk polling)."""
    if source == "shptools":
        order_data = await shptools_fetch_order(order_id)
        if not order_data:
            return None
        driver_data = await shptools_fetch_driver(order_id)
        return parse_shptools_response(order_data, driver_data)
    else:
        return None