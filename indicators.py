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
    """
    Добавляем технические индикаторы к данным
    """
    # RSI — индекс относительной силы (период 14 свечей)
    df['rsi'] = ta.momentum.RSIIndicator(
        close=df['close'],
        window=14
    ).rsi()

    # EMA — экспоненциальная скользящая средняя
    df['ema_20'] = ta.trend.EMAIndicator(
        close=df['close'],
        window=20
    ).ema_indicator()

    df['ema_50'] = ta.trend.EMAIndicator(
        close=df['close'],
        window=50
    ).ema_indicator()

    # MACD — схождение/расхождение скользящих средних
    macd = ta.trend.MACD(close=df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()

    return df

# Получаем данные и добавляем индикаторы
df = get_candles('BTCUSDT', '1h', 100)
df = add_indicators(df)

# Сохраняем
df.to_csv('data/btcusdt_indicators.csv', index=False)

# Показываем последние 3 свечи с индикаторами
print("✅ Индикаторы рассчитаны!")
print("\n📊 Последние 3 свечи с индикаторами:")
print(df[['time', 'close', 'rsi', 'ema_20', 'macd']].tail(3).to_string(index=False))

# Простая интерпретация текущего состояния
last = df.iloc[-1]
print("\n🔍 Анализ текущей ситуации:")
print(f"   Цена BTC:  {last['close']:.2f} USDT")
print(f"   RSI:       {last['rsi']:.2f}", end=" ")

if last['rsi'] > 70:
    print("⚠️ Перекуплен — осторожно с покупкой")
elif last['rsi'] < 30:
    print("💡 Перепродан — возможна покупка")
else:
    print("✅ Нейтральная зона")

print(f"   EMA20:     {last['ema_20']:.2f}")
print(f"   EMA50:     {last['ema_50']:.2f}", end=" ")

if last['ema_20'] > last['ema_50']:
    print("📈 Тренд восходящий")
else:
    print("📉 Тренд нисходящий")