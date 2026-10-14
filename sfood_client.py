import httpx
import re
from datetime import datetime

from sfood_config import SFOOD_API_HEADERS, SFOOD_API_BASE


STATUS_MAP = {
    100: "Order baru masuk",
    200: "Restoran konfirmasi pesanan",
    300: "Restoran menyiapkan pesananmu",
    400: "Driver menuju restoran",
    430: "Driver dalam perjalanan",
    440: "Driver menuju alamatmu",
    500: "Driver tiba di lokasi",
    600: "Pesanan selesai",
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
        return datetime.fromtimestamp(int(ts) / 1000).strftime("%d/%m %H:%M")
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

    status_code = order.get("status", 0)
    status_text = STATUS_MAP.get(status_code, f"Status {status_code}")

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

    delivery_status = deliv_order.get("delivery_status", 0)
    enroute_status = deliv_order.get("enroute_status", 0)
    complete_time = deliv_order.get("complete_time", "0")

    store_name = store.get("name") or "-"

    items = order.get("items", [])
    item_count = sum((it.get("cart_item") or {}).get("quantity", 0) for it in items)

    total_amount = order.get("amount", {}).get("total_amount", "0")
    try:
        total_rp = int(total_amount) // 100000
    except Exception:
        total_rp = 0

    is_arriving = (enroute_status == 200 or status_code == 440)
    is_done = (complete_time and complete_time != "0") or status_code == 600
    is_cancelled = status_code == 999

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
    }