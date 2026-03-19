from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import requests
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

def analyze_volume(df):
    """
    Анализируем объём торгов.

    Объём — это количество монет которые сменили владельца
    за период свечи. Это самый честный индикатор потому что
    его невозможно нарисовать — за ним стоят реальные деньги.

    Что ищем:
    - Аномальный объём = в 2+ раза выше среднего
      Это значит крупный игрок вошёл или вышел из позиции

    - Объём растёт вместе с ценой = сильное движение
      Покупатели агрессивны — тренд продолжится

    - Цена растёт но объём падает = слабое движение
      Нет реального интереса — возможен разворот
    """
    # Средний объём за последние 20 свечей
    avg_volume = df['volume'].rolling(20).mean()
    df['avg_volume'] = avg_volume

    # Коэффициент — во сколько раз текущий объём выше среднего
    df['volume_ratio'] = df['volume'] / df['avg_volume']

    # Направление свечи
    df['bullish'] = df['close'] > df['open']

    # Последние 5 свечей
    recent = df.tail(5).copy()

    results = []
    for _, row in recent.iterrows():
        direction = '🟢' if row['bullish'] else '🔴'
        anomaly   = '⚡ АНОМАЛИЯ' if row['volume_ratio'] > 2 else ''
        results.append({
            'time':         row['time'],
            'close':        row['close'],
            'volume':       row['volume'],
            'avg_volume':   row['avg_volume'],
            'volume_ratio': row['volume_ratio'],
            'direction':    direction,
            'anomaly':      anomaly
        })

    # Общий вывод по объёму
    last = df.iloc[-1]
    last_3 = df.tail(3)

    price_trend  = last_3['close'].iloc[-1] > last_3['close'].iloc[0]
    volume_trend = last_3['volume'].iloc[-1] > last_3['volume'].iloc[0]

    if price_trend and volume_trend:
        conclusion = '💪 Цена растёт + объём растёт = сильное движение вверх'
    elif price_trend and not volume_trend:
        conclusion = '⚠️  Цена растёт + объём падает = слабый рост, осторожно'
    elif not price_trend and volume_trend:
        conclusion = '🔥 Цена падает + объём растёт = сильное давление продавцов'
    else:
        conclusion = '😴 Цена падает + объём падает = вялое движение, нет интереса'

    return results, conclusion, last['volume_ratio']

def get_funding_rate(symbol='BTCUSDT'):
    """
    Получаем Funding Rate с реального Binance Futures.

    Funding Rate — это уникальный инструмент крипторынка.
    Каждые 8 часов лонгисты платят шортистам (или наоборот).

    Как читать:
    + Положительный (>0.01%)  = рынок перегрет лонгами
      Все купили, некому покупать дальше → разворот вниз

    - Отрицательный (<-0.01%) = рынок перегрет шортами
      Все продали, некому продавать дальше → разворот вверх

    Нейтральный (около 0%)   = рынок сбалансирован
    """
    try:
        # Funding Rate доступен только на реальном Binance
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
        response = requests.get(url, timeout=5)
        data = response.json()

        if data and isinstance(data, list):
            rate = float(data[0]['fundingRate']) * 100  # переводим в %

            if rate > 0.05:
                interpretation = '🔴 Сильно перегрет лонгами — риск падения'
            elif rate > 0.01:
                interpretation = '⚠️  Умеренно перегрет лонгами'
            elif rate < -0.05:
                interpretation = '🟢 Сильно перегрет шортами — риск роста'
            elif rate < -0.01:
                interpretation = '⚠️  Умеренно перегрет шортами'
            else:
                interpretation = '✅ Нейтральный — рынок сбалансирован'

            return rate, interpretation
    except Exception as e:
        pass

    return None, '⚠️  Funding Rate недоступен (тестовая сеть)'

# ── ЗАПУСК ──────────────────────────────────────────────────
symbol = 'BTCUSDT'
df     = get_candles(symbol, '1h', 100)

volume_data, volume_conclusion, current_ratio = analyze_volume(df)
funding_rate, funding_interpretation          = get_funding_rate(symbol)

print("\n" + "=" * 50)
print("     📊 АНАЛИЗ ОБЪЁМА И FUNDING RATE")
print("=" * 50)

print("\n  📦 Последние 5 свечей:")
print(f"  {'Время':<22} {'Цена':>10} {'Объём':>12} {'Ratio':>7} {'':>5}")
for r in volume_data:
    print(
        f"  {str(r['time']):<22} "
        f"{r['close']:>10.2f} "
        f"{r['volume']:>12.2f} "
        f"{r['volume_ratio']:>7.2f}x "
        f"{r['direction']} {r['anomaly']}"
    )

print(f"\n  📈 Вывод по объёму:")
print(f"     {volume_conclusion}")
print(f"     Текущий объём: {current_ratio:.2f}x от среднего")

print(f"\n  💰 Funding Rate:")
if funding_rate is not None:
    print(f"     Значение: {funding_rate:.4f}%")
print(f"     {funding_interpretation}")

print("\n" + "=" * 50)
