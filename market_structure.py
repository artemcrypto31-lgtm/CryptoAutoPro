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

def get_candles(symbol, interval, limit=200):
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

def find_swing_points(df, window=10):
    """
    Находим свинг-хаи и свинг-лоу.

    Свинг-хай — это свеча у которой HIGH выше чем
    у 'window' свечей слева и справа. Это локальный максимум.

    Свинг-лоу — это свеча у которой LOW ниже чем
    у 'window' свечей слева и справа. Это локальный минимум.

    Именно между этими точками и живёт структура рынка.
    """
    highs = []
    lows = []

    for i in range(window, len(df) - window):
        left_highs  = df['high'].iloc[i - window:i]
        right_highs = df['high'].iloc[i + 1:i + window + 1]
        left_lows   = df['low'].iloc[i - window:i]
        right_lows  = df['low'].iloc[i + 1:i + window + 1]

        if df['high'].iloc[i] > left_highs.max() and df['high'].iloc[i] > right_highs.max():
            highs.append({
                'index': i,
                'time':  df['time'].iloc[i],
                'price': df['high'].iloc[i]
            })

        if df['low'].iloc[i] < left_lows.min() and df['low'].iloc[i] < right_lows.min():
            lows.append({
                'index': i,
                'time':  df['time'].iloc[i],
                'price': df['low'].iloc[i]
            })

    return highs, lows

def analyze_market_structure(highs, lows):
    """
    Определяем структуру рынка по последним свинг-точкам.

    БЫЧЬЯ структура  = каждый максимум ВЫШЕ предыдущего
                       И каждый минимум ВЫШЕ предыдущего
                       (Higher High + Higher Low = HH/HL)

    МЕДВЕЖЬЯ структура = каждый максимум НИЖЕ предыдущего
                         И каждый минимум НИЖЕ предыдущего
                         (Lower High + Lower Low = LH/LL)

    СЛОМ СТРУКТУРЫ (BOS) = цена пробила последний значимый максимум
                           или минимум — тренд меняется
    """
    structure = {
        'trend':     'NEUTRAL',
        'bos':       False,       # Break of Structure — слом структуры
        'last_high': None,
        'last_low':  None,
        'prev_high': None,
        'prev_low':  None,
        'description': ''
    }

    if len(highs) < 2 or len(lows) < 2:
        structure['description'] = 'Недостаточно данных для анализа структуры'
        return structure

    # Берём два последних максимума и два последних минимума
    last_high = highs[-1]['price']
    prev_high = highs[-2]['price']
    last_low  = lows[-1]['price']
    prev_low  = lows[-2]['price']

    structure['last_high'] = last_high
    structure['last_low']  = last_low
    structure['prev_high'] = prev_high
    structure['prev_low']  = prev_low

    # Определяем тренд
    hh = last_high > prev_high   # Higher High — новый максимум выше
    hl = last_low  > prev_low    # Higher Low  — новый минимум выше
    lh = last_high < prev_high   # Lower High  — новый максимум ниже
    ll = last_low  < prev_low    # Lower Low   — новый минимум ниже

    if hh and hl:
        structure['trend'] = 'BULLISH'
        structure['description'] = (
            f'📈 Бычий тренд (HH/HL): '
            f'максимум {last_high:.0f} > {prev_high:.0f}, '
            f'минимум {last_low:.0f} > {prev_low:.0f}'
        )
    elif lh and ll:
        structure['trend'] = 'BEARISH'
        structure['description'] = (
            f'📉 Медвежий тренд (LH/LL): '
            f'максимум {last_high:.0f} < {prev_high:.0f}, '
            f'минимум {last_low:.0f} < {prev_low:.0f}'
        )
    else:
        structure['trend'] = 'NEUTRAL'
        structure['description'] = (
            f'⚖️  Нейтральная структура: '
            f'нет чёткого тренда'
        )

    # Проверяем слом структуры (Break of Structure)
    # Если был медвежий тренд но последний максимум выше — это BOS вверх
    # Если был бычий тренд но последний минимум ниже — это BOS вниз
    if lh and hl:
        structure['bos'] = True
        structure['description'] += ' ⚡ СЛОМ СТРУКТУРЫ ВВЕРХ!'
    elif hh and ll:
        structure['bos'] = True
        structure['description'] += ' ⚡ СЛОМ СТРУКТУРЫ ВНИЗ!'

    return structure

def find_liquidity_zones(highs, lows, df):
    """
    Находим зоны ликвидности — места где сидят стоп-лоссы большинства.

    Логика проста:
    - Выше каждого свинг-хая сидят стоп-лоссы продавцов
    - Ниже каждого свинг-лоу сидят стоп-лоссы покупателей

    Именно туда умные деньги двигают цену перед разворотом.
    Мы торгуем ПОСЛЕ того как ликвидность собрана.
    """
    current_price = df['close'].iloc[-1]

    # Ближайшие зоны выше текущей цены (там стопы шортистов)
    resistance_zones = sorted(
        [h['price'] for h in highs if h['price'] > current_price]
    )[:3]

    # Ближайшие зоны ниже текущей цены (там стопы лонгистов)
    support_zones = sorted(
        [l['price'] for l in lows if l['price'] < current_price],
        reverse=True
    )[:3]

    return {
        'resistance': resistance_zones,
        'support':    support_zones,
        'current':    current_price
    }

# ── ЗАПУСК ──────────────────────────────────────────────────
symbol = 'BTCUSDT'
df = get_candles(symbol, '1h', 200)

highs, lows = find_swing_points(df, window=10)
structure   = analyze_market_structure(highs, lows)
liquidity   = find_liquidity_zones(highs, lows, df)

print("\n" + "=" * 50)
print("     🏗️  СТРУКТУРА РЫНКА BTCUSDT")
print("=" * 50)
print(f"\n  Текущая цена:   {liquidity['current']:.2f} USDT")
print(f"\n  {structure['description']}")
print(f"\n  Последний хай:  {structure['last_high']:.2f}")
print(f"  Предыдущий хай: {structure['prev_high']:.2f}")
print(f"  Последний лоу:  {structure['last_low']:.2f}")
print(f"  Предыдущий лоу: {structure['prev_low']:.2f}")

print(f"\n  🎯 Зоны ликвидности ВЫШЕ (стопы шортистов):")
for z in liquidity['resistance']:
    dist = ((z - liquidity['current']) / liquidity['current']) * 100
    print(f"     {z:.2f} USDT  (+{dist:.2f}%)")

print(f"\n  🎯 Зоны ликвидности НИЖЕ (стопы лонгистов):")
for z in liquidity['support']:
    dist = ((liquidity['current'] - z) / liquidity['current']) * 100
    print(f"     {z:.2f} USDT  (-{dist:.2f}%)")

print("\n" + "=" * 50)
