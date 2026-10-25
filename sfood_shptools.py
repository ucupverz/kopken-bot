"""
Wrapper API shptools.my.id untuk ShopeeFood order tracking.
Fitur ini NAMBAH support link shptools, tidak mengubah fitur existing.
"""

import httpx
import os
import re
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

SHPTOOLS_BASE = "https://shptools.my.id/api/sfood"
WEB_AUTH_COOKIE = os.getenv("SHPTOOLS_WEB_AUTH", "")
SHPTOOLS_USERID = os.getenv("SHPTOOLS_USERID", "")

# True kalau request terakhir ke shptools ditolak karena auth (cookie web_auth habis/salah).
AUTH_FAILED = False
# Alasan kegagalan request shptools terakhir (untuk diagnosa).
LAST_ERROR = ""


STATUS_MAP = {
    100: "Order baru masuk",
    110: "Menunggu konfirmasi restoran",
    200: "Restoran konfirmasi pesanan",
    210: "Restoran menerima pesanan",
    300: "Restoran menyiapkan pesananmu",
    310: "Restoran menyiapkan pesananmu",
    400: "Mencari kurir",
    410: "Restoran menyiapkan pesananmu",
    420: "Kurir menuju restoran",
    425: "Kurir menuju restoran",
    430: "Kurir dalam perjalanan",
    435: "Kurir mengambil pesanan",
    440: "Kurir menuju alamatmu",
    445: "Kurir hampir tiba",
    450: "Kurir tiba di lokasi",
    460: "Pesanan sampai",
    470: "Pesanan sampai",
    500: "Pesanan sampai",
    550: "Pesanan sampai",
    600: "Pesanan selesai",
    900: "Pesanan selesai",
    999: "Pesanan dibatalkan",
}


def ts_ms_to_hhmm(ts_ms):
    if not ts_ms or ts_ms == 0:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=WIB)
        return dt.strftime("%H:%M")
    except Exception:
        return "-"


def parse_shptools_url(url: str):
    """Extract order_id dari URL shptools."""
    if not url:
        return None
    m = re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


def _get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://shptools.my.id",
        "Referer": "https://shptools.my.id/sfood",
        "Cookie": f"web_auth={WEB_AUTH_COOKIE}",
        "x-requested-with": "XMLHttpRequest",
        "x-web-menu": "sfood",
    }


async def fetch_order_detail(order_id: str):
    """Hit shptools API untuk detail order."""
    return await _post_api("order-detail", {"id": order_id}) or None


async def fetch_driver_location(order_id: str):
    """Hit shptools API untuk posisi driver."""
    url = f"{SHPTOOLS_BASE}/driver-location"
    payload = {"id": order_id, "userid": SHPTOOLS_USERID}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, headers=_get_headers(), json=payload)
            if r.status_code != 200:
                print(f"[SHPTOOLS] ⚠️ Driver location HTTP {r.status_code}")
                return None
            data = r.json()
            if not data.get("success"):
                return None
            return data.get("data")
    except Exception as e:
        print(f"[SHPTOOLS] ❌ Driver location error: {e}")
        return None


