import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
import pytz
from datetime import datetime, timedelta
import requests
import sys
import ctypes

# ---------- KEEP SYSTEM AWAKE ----------
try:
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    )
    print("💡 Sleep prevention active — system will stay awake while this script runs.")
except Exception as e:
    print(f"⚠️ Could not enable sleep prevention: {e}")

# ---------- CONFIG ----------
symbols = ["RELIANCE.NS", "HCLTECH.NS"]
interval = "5m"
lookback = "5d"
telegram_token = "YOUR_TELEGRAM_BOT_TOKEN"
telegram_chat_id = "YOUR_CHAT_ID"
test_mode = True
sleep_padding_sec = 15
heartbeat_interval_min = 15

STRAT1 = "RSI+MACD+CCI Rally Catcher"
STRAT2 = "VWAP+RSI+ATR Trailing SL"
STRAT3 = "CCI+MACD Momentum Flip"

# ---------- TELEGRAM ----------
def send_telegram(message):
    if test_mode:
        print(f"[TEST TELEGRAM] {message}")
        return
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.post(url, data={"chat_id": telegram_chat_id, "text": message})
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

# ---------- LOGGER ----------
def log_session(strategy_name, symbol, signal, price, reason, source):
    ist = pytz.timezone("Asia/Kolkata")
    timestamp = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
    log_filename = f"session_log_{datetime.now(ist).strftime('%Y-%m-%d')}.csv"
    log_exists = os.path.exists(log_filename)
    with open(log_filename, "a", newline="") as f:
        import csv
        writer = csv.writer(f)
        if not log_exists:
            writer.writerow(["Timestamp", "Strategy", "Symbol",
                             "Signal", "Price", "Reason", "Source"])
        writer.writerow([timestamp, strategy_name, symbol,
                         signal, price, reason, source])

# ---------- INDICATORS ----------
def compute_indicators(df):
    df["EMA12"] = df["Close"].ewm(span=12, adjust=False).mean()
    df["EMA26"] = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = df["EMA12"] - df["EMA26"]
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df["RSI"] = 100 - (100 / (1 + rs))

    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = tp * df["Volume"]
    df["VWAP"] = pv.cumsum() / df["Volume"].cumsum()

    df["CCI"] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).std())
    df["CCI_signal"] = df["CCI"].rolling(9).mean()

    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    return df.dropna()

# ---------- STRATEGIES ----------
def get_signals(df, strat_name):
    last, prev = df.iloc[-1], df.iloc[-2]
    sig, reason = "HOLD", "-"
    if strat_name == STRAT1:
        if last["MACD_hist"] > 0 and prev["MACD_hist"] <= 0 and last["RSI"] < 50 and last["CCI"] < 0:
            sig, reason = "BUY", "MACD green, RSI low, CCI oversold"
        elif last["MACD_hist"] < 0 and last["RSI"] > 60:
            sig, reason = "SELL", "MACD red + RSI high"
    elif strat_name == STRAT2:
        if last["Close"] > last["VWAP"] and last["RSI"] > 55 and last["Close"] > last["EMA26"]:
            sig, reason = "BUY", "Price>VWAP, RSI strong, EMA trend up"
        elif last["RSI"] < 45:
            sig, reason = "SELL", "RSI weakened / fade"
    elif strat_name == STRAT3:
        macd_fading = (last["MACD_hist"] > prev["MACD_hist"]) or (last["MACD_hist"] > 0 and prev["MACD_hist"] <= 0)
        cci_cross = (last["CCI"] > last["CCI_signal"]) and (prev["CCI"] <= prev["CCI_signal"]) and (prev["CCI"] < 0)
        if cci_cross and macd_fading:
            sig, reason = "BUY", "CCI cross up + MACD fading red"
        elif last["CCI"] < last["CCI_signal"] and last["MACD_hist"] < 0:
            sig, reason = "SELL", "CCI cross down + MACD red"
    return sig, reason

# ---------- HEALTH CHECK ----------
def premarket_data_check():
    print("\n🔍 Running pre-market data health check...\n")
    for sym in symbols:
        try:
            df = yf.download(sym, period="5d", interval="5m", progress=False)
            if df.empty:
                print(f"⚠️ {sym}: No data fetched!")
                continue
            df = compute_indicators(df)
            last = df.iloc[-1]
            print(f"{sym} → Last candle: {last.name} | Close: {round(last['Close'], 2)}")
        except Exception as e:
            print(f"❌ {sym}: Error fetching data — {e}")
    print("\n✅ Data sanity check complete. Sleeping until market open...\n")

# ---------- MAIN LOOP ----------
def run_forward():
    ist = pytz.timezone("Asia/Kolkata")
    print(f"\n🚀 Starting 3-Strategy Forward Test — interval={interval}")
    print(f"Symbols: {', '.join(symbols)}\n")
    last_heartbeat = datetime.now(ist)

    while True:
        now = datetime.now(ist)
        hhmm = now.strftime("%H:%M")

        # Heartbeat
        if (now - last_heartbeat).total_seconds() > heartbeat_interval_min * 60:
            print(f"[{now.strftime('%H:%M:%S')}] 💓 Heartbeat — script alive.")
            last_heartbeat = now

        # Market cutoff
        if hhmm >= "15:14":
            print("⏰ Market closing. Auto square-off triggered.")
            for s in symbols:
                for st in [STRAT1, STRAT2, STRAT3]:
                    log_session(st, s, "FORCE_EXIT", "-", "Market Close", "Auto Exit")
                    send_telegram(f"[AUTO EXIT] {st} | {s} | Market closing.")
            break

        # Outside trade window
        if hhmm < "09:35" or hhmm > "14:45":
            premarket_data_check()
            time.sleep(300)
            continue

        # Wait till next 5m candle boundary
        next_5m = (now + timedelta(minutes=5 - now.minute % 5)).replace(second=0, microsecond=0)
        wait_sec = (next_5m - now).seconds + sleep_padding_sec
        print(f"[{now.strftime('%H:%M:%S')}] Waiting {wait_sec}s for next 5m candle...")
        time.sleep(wait_sec)

        for sym in symbols:
            print(f"\n[{datetime.now(ist).strftime('%H:%M:%S')}] Processing {sym}...")
            try:
                df = yf.download(sym, period=lookback, interval=interval, progress=False)
                df = compute_indicators(df)
                if len(df) < 30:
                    print(f"⚠️ Not enough data for {sym}")
                    continue
                price = round(df["Close"].iloc[-1], 2)
                for strategy in [STRAT1, STRAT2, STRAT3]:
                    sig, reason = get_signals(df, strategy)
                    print(f"{strategy}: {sig} @ {price} ({reason})")
                    log_session(strategy, sym, sig, price, reason, "5m Loop")
                    if sig in ["BUY", "SELL"]:
                        send_telegram(f"[{strategy}] {sig} | {sym} @ {price}\nReason: {reason}")
            except Exception as e:
                print(f"⚠️ Error processing {sym}: {e}")

        print(f"--- Cycle complete. Sleeping until next 5m window ---\n")

# ---------- ENTRY ----------
if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    run_forward()
