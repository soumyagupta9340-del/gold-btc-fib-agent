"""
GOLD & BTC - Fibonacci Sweep Strategy Agent
Telegram Bot: @Aigoldbitcoin_bot
Strategy: 38.2% Fib Liquidity Sweep + EMA Confirmation
Features: News Alerts | TP/SL Tracking | Daily Bias Report | High Alert Zone
"""

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import threading
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for server
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
import io

# CONFIG - Fill these in Railway Environment
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "your_bot_token_here")
TELEGRAM_CHANNEL   = os.environ.get("TELEGRAM_CHANNEL", "8867873147")
TWELVE_DATA_KEY    = os.environ.get("TWELVE_DATA_API_KEY", "your_twelvedata_key_here")
GOOGLE_SHEETS_URL  = os.environ.get("GOOGLE_SHEETS_URL", "https://script.google.com/macros/s/AKfycbwHL0wYdyUvh_eDRtJzgEn5BvZbOFWiDEUF_33TdsI7K7fWSxPnuzlVpLW00F6LJaDc/exec")

IST = pytz.timezone("Asia/Kolkata")

ASSETS = {
    "XAUUSD": {"symbol": "XAU/USD", "name": "GOLD", "emoji": "\U0001F947", "min_sl": 3, "max_sl": 8, "interval": "1min"},
    "BTCUSDT": {"symbol": "BTC/USD", "name": "BITCOIN", "emoji": "\u20BF", "min_sl": 50, "max_sl": 200, "interval": "1min"},
}

SESSIONS = [
    {"name": "Morning", "start": (9, 0), "end": (13, 0)},
    {"name": "Evening", "start": (17, 30), "end": (21, 30)},
]

RANGE_START_HOUR = 0
RANGE_END_HOUR = 9

FIB_LEVEL = 0.382
EMA_FAST = 9
EMA_SLOW = 20
ENTRY_WINDOW_MIN = 60
SCAN_INTERVAL_S = 15  # Scan every 15 seconds for fast signal delivery

NEWS_KEYWORDS = [
    "NFP", "CPI", "Fed", "FOMC", "Interest Rate",
    "GDP", "PPI", "Unemployment", "Powell", "ECB",
    "Non-Farm", "Retail Sales", "PCE"
]

_alerted_news_ids = set()
_bias_report_sent_date = None

# Trade journal - stores completed trades for performance tracking
# Each entry: {"asset", "direction", "entry", "result", "r_multiple", "time"}
_trade_journal = []
_journal_report_sent_date = None


