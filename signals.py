from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import ta
import os
import time

load_dotenv()

API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')

client = Client(
    api_key=API_KEY,
    api_secret=API_SECRET,
    testnet=True
)

server_time = client.get_server_time()
client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)

def get_candles(symbol, interval, limit=100):
    raw = client.get_klines(
        symbol=symbol,
        interval=interval,
        limit=limit
    )
    df = pd.DataFrame(raw, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df = df[['time', 'open', 'high', 'low', 'close', 'volume']]
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    return df

def add_indicators(df):
    df['rsi'] = ta.momentum.RSIIndicator(
        close=df['close'], window=14
    ).rsi()
    df['ema_20'] = ta.trend.EMAIndicator(
        close=df['close'], window=20
    ).ema_indicator()
    df['ema_50'] = ta.trend.EMAIndicator(
        close=df['close'], window=50
    ).ema_indicator()
    macd = ta.trend.MACD(close=df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    return df

def generate_signal(df):
    """
    Анализируем последние две свечи и генерируем сигнал.
    Смотрим на ИЗМЕНЕНИЕ — это важнее чем просто текущее значение.
    """
    last = df.iloc[-1]      # последняя свеча
    prev = df.iloc[-2]      # предыдущая свеча

    signal = "WAIT"         # по умолчанию — ждём
    reasons = []            # причины сигнала

    # ── СИГНАЛ НА ПОКУПКУ ──────────────────────────────────
    buy_conditions = 0

    # RSI поднимается из зоны перепроданности
    if prev['rsi'] < 30 and last['rsi'] >= 30:
        buy_conditions += 1
        reasons.append("RSI вышел из зоны перепроданности (<30)")

    # EMA20 пересекает EMA50 снизу вверх (золотой крест)
    if prev['ema_20'] < prev['ema_50'] and last['ema_20'] >= last['ema_50']:
        buy_conditions += 1
        reasons.append("EMA20 пересекла EMA50 вверх (золотой крест)")

    # MACD пересекает сигнальную линию снизу вверх
    if prev['macd'] < prev['macd_signal'] and last['macd'] >= last['macd_signal']:
        buy_conditions += 1
        reasons.append("MACD пересёк сигнальную линию вверх")

    # ── СИГНАЛ НА ПРОДАЖУ ──────────────────────────────────
    sell_conditions = 0

    # RSI опускается из зоны перекупленности
    if prev['rsi'] > 70 and last['rsi'] <= 70:
        sell_conditions += 1
        reasons.append("RSI вышел из зоны перекупленности (>70)")

    # EMA20 пересекает EMA50 сверху вниз (мёртвый крест)
    if prev['ema_20'] > prev['ema_50'] and last['ema_20'] <= last['ema_50']:
        sell_conditions += 1
        reasons.append("EMA20 пересекла EMA50 вниз (мёртвый крест)")

    # MACD пересекает сигнальную линию сверху вниз
    if prev['macd'] > prev['macd_signal'] and last['macd'] <= last['macd_signal']:
        sell_conditions += 1
        reasons.append("MACD пересёк сигнальную линию вниз")

    # ── ИТОГОВОЕ РЕШЕНИЕ ───────────────────────────────────
    # Требуем минимум 2 подтверждения для сигнала
    if buy_conditions >= 2:
        signal = "BUY"
    elif sell_conditions >= 2:
        signal = "SELL"

    return signal, reasons, last

# ── ЗАПУСК ─────────────────────────────────────────────────
df = get_candles('BTCUSDT', '1h', 100)
df = add_indicators(df)
signal, reasons, last = generate_signal(df)

print("=" * 45)
print("       🤖 АНАЛИЗ РЫНКА BTCUSDT")
print("=" * 45)
print(f"  Цена:        {last['close']:.2f} USDT")
print(f"  RSI:         {last['rsi']:.2f}")
print(f"  EMA20:       {last['ema_20']:.2f}")
print(f"  EMA50:       {last['ema_50']:.2f}")
print(f"  MACD:        {last['macd']:.2f}")
print("-" * 45)

if signal == "BUY":
    print("  🟢 СИГНАЛ:   КУПИТЬ")
elif signal == "SELL":
    print("  🔴 СИГНАЛ:   ПРОДАТЬ")
else:
    print("  ⏳ СИГНАЛ:   ЖДАТЬ")

if reasons:
    print("\n  📋 Причины:")
    for r in reasons:
        print(f"     • {r}")

print("=" * 45)