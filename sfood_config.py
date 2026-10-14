# =========================================================
# KONFIGURASI SHOPEEFOOD TRACKER
# =========================================================

# Interval polling (detik)
SFOOD_POLL_INTERVAL = 60

# Header untuk API ShopeeFood
SFOOD_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.shopeefood.co.id",
    "Referer": "https://www.shopeefood.co.id/",
    "sec-ch-ua": '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}

SFOOD_API_BASE = "https://foody.shopee.co.id/api/buyer/orders"