def parse_shptools_response(order_data: dict, driver_data: dict = None):
    """Convert response shptools ke format internal."""
    if not order_data:
        return None

    status_code = order_data.get("status", 0)
    status_text = order_data.get("statusText") or STATUS_MAP.get(status_code, f"Status {status_code}")

    driver = order_data.get("driver") or {}
    driver_name = driver.get("name") or "-"
    driver_phone = driver.get("phone") or "-"
    driver_plate = driver.get("plate") or "-"
    driver_rating = driver.get("rating") or "-"
    driver_id = str(driver.get("id") or "")

    eta_min_ms = order_data.get("etaMinMs", 0)
    eta_max_ms = order_data.get("etaMaxMs", 0)
    if eta_min_ms and eta_max_ms:
        eta_text = f"{ts_ms_to_hhmm(eta_min_ms)} - {ts_ms_to_hhmm(eta_max_ms)}"
    else:
        eta_text = "-"

    store_name = order_data.get("storeName", "-")
    store = order_data.get("store") or {}
    address = order_data.get("address") or {}

    items = order_data.get("items") or []
    item_count = sum(it.get("qty", 0) for it in items)

    amount = order_data.get("amount") or {}
    total_rp = amount.get("total", 0)

    is_cancelled = order_data.get("cancelled", False) or status_code == 999
    is_done = status_code in (600, 900) or not order_data.get("active", True)
    is_arriving = status_code in (440, 445)

    # Driver koordinat
    driver_lat, driver_lng = 0, 0
    if driver_data:
        driver_lat = driver_data.get("latitude", 0)
        driver_lng = driver_data.get("longitude", 0)

    # Store & delivery koordinat (kalau ada di response)
    store_lat = store.get("latitude", 0) or 0
    store_lng = store.get("longitude", 0) or 0
    deliv_lat = address.get("latitude", 0) or 0
    deliv_lng = address.get("longitude", 0) or 0

    return {
        "status_code": status_code,
        "status_text": status_text,
        "eta_text": eta_text,
        "driver_name": driver_name,
        "driver_phone": driver_phone,
        "driver_plate": driver_plate,
        "driver_rating": driver_rating,
        "driver_id": driver_id,
        "delivery_status": 0,
        "enroute_status": 0,
        "store_name": store_name,
        "item_count": item_count,
        "total_rp": total_rp,
        "is_arriving": is_arriving,
        "is_done": is_done,
        "is_cancelled": is_cancelled,
        "proof_urls": [],
        "driver_lat": driver_lat,
        "driver_lng": driver_lng,
        "store_lat": store_lat,
        "store_lng": store_lng,
        "deliv_lat": deliv_lat,
        "deliv_lng": deliv_lng,
        "_source": "shptools",
    }

# ======================================================================
# CHAT DENGAN KURIR
# ======================================================================

_chat_opened = set()


async def _post_api(endpoint: str, payload: dict):
    """POST generik ke shptools. Return field `data` kalau sukses, None kalau gagal.
    Alasan gagal disimpan di LAST_ERROR."""
    global AUTH_FAILED, LAST_ERROR
    if not WEB_AUTH_COOKIE:
        LAST_ERROR = "env SHPTOOLS_WEB_AUTH kosong"
        print(f"[SHPTOOLS] ⚠️ {LAST_ERROR}")
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{SHPTOOLS_BASE}/{endpoint}", headers=_get_headers(), json=payload)
            snippet = (r.text or "")[:150].replace("\n", " ")
            if r.status_code in (401, 403):
                AUTH_FAILED = True
                LAST_ERROR = f"{endpoint}: auth ditolak HTTP {r.status_code} (cookie habis/salah)"
                print(f"[SHPTOOLS] ⚠️ {LAST_ERROR} {snippet}")
                return None
            if r.status_code != 200:
                LAST_ERROR = f"{endpoint}: HTTP {r.status_code} {snippet}"
                print(f"[SHPTOOLS] ⚠️ {LAST_ERROR}")
                return None
            AUTH_FAILED = False
            data = r.json()
            if not data.get("success"):
                LAST_ERROR = f"{endpoint}: success=false {snippet}"
                print(f"[SHPTOOLS] ⚠️ {LAST_ERROR}")
                return None
            LAST_ERROR = ""
            return data.get("data") if data.get("data") is not None else {}
    except Exception as e:
        LAST_ERROR = f"{endpoint}: {type(e).__name__}: {e}"
        print(f"[SHPTOOLS] ❌ {LAST_ERROR}")
        return None


async def chat_open(order_id: str, driver_id: str):
    """Buka sesi chat (sekali per order per proses)."""
    if order_id in _chat_opened:
        return True
    if not driver_id:
        return False
    data = await _post_api("chat-open", {"orderId": str(order_id), "driverId": str(driver_id)})
    if data is None:
        return False
    _chat_opened.add(order_id)
    return True


async def chat_fetch(order_id: str, after_id: str = "0"):
    """Ambil pesan chat setelah after_id. Return list pesan atau None kalau gagal."""
    data = await _post_api("chat-messages", {"orderId": str(order_id), "afterId": str(after_id), "wait": False})
    if data is None:
        return None
    return data.get("messages") or []


async def chat_send(order_id: str, text: str):
    """Kirim pesan ke kurir. Otomatis chat-open dulu. Return True kalau sukses."""
    if order_id not in _chat_opened:
        detail = await fetch_order_detail(order_id)
        driver_id = str(((detail or {}).get("driver") or {}).get("id") or "")
        if detail and not driver_id:
            global LAST_ERROR
            LAST_ERROR = "kurir belum ter-assign (driver.id kosong)"
            return False
        if not await chat_open(order_id, driver_id):
            return False
    data = await _post_api("chat-send", {"orderId": str(order_id), "text": text})
    return data is not None
