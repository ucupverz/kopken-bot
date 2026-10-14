import httpx
import re
from datetime import datetime, timezone, timedelta

from sfood_config import SFOOD_API_HEADERS, SFOOD_API_BASE


# Timezone WIB (UTC+7)
WIB = timezone(timedelta(hours=7))


# Mapping status kode → deskripsi
STATUS_MAP = {
    # Order phase
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
    500: "Pesanan diterima",
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
    """Convert timestamp (ms) ke string waktu WIB."""
    if not ts or ts == "0" or ts == 0:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts) / 1000, tz=WIB)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return "-"


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
    """Extract field penting dari response API."""
    if not data:
        return None

    order = data.get("order") or {}
    delivery = data.get("union_delivery") or {}
    deliv_order = delivery.get("delivery_order") or {}
    driver = delivery.get("driver") or {}
    store = order.get("store") or {}

    # Extract status
    status_code = order.get("status", 0)
    delivery_status = deliv_order.get("delivery_status", 0)
    enroute_status = deliv_order.get("enroute_status", 0)

    # Cari status text dari mapping
    status_text = STATUS_MAP.get(status_code)

    # Fallback: tebak dari delivery_status / enroute_status
    if not status_text:
        if enroute_status >= 300:
            status_text = "Driver tiba di lokasi"
        elif enroute_status >= 200:
            status_text = "Driver menuju alamatmu"
        elif enroute_status >= 100:
            status_text = "Driver dalam perjalanan"
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

    # Extract ETA
    eta_obj = order.get("display_estimate_delivered_time")
    eta_text = "-"
    if eta_obj and isinstance(eta_obj, dict):
        eta_min = eta_obj.get("min")
        eta_max = eta_obj.get("max")
        if eta_min and eta_max:
            eta_text = f"{ts_to_str(eta_min).split(' ')[-1]} - {ts_to_str(eta_max).split(' ')[-1]}"

    # Extract driver
    driver_name = driver.get("full_name") or "-"
    driver_phone = driver.get("phone") or "-"
    driver_plate = driver.get("vehicle_plate_no") or "-"
    driver_rating = driver.get("rating_score") or "-"

    # Extract store
    store_name = store.get("name") or "-"

    # Extract items
    items = order.get("items", [])
    item_count = sum((it.get("cart_item") or {}).get("quantity", 0) for it in items)

    # Extract total
    total_amount = order.get("amount", {}).get("total_amount", "0")
    try:
        total_rp = int(total_amount) // 100000
    except Exception:
        total_rp = 0

    # Complete time
    complete_time = deliv_order.get("complete_time", "0")

    # Flags
    is_arriving = (enroute_status == 200 or status_code == 440 or status_code == 445)
    is_done = (complete_time and complete_time != "0") or status_code in (600, 900)
    is_cancelled = status_code == 999

    # === EXTRACT KOORDINAT ===
    geo = deliv_order.get("geo_tracking") or {}
    driver_geo = geo.get("complete_geo") or geo.get("pickup_geo") or geo.get("arrive_geo") or {}
    driver_lat = driver_geo.get("precise_latitude") or driver_geo.get("latitude") or 0
    driver_lng = driver_geo.get("precise_longitude") or driver_geo.get("longitude") or 0

    store_loc = store.get("location") or {}
    store_lat = store_loc.get("precise_latitude") or store_loc.get("latitude") or 0
    store_lng = store_loc.get("precise_longitude") or store_loc.get("longitude") or 0

    deliv_loc = order.get("delivery_address", {}).get("location") or {}
    deliv_lat = deliv_loc.get("precise_latitude") or deliv_loc.get("latitude") or 0
    deliv_lng = deliv_loc.get("precise_longitude") or deliv_loc.get("longitude") or 0

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
        # Koordinat
        "driver_lat": driver_lat,
        "driver_lng": driver_lng,
        "store_lat": store_lat,
        "store_lng": store_lng,
        "deliv_lat": deliv_lat,
        "deliv_lng": deliv_lng,
    }