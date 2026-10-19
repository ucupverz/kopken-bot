import httpx
import os
import time

WAHUB_BASE_URL = "https://dehuyzotp.shop"
WAHUB_API_KEY = os.getenv("WAHUB_API_KEY", "")

_cache = {"ts": 0, "data": []}
_CACHE_TTL = 30

def get_headers():
    return {
        "Authorization": f"Bearer {WAHUB_API_KEY}",
        "Content-Type": "application/json"
    }

async def get_services(force=False):
    now = time.time()
    if not force and _cache["data"] and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{WAHUB_BASE_URL}/api/services", headers=get_headers())
            if r.status_code == 200:
                data = r.json()
                _cache["data"] = data
                _cache["ts"] = now
                return data
        except Exception as e:
            print(f"[WAHUB] get_services error: {e}")
    return _cache["data"]

async def get_balance():
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{WAHUB_BASE_URL}/api/balance", headers=get_headers())
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[WAHUB] get_balance error: {e}")
    return None

async def rent_number(service_id):
    async with httpx.AsyncClient(timeout=35.0) as client:
        try:
            r = await client.post(
                f"{WAHUB_BASE_URL}/api/rent",
                headers=get_headers(),
                json={"service_id": service_id}
            )
            return r.json(), r.status_code
        except httpx.TimeoutException:
            return {"error": "Server provider timeout (35s)."}, 408
        except Exception as e:
            return {"error": str(e)}, 500

async def check_otp(token, timeout=15):
    async with httpx.AsyncClient(timeout=timeout + 5) as client:
        try:
            r = await client.get(
                f"{WAHUB_BASE_URL}/api/sms/{token}?timeout={timeout}",
                headers=get_headers()
            )
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500

async def cancel_order(order_id):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{WAHUB_BASE_URL}/api/order/{order_id}",
                headers=get_headers(),
                json={"action": "cancel"}
            )
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500

async def mark_done(order_id):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{WAHUB_BASE_URL}/api/order/{order_id}",
                headers=get_headers(),
                json={"action": "done"}
            )
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500

async def retry_otp(token):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{WAHUB_BASE_URL}/api/rent/{token}/retry", headers=get_headers()
            )
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500