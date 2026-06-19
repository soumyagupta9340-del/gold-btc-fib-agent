"""
╔══════════════════════════════════════════════════════════════╗
║     GOLD & BTC — Fibonacci Sweep Strategy Agent             ║
║     Telegram: @Alphagoldsign                                ║
║     Strategy: 38.2% Fib Liquidity Sweep + EMA Confirmation ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz

# ─────────────────────────────────────────────
#  CONFIG — Fill these in Railway Environment
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "your_bot_token_here")
TELEGRAM_CHANNEL   = os.environ.get("TELEGRAM_CHANNEL", "@Alphagoldsign")
TWELVE_DATA_KEY    = os.environ.get("TWELVE_DATA_API_KEY", "your_twelvedata_key_here")

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
#  STRATEGY SETTINGS
# ─────────────────────────────────────────────
ASSETS = {
    "XAUUSD": {
        "symbol"      : "XAU/USD",
        "name"        : "GOLD",
        "emoji"       : "🥇",
        "max_sl"      : 8,       # Max SL in dollars
        "interval"    : "1min",
    },
    "BTCUSDT": {
        "symbol"      : "BTC/USD",
        "name"        : "BITCOIN",
        "emoji"       : "₿",
        "max_sl"      : 200,     # Max SL in dollars
        "interval"    : "1min",
    },
}

# Session times IST
SESSIONS = [
    {"name": "Morning", "start": (9, 0),  "end": (13, 0)},
    {"name": "Evening", "start": (17, 30), "end": (21, 30)},
]

# Range candle window IST (12 AM to 9 AM)
RANGE_START_HOUR = 0   # 12:00 AM
RANGE_END_HOUR   = 9   # 9:00 AM

FIB_LEVEL        = 0.382
EMA_FAST         = 9
EMA_SLOW         = 20
ENTRY_WINDOW_MIN = 60   # Entry valid for 60 minutes after sweep
SCAN_INTERVAL_S  = 60   # Scan every 60 seconds

# High impact news keywords to skip
NEWS_KEYWORDS = [
    "NFP", "CPI", "Fed", "FOMC", "Interest Rate",
    "GDP", "PPI", "Unemployment", "Powell", "ECB"
]

# ─────────────────────────────────────────────
#  TELEGRAM SENDER
# ─────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id"    : TELEGRAM_CHANNEL,
        "text"       : message,
        "parse_mode" : "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[TG SENT] {message[:60]}...")
    except Exception as e:
        print(f"[TG ERROR] {e}")


# ─────────────────────────────────────────────
#  DATA FETCHER — Twelve Data
# ─────────────────────────────────────────────
def fetch_candles(symbol: str, interval: str = "1min", outputsize: int = 500) -> pd.DataFrame:
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol"    : symbol,
        "interval"  : interval,
        "outputsize": outputsize,
        "apikey"    : TWELVE_DATA_KEY,
        "timezone"  : "Asia/Kolkata",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"[DATA ERROR] {symbol}: {data.get('message', 'Unknown error')}")
            return pd.DataFrame()

        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as e:
        print(f"[FETCH ERROR] {symbol}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
#  TREND BIAS — 12AM to 9AM candle direction
# ─────────────────────────────────────────────
def get_trend_bias(df: pd.DataFrame) -> str:
    """
    Overall direction of 12AM–9AM range.
    Returns 'BUY', 'SELL', or 'NEUTRAL'
    """
    now = datetime.now(IST)
    today = now.date()

    range_start = IST.localize(datetime(today.year, today.month, today.day, RANGE_START_HOUR, 0))
    range_end   = IST.localize(datetime(today.year, today.month, today.day, RANGE_END_HOUR, 0))

    range_df = df[(df["datetime"] >= range_start) & (df["datetime"] <= range_end)]

    if range_df.empty:
        return "NEUTRAL"

    open_price  = range_df.iloc[0]["open"]
    close_price = range_df.iloc[-1]["close"]

    if close_price > open_price:
        return "BUY"
    elif close_price < open_price:
        return "SELL"
    return "NEUTRAL"


# ─────────────────────────────────────────────
#  FIBONACCI CALCULATION
# ─────────────────────────────────────────────
def get_fib_level(df: pd.DataFrame, bias: str) -> float | None:
    """
    12AM to most recent candle — calculate 38.2% Fib level
    """
    now = datetime.now(IST)
    today = now.date()
    range_start = IST.localize(datetime(today.year, today.month, today.day, RANGE_START_HOUR, 0))

    fib_df = df[df["datetime"] >= range_start]
    if fib_df.empty:
        return None

    high = fib_df["high"].max()
    low  = fib_df["low"].min()

    if bias == "BUY":
        # Fib from Low to High — 38.2% retracement = pullback level
        fib_382 = high - (high - low) * FIB_LEVEL
    elif bias == "SELL":
        # Fib from High to Low — 38.2% retracement = pullback level
        fib_382 = low + (high - low) * FIB_LEVEL
    else:
        return None

    return round(fib_382, 4)


# ─────────────────────────────────────────────
#  EMA CALCULATION
# ─────────────────────────────────────────────
def calculate_ema(df: pd.DataFrame) -> pd.DataFrame:
    df[f"ema{EMA_FAST}"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df[f"ema{EMA_SLOW}"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return df


# ─────────────────────────────────────────────
#  FULL BODY CANDLE CHECK
# ─────────────────────────────────────────────
def is_full_body_candle(candle: pd.Series, bias: str) -> bool:
    """
    Full body candle = body is at least 70% of total candle range
    Direction must match bias
    """
    body   = abs(candle["close"] - candle["open"])
    total  = candle["high"] - candle["low"]

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


# ─────────────────────────────────────────────
#  LIQUIDITY SWEEP DETECTION
# ─────────────────────────────────────────────
def detect_sweep(df: pd.DataFrame, fib_level: float, bias: str) -> dict | None:
    """
    Wick touches 38.2% level but candle does NOT close beyond it.
    Returns the sweep candle info if found within last 60 minutes.
    """
    now = datetime.now(IST)
    lookback = now - timedelta(minutes=ENTRY_WINDOW_MIN)
    recent_df = df[df["datetime"] >= lookback].copy()

    for _, candle in recent_df.iterrows():
        if bias == "BUY":
            # Price wicks below fib level but closes above
            if candle["low"] <= fib_level and candle["close"] > fib_level:
                return {
                    "time"     : candle["datetime"],
                    "fib_level": fib_level,
                    "candle"   : candle,
                }
        elif bias == "SELL":
            # Price wicks above fib level but closes below
            if candle["high"] >= fib_level and candle["close"] < fib_level:
                return {
                    "time"     : candle["datetime"],
                    "fib_level": fib_level,
                    "candle"   : candle,
                }
    return None


# ─────────────────────────────────────────────
#  ENTRY SIGNAL DETECTION
# ─────────────────────────────────────────────
def detect_entry(df: pd.DataFrame, sweep: dict, bias: str, max_sl: float) -> dict | None:
    """
    After sweep: look for full body candle above/below 20 EMA
    within 60 min entry window
    """
    df = calculate_ema(df)
    sweep_time = sweep["time"]
    window_end = sweep_time + timedelta(minutes=ENTRY_WINDOW_MIN)
    now = datetime.now(IST)

    entry_df = df[
        (df["datetime"] > sweep_time) &
        (df["datetime"] <= min(window_end, now))
    ].copy()

    for _, candle in entry_df.iterrows():
        ema20 = candle[f"ema{EMA_SLOW}"]

        if bias == "BUY" and candle["close"] > ema20:
            if is_full_body_candle(candle, "BUY"):
                sl    = candle["low"]
                entry = candle["close"]
                sl_distance = entry - sl

                if sl_distance > max_sl:
                    print(f"[SKIP] SL too wide: ${sl_distance:.2f} > ${max_sl}")
                    return None

                tp1 = entry + (sl_distance * 2)
                tp2 = entry + (sl_distance * 3.5)

                return {
                    "direction": "BUY",
                    "entry"    : round(entry, 4),
                    "sl"       : round(sl, 4),
                    "tp1"      : round(tp1, 4),
                    "tp2"      : round(tp2, 4),
                    "sl_dist"  : round(sl_distance, 4),
                    "rr1"      : "1:2",
                    "rr2"      : "1:3.5",
                    "candle_time": candle["datetime"],
                }

        elif bias == "SELL" and candle["close"] < ema20:
            if is_full_body_candle(candle, "SELL"):
                sl    = candle["high"]
                entry = candle["close"]
                sl_distance = sl - entry

                if sl_distance > max_sl:
                    print(f"[SKIP] SL too wide: ${sl_distance:.2f} > ${max_sl}")
                    return None

                tp1 = entry - (sl_distance * 2)
                tp2 = entry - (sl_distance * 3.5)

                return {
                    "direction": "SELL",
                    "entry"    : round(entry, 4),
                    "sl"       : round(sl, 4),
                    "tp1"      : round(tp1, 4),
                    "tp2"      : round(tp2, 4),
                    "sl_dist"  : round(sl_distance, 4),
                    "rr1"      : "1:2",
                    "rr2"      : "1:3.5",
                    "candle_time": candle["datetime"],
                }

    return None


# ─────────────────────────────────────────────
#  NEWS FILTER — ForexFactory
# ─────────────────────────────────────────────
def is_high_impact_news_now() -> bool:
    """
    Check if high impact news is within ±30 minutes
    Uses ForexFactory RSS feed
    """
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=10)
        events = r.json()
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
            except:
                continue
    except Exception as e:
        print(f"[NEWS CHECK ERROR] {e}")
    return False


# ─────────────────────────────────────────────
#  SESSION CHECK
# ─────────────────────────────────────────────
def get_active_session() -> str | None:
    now = datetime.now(IST)
    current_time = (now.hour, now.minute)

    for session in SESSIONS:
        if session["start"] <= current_time < session["end"]:
            return session["name"]
    return None


# ─────────────────────────────────────────────
#  ALERT MESSAGES
# ─────────────────────────────────────────────
def format_signal(asset_key: str, signal: dict, sweep: dict, session: str, bias: str) -> str:
    asset = ASSETS[asset_key]
    direction_emoji = "🟢" if signal["direction"] == "BUY" else "🔴"

    return f"""
{asset['emoji']} <b>{asset['name']} SIGNAL</b> | {direction_emoji} <b>{signal['direction']}</b>
━━━━━━━━━━━━━━━━━━━━
📍 <b>Entry:</b> {signal['entry']}
🛑 <b>SL:</b> {signal['sl']} (${signal['sl_dist']:.2f})
🎯 <b>TP1:</b> {signal['tp1']} ({signal['rr1']}) → 50% close
🎯 <b>TP2:</b> {signal['tp2']} ({signal['rr2']}) → remaining
━━━━━━━━━━━━━━━━━━━━
📊 <b>Fib 38.2%:</b> {sweep['fib_level']}
📈 <b>Trend Bias:</b> {bias}
⏰ <b>Session:</b> {session}
🕐 <b>Signal Time:</b> {signal['candle_time'].strftime('%H:%M IST')}
━━━━━━━━━━━━━━━━━━━━
⚠️ <i>Use proper risk management. Max 1-2% risk per trade.</i>
<b>@Alphagoldsign</b>
""".strip()


def format_setup_alert(asset_key: str, fib_level: float, bias: str, session: str) -> str:
    asset = ASSETS[asset_key]
    return f"""
