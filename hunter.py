import os
import time
import json
import math
import requests
from datetime import datetime

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/book"

# --- Filtreler (senin avcılık modeli) ---
MIN_VOLUME = 20_000
MAX_VOLUME = 150_000
MIN_SPREAD = 0.04          # %4+
MIN_IMBALANCE = 0.55       # orderbook dengesizliği
TOP_LEVELS = 5             # ilk 5 kademe derinlik

# Alarm eşiği: bunu istersen sonra oynarız
ALERT_SCORE = 0.085

# Snapshot: 1 saatlik hareket için
SNAPSHOT_PATH = "snapshot.json"
MIN_MOVE_1H = 0.12         # %12+ hareket yakalarsak daha değerli

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

# Workflow env'leri
SEND_IF_EMPTY = os.environ.get("SEND_IF_EMPTY", "0") == "1"   # boşsa da mesaj at
ALERT_ONLY = os.environ.get("ALERT_ONLY", "1") == "1"         # sadece güçlü avları gönder

def tg_send(msg: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TG_CHAT,
        "text": msg,
        "disable_web_page_preview": True
    }, timeout=25)
    r.raise_for_status()

def get_markets():
    r = requests.get(GAMMA, params={"limit": 250, "active": True, "closed": False}, timeout=25)
    r.raise_for_status()
    return r.json()

def get_book(token_id: str):
    r = requests.get(CLOB, params={"token_id": token_id}, timeout=25)
    r.raise_for_status()
    return r.json()

def sum_depth(side, n=5):
    # side: bids or asks list of {"price":"0.xx","size":"123"}
    total = 0.0
    for lvl in side[:n]:
        try:
            total += float(lvl.get("size", 0))
        except:
            pass
    return total

def safe_float(x, default=0.0):
    try:
        return float(x)
    except:
        return default

def load_snapshot():
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_snapshot(snap):
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)

def compute_entry_target(bid, ask):
    # Avcılık: market emri yok. Spread içinden limit.
    # giriş: bid'e yakın
    entry_lo = bid
    entry_hi = bid + (ask - bid) * 0.25
    # hedef: mid üstü (düzeltme)
    mid = (bid + ask) / 2
    target = mid + (ask - bid) * 0.65
    return entry_lo, entry_hi, target

def score(spread, imbalance, move_1h):
    # spread + orderbook dengesizliği + hareket
    mv = move_1h if move_1h is not None else 0.0
    return spread * 0.55 + (abs(imbalance - 0.5) * 2) * 0.30 + mv * 0.15

def main():
    now = int(time.time())
    snap = load_snapshot()

    markets = get_markets()
    picks = []

    for m in markets:
        vol = safe_float(m.get("volumeNum", m.get("volume", 0.0)), 0.0)
        if not (MIN_VOLUME <= vol <= MAX_VOLUME):
            continue

        # YES token
        yes_token = None
        for t in (m.get("tokens") or []):
            if str(t.get("outcome", "")).upper() == "YES":
                yes_token = t.get("token_id") or t.get("tokenId")
                break
        if not yes_token:
            continue

        try:
            book = get_book(str(yes_token))
            bids = book.get("bids", []) or []
            asks = book.get("asks", []) or []
            if not bids or not asks:
                continue

            bid = safe_float(bids[0]["price"])
            ask = safe_float(asks[0]["price"])
            if ask <= bid or bid <= 0:
                continue

            mid = (bid + ask) / 2
            spread = (ask - bid) / mid
            if spread < MIN_SPREAD:
                continue

            bid_depth = sum_depth(bids, TOP_LEVELS)
            ask_depth = sum_depth(asks, TOP_LEVELS)
            total_depth = bid_depth + ask_depth
            if total_depth <= 0:
                continue

            # imbalance: 0.0~1.0 (1.0 = tamamen bid tarafı güçlü)
            imbalance = bid_depth / total_depth
            if not (imbalance >= MIN_IMBALANCE or imbalance <= (1 - MIN_IMBALANCE)):
                # çok dengeliyse av değil
                continue

            key = str(m.get("id") or m.get("marketId") or m.get("slug") or yes_token)
            prev = snap.get(key, {})
            prev_mid = prev.get("mid")
            prev_ts = prev.get("ts")

            move_1h = None
            if prev_mid and prev_ts and (now - int(prev_ts)) >= 3600:
                try:
                    move_1h = abs(mid - float(prev_mid)) / max(float(prev_mid), 1e-9)
                except:
                    move_1h = None

            # hareket filtresi: istersek sıkılaştırırız; şimdilik puana dahil
            entry_lo, entry_hi, target = compute_entry_target(bid, ask)
            sc = score(spread, imbalance, move_1h)

            picks.append({
                "name": m.get("question") or m.get("title") or m.get("slug") or "market",
                "slug": m.get("slug") or "",
                "vol": vol,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread": spread,
                "imbalance": imbalance,
                "move_1h": move_1h,
                "entry_lo": entry_lo,
                "entry_hi": entry_hi,
                "target": target,
                "score": sc
            })

            # snapshot güncelle
            snap[key] = {"mid": mid, "ts": now}

        except:
            continue

    save_snapshot(snap)

    picks.sort(key=lambda x: x["score"], reverse=True)

    # ALERT modu: sadece güçlü avları gönder
    strong = [p for p in picks if p["score"] >= ALERT_SCORE]

    if ALERT_ONLY:
        send_list = strong[:5]
    else:
        send_list = picks[:5]

    if not send_list:
        if SEND_IF_EMPTY:
            tg_send(f"🧊 Av yok.\nSaat: {datetime.now().strftime('%H:%M')}")
        return

    header = "🚨 AV ALARM" if (ALERT_ONLY and strong) else "🕵️ AV RAPORU"
    msg = f"{header} ({datetime.now().strftime('%H:%M')})\n"
    msg += f"Filtre: vol {MIN_VOLUME}-{MAX_VOLUME}, spread≥{int(MIN_SPREAD*100)}%\n"

    for p in send_list:
        mv = "-" if p["move_1h"] is None else f"{p['move_1h']*100:.1f}%"
        imb = f"{p['imbalance']*100:.0f}% bid" if p["imbalance"] >= 0.5 else f"{(1-p['imbalance'])*100:.0f}% ask"
        msg += "\n" + "—"*28 + "\n"
        msg += f"• {p['name']}\n"
        if p["slug"]:
            msg += f"  slug: {p['slug']}\n"
        msg += f"  vol:{p['vol']:.0f} | spread:{p['spread']*100:.1f}% | move1h:{mv}\n"
        msg += f"  YES bid/ask: {p['bid']:.3f}/{p['ask']:.3f} | depth:{imb}\n"
        msg += f"  🎯 giriş(limit): {p['entry_lo']:.3f}–{p['entry_hi']:.3f}\n"
        msg += f"  ✅ hedef çıkış: {p['target']:.3f}\n"
        msg += f"  📌 skor: {p['score']:.3f}\n"

    tg_send(msg)

if __name__ == "__main__":
    main()
