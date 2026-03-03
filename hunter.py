import os
import requests
from datetime import datetime

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/book"

MIN_VOLUME = 20000
MAX_VOLUME = 150000
MIN_SPREAD = 0.04

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TG_CHAT,
        "text": msg,
        "disable_web_page_preview": True
    })

def get_markets():
    r = requests.get(GAMMA, params={"limit": 200, "active": True})
    return r.json()

def get_book(token):
    r = requests.get(CLOB, params={"token_id": token})
    return r.json()

def main():
    markets = get_markets()
    hunts = []

    for m in markets:
        volume = float(m.get("volumeNum", 0))
        if not (MIN_VOLUME <= volume <= MAX_VOLUME):
            continue

        tokens = m.get("tokens", [])
        yes_token = None
        for t in tokens:
            if t.get("outcome") == "YES":
                yes_token = t.get("token_id")
        if not yes_token:
            continue

        try:
            book = get_book(yes_token)
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            if not bids or not asks:
                continue

            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])

            if ask <= bid:
                continue

            mid = (bid + ask) / 2
            spread = (ask - bid) / mid

            if spread >= MIN_SPREAD:
                hunts.append({
                    "name": m.get("question"),
                    "bid": bid,
                    "ask": ask,
                    "spread": spread
                })

        except:
            continue

    message = f"🕵️ Av Raporu ({datetime.now().strftime('%H:%M')})\n\n"

    if not hunts:
        message += "Şu anda net av yok."
    else:
        for h in hunts[:5]:
            message += f"\n• {h['name']}\n  bid:{h['bid']} ask:{h['ask']}\n  spread:%{round(h['spread']*100,2)}\n"

    send_telegram(message)

if __name__ == "__main__":
    main()