def send_telegram(message, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL, "text": message, "parse_mode": "HTML"}
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=8)
            r.raise_for_status()
            print(f"[TG SENT] {message[:60]}...")
            return
        except Exception as e:
            print(f"[TG ERROR] Attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2)
    print(f"[TG FAILED] Could not send after {retries} attempts")


def send_telegram_photo(image_bytes, caption, retries=3):
    """Sends a chart image to Telegram with caption."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    for attempt in range(1, retries + 1):
        try:
            image_bytes.seek(0)
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHANNEL, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("chart.png", image_bytes, "image/png")},
                timeout=15,
            )
            r.raise_for_status()
            print(f"[TG PHOTO SENT] Chart delivered successfully")
            return True
        except Exception as e:
            print(f"[TG PHOTO ERROR] Attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2)
    return False


def generate_signal_chart(df, signal, sweep, bias, asset_key):
    """
    Generates a clean TradingView-style candlestick chart with:
    - Last 60 candles
    - 9 EMA & 20 EMA
    - Fib 38.2% level
    - Entry, SL, TP1, TP2 horizontal lines
    - Sweep candle highlighted
    Returns image as BytesIO buffer.
    """
    asset = ASSETS[asset_key]

    # Get last 60 candles for chart
    df = df.tail(60).copy().reset_index(drop=True)

    # Calculate EMAs
    df["ema9"]  = df["close"].ewm(span=9,  adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    # Draw candlesticks
    for i, row in df.iterrows():
        color = "#26a69a" if row["close"] >= row["open"] else "#ef5350"
        # Wick
        ax.plot([i, i], [row["low"], row["high"]], color=color, linewidth=0.8)
        # Body
        body_bottom = min(row["open"], row["close"])
        body_height = abs(row["close"] - row["open"])
        rect = plt.Rectangle((i - 0.3, body_bottom), 0.6, body_height,
                               color=color, zorder=3)
        ax.add_patch(rect)

    x_range = range(len(df))

    # EMA lines
    ax.plot(x_range, df["ema9"],  color="#f5a623", linewidth=1.2, label="EMA 9",  zorder=4)
    ax.plot(x_range, df["ema20"], color="#4fc3f7", linewidth=1.2, label="EMA 20", zorder=4)

    # Fib 38.2% level
    fib_lvl = sweep["fib_level"]
    ax.axhline(y=fib_lvl, color="#9c27b0", linewidth=1,
               linestyle="--", alpha=0.8, label=f"Fib 38.2% {fib_lvl}", zorder=4)

    # Key levels
    levels = [
        (signal["sl"],    "#ef5350", "SL",    "--"),
        (signal["entry"], "#ffffff", "ENTRY", "-"),
        (signal["tp1"],   "#66bb6a", "TP1",   "-."),
        (signal["tp2"],   "#26a69a", "TP2",   "-."),
    ]
    for price, color, label, ls in levels:
        ax.axhline(y=price, color=color, linewidth=1.3,
                   linestyle=ls, alpha=0.9, zorder=5)
        ax.text(len(df) - 0.5, price, f" {label}: {price}",
                color=color, fontsize=8, va="center",
                fontweight="bold", zorder=6)

    # Highlight sweep candle (find it in df)
    sweep_time = sweep["time"]
    sweep_matches = df[df.index == df.index[-1]]  # fallback
    for i, row in df.iterrows():
        if abs((row.get("datetime", sweep_time) - sweep_time).total_seconds()
               if hasattr(row.get("datetime", None), "total_seconds") else 999) < 120:
            ax.axvspan(i - 0.5, i + 0.5, color="#f5a623", alpha=0.15, zorder=2)
            break

    # Direction arrow
    arrow_x = len(df) * 0.15
    if bias == "BUY":
        ax.annotate("", xy=(arrow_x, signal["tp1"]),
                    xytext=(arrow_x, signal["sl"]),
                    arrowprops=dict(arrowstyle="->", color="#26a69a", lw=2))
    else:
        ax.annotate("", xy=(arrow_x, signal["tp1"]),
                    xytext=(arrow_x, signal["sl"]),
                    arrowprops=dict(arrowstyle="->", color="#ef5350", lw=2))

    # Labels & styling
    direction_symbol = "â–² BUY" if bias == "BUY" else "â–¼ SELL"
    ax.set_title(
        f"{asset['emoji']} {asset['name']}  |  {direction_symbol}  |  "
        f"Fib 38.2% Sweep  |  {signal['candle_time'].strftime('%d %b %Y  %H:%M IST')}",
        color="white", fontsize=11, fontweight="bold", pad=12
    )
    ax.tick_params(colors="gray", labelsize=7)
    ax.spines["bottom"].set_color("#2d2d2d")
    ax.spines["top"].set_color("#2d2d2d")
    ax.spines["left"].set_color("#2d2d2d")
    ax.spines["right"].set_color("#2d2d2d")
    ax.yaxis.tick_right()
    ax.set_xlim(-1, len(df) + 8)

    # Legend
    legend = ax.legend(loc="upper left", fontsize=7,
                       facecolor="#1a1a2e", edgecolor="#2d2d2d",
                       labelcolor="white", framealpha=0.8)

    # Watermark
    ax.text(0.5, 0.5, "@Aigoldbitcoin_bot",
            transform=ax.transAxes, fontsize=18,
            color="white", alpha=0.04,
            ha="center", va="center",
            fontweight="bold", rotation=20)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _normalize_df(df):
    """Normalize dataframe columns and localize datetime to IST."""
    df = df.sort_values("datetime").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if df["datetime"].dt.tz is None:
        df["datetime"] = df["datetime"].dt.tz_localize(IST)
    else:
        df["datetime"] = df["datetime"].dt.tz_convert(IST)
    return df


# â”€â”€ Gold cache (5 min) to keep Twelve Data within 800 calls/day â”€â”€
_gold_cache = {"df": None, "fetched_at": None}
GOLD_CACHE_SECONDS = 180  # 3 minutes â€” balances API limit vs sweep detection accuracy


def fetch_candles_gold(outputsize=500):
    """
    Fetch Gold (XAU/USD) 1-min candles from Twelve Data.
    Uses 5-minute cache to stay within 800 free calls/day.
    Calculation: 24hr / 5min = 288 fetches/day â€” well within limit.
    """
    global _gold_cache
    now_utc = datetime.now(pytz.utc)

    # Return cached data if still fresh
    if (
        _gold_cache["df"] is not None
        and _gold_cache["fetched_at"] is not None
        and (now_utc - _gold_cache["fetched_at"]).total_seconds() < GOLD_CACHE_SECONDS
    ):
        age = int((now_utc - _gold_cache["fetched_at"]).total_seconds())
        print(f"[GOLD CACHE] Returning cached data ({age}s old)")
        return _gold_cache["df"]

    # Fetch fresh data from Twelve Data
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol"    : "XAU/USD",
            "interval"  : "1min",
            "outputsize": outputsize,
            "apikey"    : TWELVE_DATA_KEY,
            "timezone"  : "Asia/Kolkata",
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()

        if "values" not in data:
            msg = data.get("message", "Unknown error")
            print(f"[GOLD FETCH ERROR] Twelve Data: {msg}")
            # Return stale cache if available rather than empty
            return _gold_cache["df"] if _gold_cache["df"] is not None else pd.DataFrame()

        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = _normalize_df(df)

        _gold_cache["df"] = df
        _gold_cache["fetched_at"] = now_utc
        print(f"[GOLD FETCH] Fresh data from Twelve Data ({len(df)} candles)")
        return df

    except Exception as e:
        print(f"[GOLD FETCH ERROR] {e}")
        return _gold_cache["df"] if _gold_cache["df"] is not None else pd.DataFrame()


def fetch_candles_btc(outputsize=500):
    """
    Fetch BTC/USDT 1-min candles from Binance API.
    Free, no API key, unlimited calls.
    """
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": "BTCUSDT", "interval": "1m", "limit": outputsize}
        r = requests.get(url, params=params, timeout=15)
        data = r.json()

        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "num_trades", "tbbav", "tbqav", "ignore"
        ])
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df[["datetime", "open", "high", "low", "close", "volume"]]
        df = df.dropna(subset=["close"])
        return _normalize_df(df)
    except Exception as e:
        print(f"[BTC FETCH ERROR] Binance: {e}")
        return pd.DataFrame()


def fetch_candles(symbol, interval="1min", outputsize=500):
    """Router â€” Twelve Data (cached) for Gold, Binance for BTC."""
    if "BTC" in symbol.upper():
        return fetch_candles_btc(outputsize)
    else:
        return fetch_candles_gold(outputsize)


def fetch_latest_price(symbol):
    """
    Gets latest REAL-TIME price â€” never uses cache.
    Gold: Twelve Data /price (accurate forex spot price)
    BTC:  Binance ticker (free, unlimited, real-time)
    """
    if "BTC" in symbol.upper():
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            r = requests.get(url, params={"symbol": "BTCUSDT"}, timeout=8)
            return float(r.json()["price"])
        except Exception as e:
            print(f"[BTC PRICE ERROR] {e}")
            return None
    else:
        # Gold â€” always real-time from Twelve Data /price (1 API call only)
        try:
            url = "https://api.twelvedata.com/price"
            params = {"symbol": "XAU/USD", "apikey": TWELVE_DATA_KEY}
            r = requests.get(url, params=params, timeout=8)
            data = r.json()
            if "price" in data:
                price = float(data["price"])
                print(f"[GOLD PRICE] Real-time: ${price}")
                return price
            else:
                # Fallback to latest candle close from cache
                if _gold_cache["df"] is not None:
                    return float(_gold_cache["df"].iloc[-1]["close"])
        except Exception as e:
            print(f"[GOLD PRICE ERROR] {e}")
            # Fallback to cache if API fails
            if _gold_cache["df"] is not None:
                return float(_gold_cache["df"].iloc[-1]["close"])
        return None


def get_trend_bias(df):
    now = datetime.now(IST)
    today = now.date()
    range_start = IST.localize(datetime(today.year, today.month, today.day, RANGE_START_HOUR, 0))
    range_end = IST.localize(datetime(today.year, today.month, today.day, RANGE_END_HOUR, 0))
    range_df = df[(df["datetime"] >= range_start) & (df["datetime"] <= range_end)]
    if range_df.empty:
        return "NEUTRAL"
    open_price = range_df.iloc[0]["open"]
    close_price = range_df.iloc[-1]["close"]
    if close_price > open_price:
        return "BUY"
    elif close_price < open_price:
        return "SELL"
    return "NEUTRAL"


def get_range_high_low(df):
    now = datetime.now(IST)
    today = now.date()
    range_start = IST.localize(datetime(today.year, today.month, today.day, RANGE_START_HOUR, 0))
    range_end = IST.localize(datetime(today.year, today.month, today.day, RANGE_END_HOUR, 0))
    range_df = df[(df["datetime"] >= range_start) & (df["datetime"] <= range_end)]
    if range_df.empty:
        return None
    return (range_df.iloc[0]["open"], range_df["high"].max(), range_df["low"].min(), range_df.iloc[-1]["close"])


def get_fib_level(df, bias):
    now = datetime.now(IST)
    today = now.date()
    range_start = IST.localize(datetime(today.year, today.month, today.day, RANGE_START_HOUR, 0))
    fib_df = df[df["datetime"] >= range_start]
    if fib_df.empty:
        return None
    high = fib_df["high"].max()
    low = fib_df["low"].min()
    if bias == "BUY":
        fib_382 = high - (high - low) * FIB_LEVEL
    elif bias == "SELL":
        fib_382 = low + (high - low) * FIB_LEVEL
    else:
        return None
    return round(fib_382, 4)


def calculate_ema(df):
    df[f"ema{EMA_FAST}"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df[f"ema{EMA_SLOW}"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return df


def is_full_body_candle(candle, bias):
    body = abs(candle["close"] - candle["open"])
    total = candle["high"] - candle["low"]
    if total == 0:
        return False
    body_pct = body / total
    if body_pct < 0.70:
        return False
    if bias == "BUY" and candle["close"] > candle["open"]:
        return True
    if bias == "SELL" and candle["close"] < candle["open"]:
        return True
    return False


def detect_sweep(df, fib_level, bias):
    now = datetime.now(IST)
    lookback = now - timedelta(minutes=ENTRY_WINDOW_MIN)
    recent_df = df[df["datetime"] >= lookback].copy()
    for _, candle in recent_df.iterrows():
        if bias == "BUY":
            if candle["low"] <= fib_level and candle["close"] > fib_level:
                return {"time": candle["datetime"], "fib_level": fib_level, "candle": candle}
        elif bias == "SELL":
            if candle["high"] >= fib_level and candle["close"] < fib_level:
                return {"time": candle["datetime"], "fib_level": fib_level, "candle": candle}
    return None


def detect_entry(df, sweep, bias, min_sl, max_sl):
    df = calculate_ema(df)
    sweep_time = sweep["time"]
    window_end = sweep_time + timedelta(minutes=ENTRY_WINDOW_MIN)
    now = datetime.now(IST)
    entry_df = df[(df["datetime"] > sweep_time) & (df["datetime"] <= min(window_end, now))].copy()

    # Get Fib range (High and Low used for Fib calculation)
    today = now.date()
    range_start = IST.localize(datetime(today.year, today.month, today.day, RANGE_START_HOUR, 0))
    fib_df = df[df["datetime"] >= range_start]
    fib_high = round(fib_df["high"].max(), 4) if not fib_df.empty else 0
    fib_low  = round(fib_df["low"].min(), 4) if not fib_df.empty else 0

    for _, candle in entry_df.iterrows():
        ema20 = candle[f"ema{EMA_SLOW}"]

        if bias == "BUY" and candle["close"] > ema20:
            if is_full_body_candle(candle, "BUY"):
                sl    = candle["low"]
                entry = candle["close"]
                sl_distance = entry - sl

                if sl_distance < min_sl:
                    print(f"[SKIP] SL too tight: ${sl_distance:.2f} < ${min_sl}")
                    return None
                if sl_distance > max_sl:
                    print(f"[SKIP] SL too wide: ${sl_distance:.2f} > ${max_sl}")
                    return None

                tp1 = entry + (sl_distance * 2)
                tp2 = entry + (sl_distance * 3.5)
                return {
                    "direction": "BUY", "entry": round(entry, 4), "sl": round(sl, 4),
                    "tp1": round(tp1, 4), "tp2": round(tp2, 4),
                    "sl_dist": round(sl_distance, 4),
                    "rr1": "1:2", "rr2": "1:3.5",
                    "candle_time": candle["datetime"],
                    "fib_high": fib_high, "fib_low": fib_low,
                }

        elif bias == "SELL" and candle["close"] < ema20:
            if is_full_body_candle(candle, "SELL"):
                sl    = candle["high"]
                entry = candle["close"]
                sl_distance = sl - entry

                if sl_distance < min_sl:
                    print(f"[SKIP] SL too tight: ${sl_distance:.2f} < ${min_sl}")
                    return None
                if sl_distance > max_sl:
                    print(f"[SKIP] SL too wide: ${sl_distance:.2f} > ${max_sl}")
                    return None

                tp1 = entry - (sl_distance * 2)
                tp2 = entry - (sl_distance * 3.5)
                return {
                    "direction": "SELL", "entry": round(entry, 4), "sl": round(sl, 4),
                    "tp1": round(tp1, 4), "tp2": round(tp2, 4),
                    "sl_dist": round(sl_distance, 4),
                    "rr1": "1:2", "rr2": "1:3.5",
                    "candle_time": candle["datetime"],
                    "fib_high": fib_high, "fib_low": fib_low,
                }

    return None


_news_cache = {"data": None, "fetched_at": None}


def _fetch_news_calendar():
    """
    Fetches the ForexFactory weekly calendar with proper headers.
    Caches result for 10 minutes to avoid hammering the API and
    reduce chances of being rate-limited / blocked.
    """
    now = datetime.now(pytz.utc)
    if _news_cache["data"] is not None and _news_cache["fetched_at"] is not None:
        age = (now - _news_cache["fetched_at"]).total_seconds()
        if age < 600:  # 10 minute cache
            return _news_cache["data"]

    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"[NEWS CHECK ERROR] HTTP {r.status_code} from ForexFactory")
            return _news_cache["data"] or []

        content_type = r.headers.get("Content-Type", "")
        if "json" not in content_type and not r.text.strip().startswith(("[", "{")):
            print("[NEWS CHECK ERROR] Non-JSON response received, skipping this cycle")
            return _news_cache["data"] or []

        events = r.json()
        _news_cache["data"] = events
        _news_cache["fetched_at"] = now
        return events
    except requests.exceptions.JSONDecodeError:
        print("[NEWS CHECK ERROR] Invalid JSON response, skipping this cycle")
        return _news_cache["data"] or []
    except Exception as e:
        print(f"[NEWS CHECK ERROR] {e}")
        return _news_cache["data"] or []


def check_news_events():
    events_found = []
    events = _fetch_news_calendar()
    if not events:
        return events_found

    now = datetime.now(pytz.utc)
    for event in events:
        if event.get("impact") != "High":
            continue
        title = event.get("title", "")
        if not any(kw.lower() in title.lower() for kw in NEWS_KEYWORDS):
            continue
        try:
            event_time = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            diff_min = (event_time - now).total_seconds() / 60
            event_id = f"{title}_{event['date']}"
            if 0 <= diff_min <= 60 and event_id not in _alerted_news_ids:
                events_found.append({
                    "id": event_id, "title": title,
                    "time_ist": event_time.astimezone(IST),
                    "minutes_away": round(diff_min),
                    "currency": event.get("country", ""),
                })
        except Exception:
            continue
    return events_found


def is_high_impact_news_now():
    events = _fetch_news_calendar()
    if not events:
        return False

    now = datetime.now(pytz.utc)
    for event in events:
        if event.get("impact") != "High":
            continue
        title = event.get("title", "")
        if not any(kw.lower() in title.lower() for kw in NEWS_KEYWORDS):
            continue
        try:
            event_time = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            diff = abs((event_time - now).total_seconds() / 60)
            if diff <= 30:
                print(f"[NEWS SKIP] High impact event in {diff:.0f} min: {title}")
                return True
        except Exception:
            continue
    return False


def format_news_alert(event):
    return (
        f"\U0001F4F0 <b>HIGH IMPACT NEWS ALERT</b>\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\u26A0\uFE0F <b>{event['title']}</b>\n"
        f"\U0001F30D <b>Currency:</b> {event['currency']}\n"
        f"\U0001F550 <b>Time:</b> {event['time_ist'].strftime('%H:%M IST')}\n"
        f"\u23F3 <b>In:</b> {event['minutes_away']} minutes\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001F6AB Trades will be paused +-30 min around this event for GOLD & BTC.\n"
        f"<b>@Aigoldbitcoin_bot</b>"
    )


def get_active_session():
    now = datetime.now(IST)
    current_time = (now.hour, now.minute)
    for session in SESSIONS:
        if session["start"] <= current_time < session["end"]:
            return session["name"]
    return None


def format_signal(asset_key, signal, sweep, session, bias):
    asset = ASSETS[asset_key]
    direction_emoji = "\U0001F7E2" if signal["direction"] == "BUY" else "\U0001F534"

    # Fib direction line â€” clearly show which swing to which swing
    fib_high = signal.get("fib_high", "?")
    fib_low  = signal.get("fib_low", "?")
    if bias == "BUY":
        fib_range_line = f"\U0001F4CF <b>Fib Range:</b> Low {fib_low} \u2192 High {fib_high} (BUY bias)"
    else:
        fib_range_line = f"\U0001F4CF <b>Fib Range:</b> High {fib_high} \u2192 Low {fib_low} (SELL bias)"

    return (
        f"{asset['emoji']} <b>{asset['name']} SIGNAL</b> | {direction_emoji} <b>{signal['direction']}</b>\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001F4CD <b>Entry:</b> {signal['entry']}\n"
        f"\U0001F6D1 <b>SL:</b> {signal['sl']} (${signal['sl_dist']:.2f})\n"
        f"\U0001F3AF <b>TP1:</b> {signal['tp1']} ({signal['rr1']}) -> 50% close\n"
        f"\U0001F3AF <b>TP2:</b> {signal['tp2']} ({signal['rr2']}) -> remaining\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"{fib_range_line}\n"
        f"\U0001F4CA <b>Fib 38.2% Level:</b> {sweep['fib_level']}\n"
        f"\U0001F4C8 <b>Trend Bias:</b> {bias}\n"
        f"\u23F0 <b>Session:</b> {session}\n"
        f"\U0001F550 <b>Signal Time:</b> {signal['candle_time'].strftime('%H:%M IST')}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\u26A0\uFE0F <i>Use proper risk management. Max 1-2% risk per trade.</i>\n"
        f"<b>@Aigoldbitcoin_bot</b>"
    )


def format_setup_alert(asset_key, fib_level, bias, session):
    asset = ASSETS[asset_key]
    return (
        f"\U0001F536 <b>HIGH ALERT ZONE</b> | {asset['emoji']} {asset['name']}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001F3AF <b>38.2% Fib Level:</b> {fib_level}\n"
        f"\U0001F4C8 <b>Bias:</b> {bias}\n"
        f"\u23F0 <b>Session:</b> {session}\n"
        f"\U0001F440 Price swept the zone - watching for EMA + full body candle confirmation...\n"
        f"<b>@Aigoldbitcoin_bot</b>"
    )


def format_tp_sl_hit(asset_key, trade, hit_type, hit_price):
    asset = ASSETS[asset_key]
    icons = {
        "TP1": "\U0001F3AF\u2705",
        "TP2": "\U0001F3AF\U0001F3C6",
        "SL": "\U0001F6D1\u274C",
        "BREAKEVEN": "\u2696\uFE0F",
    }
    icon = icons.get(hit_type, "\u2139\uFE0F")
    if hit_type in ("TP1", "TP2"):
        pnl_label = "PROFIT"
    elif hit_type == "BREAKEVEN":
        pnl_label = "NO LOSS (Breakeven Exit)"
    else:
        pnl_label = "LOSS"

    display_type = "BREAKEVEN EXIT" if hit_type == "BREAKEVEN" else hit_type

    return (
        f"{icon} <b>{display_type} HIT</b> | {asset['emoji']} {asset['name']}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001F4CD <b>Entry was:</b> {trade['entry']}\n"
        f"\U0001F4B0 <b>Exit Price:</b> {hit_price}\n"
        f"\U0001F4CA <b>Direction:</b> {trade['direction']}\n"
        f"\U0001F3F7\uFE0F <b>Result:</b> {pnl_label}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"<b>@Aigoldbitcoin_bot</b>"
    )


def format_breakeven_alert(asset_key, trade):
    asset = ASSETS[asset_key]
    return (
        f"\u2696\uFE0F <b>RISK-FREE ACTIVATED</b> | {asset['emoji']} {asset['name']}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001F4C8 <b>1:1.5 RR achieved</b>\n"
        f"\U0001F6E1\uFE0F <b>SL moved to Entry:</b> {trade['entry']}\n"
        f"\u2705 Trade is now risk-free. Worst case = breakeven.\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"<b>@Aigoldbitcoin_bot</b>"
    )


def format_daily_bias_report(report_data):
    lines = ["\U0001F4CA <b>TODAY'S BIAS REPORT</b>", "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"]
    for asset_key, info in report_data.items():
        asset = ASSETS[asset_key]
        if info is None:
            lines.append(f"{asset['emoji']} <b>{asset['name']}:</b> Data unavailable")
            continue
        bias_emoji = "\U0001F7E2 BUY" if info["bias"] == "BUY" else ("\U0001F534 SELL" if info["bias"] == "SELL" else "\u26AA NEUTRAL")
        lines.append(
            f"{asset['emoji']} <b>{asset['name']}</b>\n"
            f"   Bias: {bias_emoji}\n"
            f"   Range High: {info['high']}\n"
            f"   Range Low: {info['low']}\n"
            f"   Fib 38.2%: {info['fib']}"
        )
    lines.append("\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    lines.append("<b>@Aigoldbitcoin_bot</b>")
    return "\n".join(lines)


def send_to_google_sheets(trade_entry):
    """Posts a completed trade row to the Google Sheets Apps Script web app."""
    if not GOOGLE_SHEETS_URL:
        return  # Not configured, skip silently

    payload = {
        "date": trade_entry["time"].strftime("%Y-%m-%d"),
        "time": trade_entry["time"].strftime("%H:%M:%S"),
        "asset": ASSETS[trade_entry["asset"]]["name"],
        "direction": trade_entry["direction"],
        "entry": trade_entry["entry"],
        "exit": trade_entry["exit"],
        "result": trade_entry["result"],
        "r_multiple": trade_entry["r_multiple"],
    }
    try:
        r = requests.post(GOOGLE_SHEETS_URL, json=payload, timeout=10)
        print(f"[SHEETS] Logged trade: {payload['asset']} {payload['direction']} -> {payload['result']}")
    except Exception as e:
        print(f"[SHEETS ERROR] {e}")


def log_trade_result(asset_key, trade, hit_type, hit_price):
    """Logs a completed trade (TP2, SL, or BREAKEVEN final close) into the journal."""
    if hit_type not in ("TP2", "SL", "BREAKEVEN"):
        return  # Only log final outcomes, not partial TP1

    direction = trade["direction"]
    entry = trade["entry"]
    # Use original SL distance (before breakeven shift) for accurate R-multiple
    original_sl = trade.get("original_sl", trade["sl"])
    sl_dist = abs(entry - original_sl)

    if hit_type == "TP2":
        result = "WIN"
        r_multiple = round(abs(hit_price - entry) / sl_dist, 2) if sl_dist else 0
    elif hit_type == "BREAKEVEN":
        result = "BREAKEVEN"
        r_multiple = 0.0
    else:
        result = "LOSS"
        r_multiple = -1.0

    new_entry = {
        "asset": asset_key,
        "direction": direction,
        "entry": entry,
        "exit": hit_price,
        "result": result,
        "r_multiple": r_multiple,
        "time": datetime.now(IST),
    }
    _trade_journal.append(new_entry)
    send_to_google_sheets(new_entry)
    print(f"[JOURNAL] {asset_key} {direction} -> {result} ({r_multiple}R)")


def format_journal_report(trades):
    if not trades:
        return (
            "\U0001F4D2 <b>DAILY TRADE JOURNAL</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "No trades completed today.\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "<b>@Aigoldbitcoin_bot</b>"
        )

    total = len(trades)
    wins = sum(1 for t in trades if t["result"] == "WIN")
    losses = sum(1 for t in trades if t["result"] == "LOSS")
    breakevens = sum(1 for t in trades if t["result"] == "BREAKEVEN")
    win_rate = round((wins / total) * 100, 1) if total else 0
    net_r = round(sum(t["r_multiple"] for t in trades), 2)

    icon_map = {"WIN": "\u2705", "LOSS": "\u274C", "BREAKEVEN": "\u2696\uFE0F"}

    lines = [
        "\U0001F4D2 <b>DAILY TRADE JOURNAL</b>",
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
    ]
    for t in trades:
        asset = ASSETS[t["asset"]]
        icon = icon_map.get(t["result"], "\u2139\uFE0F")
        lines.append(
            f"{icon} {asset['emoji']} {asset['name']} {t['direction']} "
            f"| Entry {t['entry']} -> Exit {t['exit']} "
            f"| {t['r_multiple']:+.2f}R"
        )
    lines.append("\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    lines.append(f"\U0001F4CA <b>Total Trades:</b> {total}")
    lines.append(f"\u2705 <b>Wins:</b> {wins}  \u274C <b>Losses:</b> {losses}  \u2696\uFE0F <b>Breakeven:</b> {breakevens}")
    lines.append(f"\U0001F3AF <b>Win Rate:</b> {win_rate}%")
    lines.append(f"\U0001F4B0 <b>Net R:</b> {net_r:+.2f}R")
    lines.append("\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    lines.append("<b>@Aigoldbitcoin_bot</b>")
    return "\n".join(lines)


class FibAgent:
    def __init__(self):
        self.last_signal_time = {k: None for k in ASSETS}
        self.sweep_alerted = {k: False for k in ASSETS}
        self.last_sweep = {k: None for k in ASSETS}
        self.last_bias = {k: None for k in ASSETS}
        self.active_trades = {k: None for k in ASSETS}
        # Confirmation counters â€” SL/TP must be confirmed 3 times before triggering
        self.sl_confirm_count  = {k: 0 for k in ASSETS}
        self.tp1_confirm_count = {k: 0 for k in ASSETS}
        self.tp2_confirm_count = {k: 0 for k in ASSETS}
        self.be_confirm_count  = {k: 0 for k in ASSETS}
        CONFIRM_NEEDED = 3  # Number of consecutive confirmations required

    def send_daily_bias_report(self):
        global _bias_report_sent_date
        now = datetime.now(IST)
        if now.hour < RANGE_END_HOUR:
            return
        today = now.date()
        if _bias_report_sent_date == today:
            return

        report_data = {}
        for asset_key, asset in ASSETS.items():
            df = fetch_candles(asset["symbol"], interval="1min", outputsize=500)
            if df.empty:
                report_data[asset_key] = None
                continue
            bias = get_trend_bias(df)
            range_info = get_range_high_low(df)
            fib = get_fib_level(df, bias) if bias != "NEUTRAL" else None
            if range_info:
                report_data[asset_key] = {
                    "bias": bias, "high": round(range_info[1], 2),
                    "low": round(range_info[2], 2), "fib": fib,
                }
            else:
                report_data[asset_key] = None

        msg = format_daily_bias_report(report_data)
        send_telegram(msg)
        _bias_report_sent_date = today
        print(f"[BIAS REPORT] Sent for {today}")

    def send_daily_journal_report(self):
        """Sends trade journal summary once per day after evening session ends (9:30 PM)."""
        global _journal_report_sent_date
        now = datetime.now(IST)

        # Trigger after evening session ends
        if (now.hour, now.minute) < (21, 30):
            return

        today = now.date()
        if _journal_report_sent_date == today:
            return

        todays_trades = [t for t in _trade_journal if t["time"].date() == today]
        msg = format_journal_report(todays_trades)
        send_telegram(msg)
        _journal_report_sent_date = today
        print(f"[JOURNAL REPORT] Sent for {today} - {len(todays_trades)} trades")

    def check_news_alerts(self):
        events = check_news_events()
        for event in events:
            msg = format_news_alert(event)
            send_telegram(msg)
            _alerted_news_ids.add(event["id"])
            print(f"[NEWS ALERT] {event['title']} in {event['minutes_away']} min")

    def check_active_trade(self, asset_key):
        trade = self.active_trades[asset_key]
        if trade is None:
            # Reset all counters when no active trade
            self.sl_confirm_count[asset_key]  = 0
            self.tp1_confirm_count[asset_key] = 0
            self.tp2_confirm_count[asset_key] = 0
            self.be_confirm_count[asset_key]  = 0
            return

        asset = ASSETS[asset_key]
        price = fetch_latest_price(asset["symbol"])
        if price is None:
            return

        direction = trade["direction"]
        CONFIRM_NEEDED = 3  # 3 consecutive scans (~3 min) before triggering

        if direction == "BUY":
            # --- Breakeven check ---
            if not trade.get("breakeven_done") and price >= trade["breakeven_trigger"]:
                self.be_confirm_count[asset_key] += 1
                if self.be_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    trade["sl"] = trade["entry"]
                    trade["breakeven_done"] = True
                    self.be_confirm_count[asset_key] = 0
                    send_telegram(format_breakeven_alert(asset_key, trade))
                    print(f"[BREAKEVEN] {asset_key} SL moved to entry {trade['entry']}")
            else:
                self.be_confirm_count[asset_key] = 0

            # --- TP1 check ---
            if not trade.get("tp1_hit") and price >= trade["tp1"]:
                self.tp1_confirm_count[asset_key] += 1
                if self.tp1_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    send_telegram(format_tp_sl_hit(asset_key, trade, "TP1", price))
                    trade["tp1_hit"] = True
                    self.tp1_confirm_count[asset_key] = 0
            else:
                self.tp1_confirm_count[asset_key] = 0

            # --- TP2 check ---
            if price >= trade["tp2"]:
                self.tp2_confirm_count[asset_key] += 1
                if self.tp2_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    send_telegram(format_tp_sl_hit(asset_key, trade, "TP2", price))
                    log_trade_result(asset_key, trade, "TP2", price)
                    self.active_trades[asset_key] = None
                    self.tp2_confirm_count[asset_key] = 0
                    return
            else:
                self.tp2_confirm_count[asset_key] = 0

            # --- SL check ---
            if price <= trade["sl"]:
                self.sl_confirm_count[asset_key] += 1
                print(f"[SL WATCH] {asset_key} BUY | Price {price} <= SL {trade['sl']} | Confirm {self.sl_confirm_count[asset_key]}/{CONFIRM_NEEDED}")
                if self.sl_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    hit_label = "BREAKEVEN" if trade.get("breakeven_done") else "SL"
                    send_telegram(format_tp_sl_hit(asset_key, trade, hit_label, price))
                    log_trade_result(asset_key, trade, hit_label, price)
                    self.active_trades[asset_key] = None
                    self.sl_confirm_count[asset_key] = 0
                    return
            else:
                self.sl_confirm_count[asset_key] = 0  # Reset if price recovers

        else:  # SELL
            # --- Breakeven check ---
            if not trade.get("breakeven_done") and price <= trade["breakeven_trigger"]:
                self.be_confirm_count[asset_key] += 1
                if self.be_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    trade["sl"] = trade["entry"]
                    trade["breakeven_done"] = True
                    self.be_confirm_count[asset_key] = 0
                    send_telegram(format_breakeven_alert(asset_key, trade))
                    print(f"[BREAKEVEN] {asset_key} SL moved to entry {trade['entry']}")
            else:
                self.be_confirm_count[asset_key] = 0

            # --- TP1 check ---
            if not trade.get("tp1_hit") and price <= trade["tp1"]:
                self.tp1_confirm_count[asset_key] += 1
                if self.tp1_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    send_telegram(format_tp_sl_hit(asset_key, trade, "TP1", price))
                    trade["tp1_hit"] = True
                    self.tp1_confirm_count[asset_key] = 0
            else:
                self.tp1_confirm_count[asset_key] = 0

            # --- TP2 check ---
            if price <= trade["tp2"]:
                self.tp2_confirm_count[asset_key] += 1
                if self.tp2_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    send_telegram(format_tp_sl_hit(asset_key, trade, "TP2", price))
                    log_trade_result(asset_key, trade, "TP2", price)
                    self.active_trades[asset_key] = None
                    self.tp2_confirm_count[asset_key] = 0
                    return
            else:
                self.tp2_confirm_count[asset_key] = 0

            # --- SL check ---
            if price >= trade["sl"]:
                self.sl_confirm_count[asset_key] += 1
                print(f"[SL WATCH] {asset_key} SELL | Price {price} >= SL {trade['sl']} | Confirm {self.sl_confirm_count[asset_key]}/{CONFIRM_NEEDED}")
                if self.sl_confirm_count[asset_key] >= CONFIRM_NEEDED:
                    hit_label = "BREAKEVEN" if trade.get("breakeven_done") else "SL"
                    send_telegram(format_tp_sl_hit(asset_key, trade, hit_label, price))
                    log_trade_result(asset_key, trade, hit_label, price)
                    self.active_trades[asset_key] = None
                    self.sl_confirm_count[asset_key] = 0
                    return
            else:
                self.sl_confirm_count[asset_key] = 0  # Reset if price recovers

    def run_asset(self, asset_key):
        asset = ASSETS[asset_key]
        session = get_active_session()

        self.check_active_trade(asset_key)

        if not session:
            return

        print(f"\n[{datetime.now(IST).strftime('%H:%M')}] Scanning {asset['name']} | Session: {session}")

        if self.active_trades[asset_key] is not None:
            print(f"[SKIP] {asset_key} - Trade already active, monitoring TP/SL")
            return

        df = fetch_candles(asset["symbol"], interval="1min", outputsize=500)
        if df.empty:
            print(f"[SKIP] No data for {asset_key}")
            return

        bias = get_trend_bias(df)
        if bias == "NEUTRAL":
            print(f"[SKIP] {asset_key} - Neutral bias")
            return

        if bias != self.last_bias[asset_key]:
            print(f"[BIAS] {asset_key} bias changed to {bias}")
            self.last_bias[asset_key] = bias
            self.sweep_alerted[asset_key] = False
            self.last_sweep[asset_key] = None

        fib_level = get_fib_level(df, bias)
        if fib_level is None:
            print(f"[SKIP] {asset_key} - Could not calculate Fib level")
            return

        if is_high_impact_news_now():
            print(f"[SKIP] {asset_key} - High impact news active")
            return

        sweep = detect_sweep(df, fib_level, bias)

        if sweep:
            if not self.sweep_alerted[asset_key]:
                msg = format_setup_alert(asset_key, fib_level, bias, session)
                send_telegram(msg)
                self.sweep_alerted[asset_key] = True
                self.last_sweep[asset_key] = sweep
                print(f"[SWEEP] {asset_key} - Sweep detected at {fib_level}")

            signal = detect_entry(df, sweep, bias, asset["min_sl"], asset["max_sl"])

            if signal:
                last = self.last_signal_time[asset_key]
                if last and (datetime.now(IST) - last).seconds < 7200:
                    print(f"[SKIP] {asset_key} - Signal already sent recently")
                    return

                msg = format_signal(asset_key, signal, sweep, session, bias)

                # Generate and send chart image with signal as caption
                try:
                    chart_buf = generate_signal_chart(df, signal, sweep, bias, asset_key)
                    sent = send_telegram_photo(chart_buf, msg)
                    if not sent:
                        # Fallback to text-only if photo fails
                        send_telegram(msg)
                except Exception as chart_err:
                    print(f"[CHART ERROR] {chart_err} â€” sending text only")
                    send_telegram(msg)

                self.last_signal_time[asset_key] = datetime.now(IST)
                self.sweep_alerted[asset_key] = False

                # Calculate breakeven trigger level at 1:1.5 RR
                sl_distance = signal["sl_dist"]
                if signal["direction"] == "BUY":
                    breakeven_trigger = signal["entry"] + (sl_distance * 1.5)
                else:
                    breakeven_trigger = signal["entry"] - (sl_distance * 1.5)

                self.active_trades[asset_key] = {
                    "direction": signal["direction"], "entry": signal["entry"],
                    "sl": signal["sl"], "original_sl": signal["sl"],
                    "tp1": signal["tp1"], "tp2": signal["tp2"],
                    "tp1_hit": False, "breakeven_done": False,
                    "breakeven_trigger": round(breakeven_trigger, 4),
                }
                print(f"[SIGNAL] {asset_key} {signal['direction']} @ {signal['entry']}")
        else:
            print(f"[WAIT] {asset_key} - Fib: {fib_level} | Bias: {bias} | No sweep yet")

    def run(self):
        print("=" * 60)
        print("  GOLD & BTC Fibonacci Sweep Agent - STARTED")
        print(f"  Telegram: {TELEGRAM_CHANNEL}")
        print("=" * 60)

        send_telegram(
            "\U0001F916 <b>Agent Started</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "\U0001F4CA Monitoring: GOLD & BTC\n"
            "\u23F0 Sessions: 9AM-1PM & 5:30PM-9:30PM IST\n"
            "\U0001F3AF Strategy: 38.2% Fib Sweep\n"
            "\U0001D7EE News Alerts | TP/SL Tracking | Daily Bias Report\n"
            "<b>@Aigoldbitcoin_bot</b>"
        )

        while True:
            try:
                self.check_news_alerts()
                self.send_daily_bias_report()
                self.send_daily_journal_report()

                # Scan Gold and BTC in parallel threads for faster signal delivery
                threads = []
                for asset_key in ASSETS:
                    t = threading.Thread(target=self.run_asset, args=(asset_key,), daemon=True)
                    threads.append(t)
                    t.start()
                for t in threads:
                    t.join(timeout=30)  # Max 30s wait per asset thread

                print(f"\n[SLEEP] Next scan in {SCAN_INTERVAL_S}s...")
                time.sleep(SCAN_INTERVAL_S)

            except KeyboardInterrupt:
                print("\n[STOPPED] Agent manually stopped.")
                break
            except Exception as e:
                print(f"[ERROR] {e}")
                time.sleep(30)


if __name__ == "__main__":
    agent = FibAgent()
    agent.run()
