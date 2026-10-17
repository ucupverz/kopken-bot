import httpx
import os
import asyncio

WAHUB_BASE_URL = "https://dehuyzotp.shop"
WAHUB_API_KEY = os.getenv("WAHUB_API_KEY", "")

def get_headers():
    return {
        "Authorization": f"Bearer {WAHUB_API_KEY}",
        "Content-Type": "application/json"
    }

async def get_services():
    # Timeout 15 detik biar ga nge-hang
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
    # Timeout 20 detik. Kalau lebih dari 20 detik, anggap gagal.
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(f"{WAHUB_BASE_URL}/api/rent", headers=get_headers(), json={"service_id": service_id})
            
            # Cek apakah respon sukses dan ada datanya
            if r.status_code == 200:
                data = r.json()
                # Validasi apakah API ngasih token/nomor
                if data.get('token') and data.get('phone'):
                    return data, 200
                else:
                    # API ngasih 200 tapi datanya kosong / error internal
                    print(f"[WAHUB] Rent response missing token/phone: {data}")
                    return {"error": "Server provider sedang sibuk, nomor gagal dibuat."}, 500
            else:
                return r.json(), r.status_code
                
        except httpx.TimeoutException:
            print("[WAHUB] Rent timeout setelah 20 detik")
            return {"error": "Server provider timeout (20s). Silakan coba lagi."}, 408
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

        async def get_order_status(order_id):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{WAHUB_BASE_URL}/api/order/{order_id}", headers=get_headers())
            return r.json(), r.status_code
        except Exception as e:
            return {"error": str(e)}, 500