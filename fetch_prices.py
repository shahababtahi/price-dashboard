#!/usr/bin/env python3
"""
به‌روزرسانی داده‌ها: قیمت ارزهای دیجیتال (بایننس) و طلا (یاهو فایننس) را
در ساعت‌های مشخص می‌گیرد و در data/prices.json ذخیره می‌کند.
فقط ردیف‌های جدید اضافه می‌شوند و داده‌های قبلی دست‌نخورده می‌مانند.
در اولین اجرا، BACKFILL_DAYS روز گذشته هم پر می‌شود.
"""

import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ===================== تنظیمات =====================
TIMEZONE = "Asia/Tehran"
TIMES = ["03:30", "11:30", "19:30"]   # ساعت‌ها به وقت TIMEZONE
BACKFILL_DAYS = 30                    # در اولین اجرا چند روز گذشته پر شود
SAFETY_MINUTES = 30                   # صبر برای کامل شدن داده‌ی ساعت‌های خیلی تازه

CRYPTO = {                            # نام نمایشی -> نماد بایننس
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "TUT": "TUTUSDT",
    "RLC": "RLCUSDT",
}
GOLD_NAME = "GOLD"
GOLD_SYMBOLS = ["XAUUSD=X", "GC=F"]   # به ترتیب امتحان می‌شوند

DATA_FILE = "data/prices.json"
# سرورهای GitHub در آمریکا هستند و api.binance.com از آمریکا بلاک است؛
# پس آدرس data-api.binance.vision اول آمده.
BINANCE_URLS = ["https://data-api.binance.vision", "https://api.binance.com"]
# ===================================================

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})


class FetchError(Exception):
    pass


def get_crypto_price(symbol, dt):
    """قیمت open کندل ۱ دقیقه‌ای که دقیقاً در dt شروع می‌شود. None = داده‌ای نیست."""
    params = {"symbol": symbol, "interval": "1m",
              "startTime": int(dt.timestamp() * 1000), "limit": 1}
    last_err = "unknown"
    for base in BINANCE_URLS:
        try:
            data = session.get(f"{base}/api/v3/klines", params=params, timeout=15).json()
            if isinstance(data, list):
                return float(data[0][1]) if data else None
            if isinstance(data, dict) and data.get("code") == -1121:  # نماد نامعتبر
                return None
            last_err = f"{base}: {data}"
        except (requests.RequestException, ValueError) as e:
            last_err = f"{base}: {type(e).__name__}"
    raise FetchError(last_err)


def load_gold_candles():
    """{timestamp: open} کندل‌های ۵ دقیقه‌ای طلا؛ در صورت شکست None."""
    for symbol in GOLD_SYMBOLS:
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                r = session.get(f"https://{host}/v8/finance/chart/{symbol}",
                                params={"interval": "5m", "range": "60d"}, timeout=20)
                if r.status_code != 200:
                    print(f"⚠️ طلا {symbol} از {host}: کد پاسخ {r.status_code}")
                    continue
                res = r.json()["chart"]["result"][0]
                candles = {ts: o for ts, o in zip(res["timestamp"],
                           res["indicators"]["quote"][0]["open"]) if o is not None}
                if candles:
                    print(f"✅ طلا از {symbol} دریافت شد ({len(candles)} کندل)")
                    return candles
            except Exception as e:
                print(f"⚠️ طلا {symbol} از {host}: {type(e).__name__}: {e}")
    print("❌ قیمت طلا دریافت نشد؛ در اجرای بعدی دوباره تلاش می‌شود.")
    return None


def get_gold_price(candles, dt):
    ts = int(dt.timestamp())
    return candles.get(ts - ts % 300)   # None = بازار بسته


def slot_datetimes(tz, now):
    start = now.date() - timedelta(days=BACKFILL_DAYS - 1)
    for i in range(BACKFILL_DAYS):
        day = start + timedelta(days=i)
        for t in TIMES:
            hh, mm = map(int, t.split(":"))
            dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            if dt <= now - timedelta(minutes=SAFETY_MINUTES):
                yield dt


def load_rows():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            return {r["t"]: r for r in json.load(f).get("rows", [])}
    return {}


def save_rows(rows, now):
    out = {
        "meta": {
            "timezone": TIMEZONE,
            "times": TIMES,
            "assets": list(CRYPTO.keys()) + [GOLD_NAME],
            "updated": now.isoformat(timespec="minutes"),
        },
        "rows": [rows[k] for k in sorted(rows)],
    }
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def main():
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    rows = load_rows()
    slots = list(slot_datetimes(tz, now))
    key = lambda dt: dt.strftime("%Y-%m-%d %H:%M")
    changed = False

    need_gold = any(key(dt) not in rows or not rows[key(dt)].get("gold_ok") for dt in slots)
    gold = load_gold_candles() if need_gold else None

    for dt in slots:
        k = key(dt)
        row = rows.get(k)
        if row is None:
            row = {"t": k}
            try:
                for name, symbol in CRYPTO.items():
                    row[name] = get_crypto_price(symbol, dt)
                    time.sleep(0.05)
            except FetchError as e:
                print(f"⚠️ {k} رد شد (دریافت ارزها ناموفق): {e}")
                continue
            row[GOLD_NAME] = None
            row["gold_ok"] = False
            rows[k] = row
            changed = True
            print(f"➕ {k}")
        if not row.get("gold_ok") and gold:
            row[GOLD_NAME] = get_gold_price(gold, dt)
            row["gold_ok"] = True
            changed = True

    if changed or not os.path.exists(DATA_FILE):
        save_rows(rows, now)
        print(f"ذخیره شد: {len(rows)} ردیف")
    else:
        print("تغییری نبود.")


if __name__ == "__main__":
    main()
