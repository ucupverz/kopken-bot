import httpx
import os

WAHUB_BASE_URL = "https://dehuyzotp.shop"
WAHUB_API_KEY = os.getenv("WAHUB_API_KEY", "")

def get_headers():
    return {
        "Authorization": f"Bearer {WAHUB_API_KEY}",
        "Content-Type": "application/json"
    }

async def get_services():
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{WAHUB_BASE_URL}/api/services", headers=get_headers())
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[WAHUB] Error get_services: {e}")
    return []

async def get_balance():
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{WAHUB_BASE_URL}/api/balance", headers=get_headers())
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[WAHUB] Error get_balance: {e}")
    return None

async def rent_number(service_id):
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(f"{WAHUB_BASE_URL}/api/rent", headers=get_headers(), json={"service_id": service_id})
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500

async def check_otp(token, timeout=60):
    async with httpx.AsyncClient(timeout=timeout + 5) as client:
        try:
            r = await client.get(f"{WAHUB_BASE_URL}/api/sms/{token}?timeout={timeout}", headers=get_headers())
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500

async def cancel_order(order_id):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{WAHUB_BASE_URL}/api/order/{order_id}", headers=get_headers(), json={"action": "cancel"})
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500

async def mark_done(order_id):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{WAHUB_BASE_URL}/api/order/{order_id}", headers=get_headers(), json={"action": "done"})
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500

async def retry_otp(token):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{WAHUB_BASE_URL}/api/rent/{token}/retry", headers=get_headers())
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500