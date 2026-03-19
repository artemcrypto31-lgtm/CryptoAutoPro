from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import os
import time

load_dotenv()

client = Client(
    api_key=os.getenv('BINANCE_API_KEY'),
    api_secret=os.getenv('BINANCE_API_SECRET'),
    testnet=True
)

server_time = client.get_server_time()
client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)

# ── НАСТРОЙКИ ФИЛЬТРОВ ───────────────────────────────────────
MIN_VOLUME_USDT   = 100_000_000   # минимум $100M объёма за 24ч
MIN_TRADES        = 1_000_000     # минимум 1M сделок за 24ч
MAX_BTC_CORR      = 0.7           # максимальная корреляция с BTC
TOP_N             = 5             # сколько активов отбираем

def get_btc_returns(limit=100):
    """Получаем доходности BTC для расчёта корреляции"""
    raw = client.get_klines(
        symbol='BTCUSDT',
        interval='1h',
        limit=limit
    )
    closes = [float(x[4]) for x in raw]
    returns = pd.Series(closes).pct_change().dropna()
    return returns

def calculate_correlation(symbol, btc_returns, limit=100):
    """
    Считаем корреляцию монеты с BTC.
    
    Корреляция от -1 до +1:
    +1.0 = двигается точно как BTC
     0.0 = независимое движение
    -1.0 = двигается противоположно BTC
    
    Нам нужны монеты с корреляцией < 0.7
    — они имеют собственную жизнь
    """
    try:
        raw = client.get_klines(
            symbol=symbol,
            interval='1h',
            limit=limit
        )
        closes  = [float(x[4]) for x in raw]
        returns = pd.Series(closes).pct_change().dropna()

        # Выравниваем длины
        min_len = min(len(returns), len(btc_returns))
        corr    = returns.iloc[-min_len:].corr(
            btc_returns.iloc[-min_len:]
        )
        return round(corr, 3)
    except:
        return 1.0  # если ошибка — считаем максимальную корреляцию

def scan_markets():
    """
    Главная функция сканера.
    Проходим по всем USDT парам и отбираем лучшие.
    """
    print("🔍 Сканируем рынок Binance...")
    print(f"   Фильтры: объём > ${MIN_VOLUME_USDT/1e6:.0f}M | "
          f"сделок > {MIN_TRADES/1e6:.0f}M | "
          f"корреляция BTC < {MAX_BTC_CORR}")

    # Получаем статистику за 24 часа по всем парам
    tickers = client.get_ticker()

    # Оставляем только USDT пары
    usdt_pairs = [
        t for t in tickers
        if t['symbol'].endswith('USDT')
        and not t['symbol'].endswith('DOWNUSDT')
        and not t['symbol'].endswith('UPUSDT')
        and t['symbol'] != 'USDCUSDT'
    ]

    print(f"   Найдено USDT пар: {len(usdt_pairs)}")

    # Применяем фильтры объёма и сделок
    candidates = []
    for t in usdt_pairs:
        volume_usdt = float(t['quoteVolume'])   # объём в USDT
        trades      = int(t['count'])            # количество сделок
        price_chg   = float(t['priceChangePercent'])

        if volume_usdt >= MIN_VOLUME_USDT and trades >= MIN_TRADES:
            candidates.append({
                'symbol':     t['symbol'],
                'volume':     volume_usdt,
                'trades':     trades,
                'price_chg':  price_chg,
                'price':      float(t['lastPrice'])
            })

    print(f"   Прошли фильтр объёма: {len(candidates)} пар")

    if not candidates:
        print("⚠️  Нет пар прошедших фильтр. Снижаем порог...")
        # На тестнете меньше данных — снижаем порог
        for t in usdt_pairs:
            volume_usdt = float(t['quoteVolume'])
            trades      = int(t['count'])
            if volume_usdt > 0 and trades > 0:
                candidates.append({
                    'symbol':    t['symbol'],
                    'volume':    volume_usdt,
                    'trades':    trades,
                    'price_chg': float(t['priceChangePercent']),
                    'price':     float(t['lastPrice'])
                })
        # Берём топ-20 по объёму
        candidates = sorted(
            candidates,
            key=lambda x: x['volume'],
            reverse=True
        )[:20]

    # Получаем доходности BTC для корреляции
    print("   Считаем корреляцию с BTC...")
    btc_returns = get_btc_returns()

    # Считаем корреляцию для каждого кандидата
    results = []
    for c in candidates:
        if c['symbol'] == 'BTCUSDT':
            continue

        corr = calculate_correlation(c['symbol'], btc_returns)
        c['btc_correlation'] = corr

        # Фильтр корреляции
        if corr <= MAX_BTC_CORR:
            results.append(c)

        time.sleep(0.1)  # пауза чтобы не перегрузить API

    print(f"   Прошли фильтр корреляции: {len(results)} пар")

    # Сортируем по объёму и берём топ
    results = sorted(results, key=lambda x: x['volume'], reverse=True)
    top     = results[:TOP_N]

    return top

def print_results(assets):
    """Красиво выводим результаты сканирования"""
    print("\n" + "=" * 60)
    print("     🎯 ЛУЧШИЕ АКТИВЫ ДЛЯ ТОРГОВЛИ")
    print("=" * 60)

    if not assets:
        print("  ⚠️  Активов не найдено. Проверь параметры фильтров.")
        return

    for i, a in enumerate(assets, 1):
        corr_icon = "🟢" if a['btc_correlation'] < 0.5 else "🟡"
        chg_icon  = "📈" if a['price_chg'] > 0 else "📉"

        print(f"\n  #{i} {a['symbol']}")
        print(f"      Цена:        ${a['price']:.4f}")
        print(f"      Объём 24ч:   ${a['volume']/1e6:.1f}M USDT")
        print(f"      Сделок 24ч:  {a['trades']/1e6:.2f}M")
        print(f"      Изменение:   {chg_icon} {a['price_chg']:+.2f}%")
        print(f"      Корр. BTC:   {corr_icon} {a['btc_correlation']:.3f}")

    print("\n" + "=" * 60)

    # Сохраняем список для использования ботом
    symbols = [a['symbol'] for a in assets]
    with open('data/active_assets.txt', 'w') as f:
        f.write('\n'.join(symbols))
    print(f"  💾 Список сохранён в data/active_assets.txt")
    print("=" * 60)

# ── ЗАПУСК ──────────────────────────────────────────────────
assets = scan_markets()
print_results(assets)