⚠️ <b>SETUP FORMING</b> | {asset['emoji']} {asset['name']}
━━━━━━━━━━━━━━━━━━━━
🎯 <b>38.2% Fib Level:</b> {fib_level}
📈 <b>Bias:</b> {bias}
⏰ <b>Session:</b> {session}
👀 Waiting for liquidity sweep + EMA confirmation...
<b>@Alphagoldsign</b>
""".strip()


# ─────────────────────────────────────────────
#  MAIN AGENT LOOP
# ─────────────────────────────────────────────
class FibAgent:
    def __init__(self):
        # Track state per asset to avoid duplicate signals
        self.last_signal_time  = {k: None for k in ASSETS}
        self.sweep_alerted     = {k: False for k in ASSETS}
        self.last_sweep        = {k: None for k in ASSETS}
        self.last_bias         = {k: None for k in ASSETS}

    def run_asset(self, asset_key: str):
        asset   = ASSETS[asset_key]
        session = get_active_session()

        if not session:
            return  # Outside trading hours

        print(f"\n[{datetime.now(IST).strftime('%H:%M')}] Scanning {asset['name']} | Session: {session}")

        # Fetch 1M candles
        df = fetch_candles(asset["symbol"], interval="1min", outputsize=500)
        if df.empty:
            print(f"[SKIP] No data for {asset_key}")
            return

        # Get trend bias
        bias = get_trend_bias(df)
        if bias == "NEUTRAL":
            print(f"[SKIP] {asset_key} — Neutral bias")
            return

        if bias != self.last_bias[asset_key]:
            print(f"[BIAS] {asset_key} bias changed to {bias}")
            self.last_bias[asset_key] = bias
            self.sweep_alerted[asset_key] = False
            self.last_sweep[asset_key] = None

        # Get Fib 38.2% level
        fib_level = get_fib_level(df, bias)
        if fib_level is None:
            print(f"[SKIP] {asset_key} — Could not calculate Fib level")
            return

        # News filter
        if is_high_impact_news_now():
            print(f"[SKIP] {asset_key} — High impact news active")
            return

        # Detect liquidity sweep
        sweep = detect_sweep(df, fib_level, bias)

        if sweep:
            # Send setup forming alert only once per sweep
            if not self.sweep_alerted[asset_key]:
                msg = format_setup_alert(asset_key, fib_level, bias, session)
                send_telegram(msg)
                self.sweep_alerted[asset_key] = True
                self.last_sweep[asset_key] = sweep
                print(f"[SWEEP] {asset_key} — Sweep detected at {fib_level}")

            # Look for entry signal
            signal = detect_entry(df, sweep, bias, asset["max_sl"])

            if signal:
                # Avoid duplicate signals within 2 hours
                last = self.last_signal_time[asset_key]
                if last and (datetime.now(IST) - last).seconds < 7200:
                    print(f"[SKIP] {asset_key} — Signal already sent recently")
                    return

                msg = format_signal(asset_key, signal, sweep, session, bias)
                send_telegram(msg)
                self.last_signal_time[asset_key] = datetime.now(IST)
                self.sweep_alerted[asset_key] = False  # Reset for next setup
                print(f"[SIGNAL] {asset_key} {signal['direction']} @ {signal['entry']}")
        else:
            print(f"[WAIT] {asset_key} — Fib: {fib_level} | Bias: {bias} | No sweep yet")

    def run(self):
        print("=" * 60)
        print("  GOLD & BTC Fibonacci Sweep Agent — STARTED")
        print(f"  Telegram: {TELEGRAM_CHANNEL}")
        print("=" * 60)

        send_telegram(
            f"🤖 <b>Agent Started</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 Monitoring: GOLD & BTC\n"
            f"⏰ Sessions: 9AM–1PM & 5:30PM–9:30PM IST\n"
            f"🎯 Strategy: 38.2% Fib Sweep\n"
            f"<b>@Alphagoldsign</b>"
        )

        while True:
            try:
                for asset_key in ASSETS:
                    self.run_asset(asset_key)
                    time.sleep(2)  # Small delay between assets

                print(f"\n[SLEEP] Next scan in {SCAN_INTERVAL_S}s...")
                time.sleep(SCAN_INTERVAL_S)

            except KeyboardInterrupt:
                print("\n[STOPPED] Agent manually stopped.")
                break
            except Exception as e:
                print(f"[ERROR] {e}")
                time.sleep(30)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    agent = FibAgent()
    agent.run()
