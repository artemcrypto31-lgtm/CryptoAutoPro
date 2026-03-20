from binance.um_futures import UMFutures
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime

load_dotenv()

client = UMFutures(
    key=os.getenv('FUTURES_API_KEY'),
    secret=os.getenv('FUTURES_API_SECRET'),
    base_url="https://testnet.binancefuture.com"
)

# ── НАСТРОЙКИ ФИЛЬТРОВ ───────────────────────────────────────
MIN_VOLUME_USDT  = 100_000_000   # минимум $100M объёма за 24ч
MIN_TRADES       = 750_000       # минимум 500K сделок за 24ч
MIN_VOLATILITY   = 1.5           # минимум 1% диапазон за 24ч
MIN_NATR         = 1.0          # минимум NATR 1.5%
MAX_BTC_CORR     = 0.75          # максимальная корреляция с BTC
PREFILTER_TOP    = 30            # берём топ-50 по объёму до корреляции
TOP_N            = 10            # финальный топ пар
SCAN_INTERVAL    = 30 * 60       # пересканируем каждые 30 минут
ACTIVE_FILE      = 'data/futures_active.txt'

EXCLUDED = {
    'USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'BUSDUSDT',
    'DAIUSDT',  'USDTUSDT',  'BTCDOMUSDT'
}

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")

def get_all_futures_symbols():
    """Получаем все активные USDT фьючерсы"""
    try:
        info    = client.exchange_info()
        symbols = [
            s['symbol'] for s in info['symbols']
            if s['symbol'].endswith('USDT')
            and s['status'] == 'TRADING'
            and s['symbol'] not in EXCLUDED
            and 'BEAR' not in s['symbol']
            and 'BULL' not in s['symbol']
            and 'DOWN' not in s['symbol']
            and 'UP'   not in s['symbol']
        ]
        return symbols
    except Exception as e:
        log(f"Ошибка получения символов: {e}")
        return []

def get_24h_stats():
    """Получаем статистику за 24ч по всем парам одним запросом"""
    try:
        tickers = client.ticker_24hr_price_change()
        stats   = {}
        for t in tickers:
            if t['symbol'].endswith('USDT'):
                high  = float(t['highPrice'])
                low   = float(t['lowPrice'])
                price = float(t['lastPrice'])
                vol   = (high - low) / price * 100 if price > 0 else 0
                stats[t['symbol']] = {
                    'volume':     float(t['quoteVolume']),
                    'trades':     int(t['count']),
                    'price_chg':  float(t['priceChangePercent']),
                    'high':       high,
                    'low':        low,
                    'price':      price,
                    'volatility': vol
                }
        return stats
    except Exception as e:
        log(f"Ошибка статистики: {e}")
        return {}

def calculate_atr_natr(symbol, period=14):
    """
    Считаем ATR и NATR по последним свечам.

    ATR (Average True Range) — средний истинный диапазон.
    Истинный диапазон = максимум из:
      1. High - Low текущей свечи
      2. |High - Close предыдущей свечи|
      3. |Low  - Close предыдущей свечи|

    NATR = ATR / Close * 100  (в процентах)
    Это нормализованный ATR — удобен для сравнения разных монет.
    NATR > 1.5% означает что монета движется достаточно для торговли.
    """
    try:
        raw    = client.klines(symbol=symbol, interval='1h', limit=period+5)
        df     = pd.DataFrame(raw, columns=[
            'time','open','high','low','close','volume',
            'ct','qv','trades','tbb','tbq','ignore'
        ])
        df['high']  = df['high'].astype(float)
        df['low']   = df['low'].astype(float)
        df['close'] = df['close'].astype(float)

        # Считаем True Range
        df['prev_close'] = df['close'].shift(1)
        df['tr'] = df[['high','low','prev_close']].apply(
            lambda r: max(
                r['high'] - r['low'],
                abs(r['high'] - r['prev_close']) if pd.notna(r['prev_close']) else 0,
                abs(r['low']  - r['prev_close']) if pd.notna(r['prev_close']) else 0
            ), axis=1
        )

        atr  = df['tr'].tail(period).mean()
        natr = atr / df['close'].iloc[-1] * 100

        return round(atr, 6), round(natr, 3)

    except:
        return 0, 0

def get_btc_returns():
    """Получаем доходности BTC для корреляции"""
    try:
        raw     = client.klines(symbol='BTCUSDT', interval='1h', limit=48)
        closes  = [float(x[4]) for x in raw]
        returns = pd.Series(closes).pct_change().dropna()
        return returns
    except:
        return None

def calculate_correlation(symbol, btc_returns):
    """Считаем корреляцию с BTC за 48 часов"""
    try:
        raw     = client.klines(symbol=symbol, interval='1h', limit=48)
        closes  = [float(x[4]) for x in raw]
        returns = pd.Series(closes).pct_change().dropna()
        min_len = min(len(returns), len(btc_returns))

        if min_len < 10:
            return 1.0

        r1   = returns.iloc[-min_len:].values
        r2   = btc_returns.iloc[-min_len:].values
        std1 = np.std(r1)
        std2 = np.std(r2)

        if std1 == 0 or std2 == 0:
            return 1.0

        corr = np.corrcoef(r1, r2)[0][1]
        return round(float(corr), 3) if not np.isnan(corr) else 1.0
    except:
        return 1.0

def score_symbol(data):
    """
    Итоговый балл пары для ранжирования.
    Учитывает объём, NATR и независимость от BTC.
    """
    vol_score  = min(data['volume']  / 2_000_000_000, 1.0) * 40
    natr_score = min(data['natr']    / 5.0,           1.0) * 40
    corr_score = (1 - abs(data['btc_corr']))               * 20
    return round(vol_score + natr_score + corr_score, 2)

def scan():
    """Главная функция сканера"""
    log("=" * 55)
    log("🔍 Запускаем сканер фьючерсов...")

    # Шаг 1: Все символы
    all_symbols = get_all_futures_symbols()
    log(f"   Всего пар: {len(all_symbols)}")

    # Шаг 2: Статистика за 24ч
    stats_24h = get_24h_stats()

    # Шаг 3: Фильтр объёма, сделок, волатильности
    candidates = []
    for symbol in all_symbols:
        if symbol not in stats_24h:
            continue
        s = stats_24h[symbol]

        # На тестнете данные меньше — адаптивный порог
        vol_ok    = s['volume']     >= MIN_VOLUME_USDT * 0.01
        trades_ok = s['trades']     >= MIN_TRADES      * 0.01
        vlt_ok    = s['volatility'] >= MIN_VOLATILITY  * 0.1

        if vol_ok and trades_ok and vlt_ok:
            candidates.append({'symbol': symbol, **s})

    # Сортируем по объёму и берём топ-50 для корреляции
    candidates = sorted(
        candidates,
        key=lambda x: x['volume'],
        reverse=True
    )[:PREFILTER_TOP]

    log(f"   После предфильтра: {len(candidates)} пар")
    log(f"   Считаем ATR/NATR и корреляцию...")

    # Шаг 4: ATR/NATR + корреляция для топ-50
    btc_returns = get_btc_returns()
    results     = []

    for i, c in enumerate(candidates):
        symbol = c['symbol']

        if symbol == 'BTCUSDT':
            atr, natr = calculate_atr_natr(symbol)
            c['atr']      = atr
            c['natr']     = natr
            c['btc_corr'] = 1.0
            c['score']    = 100.0
            results.append(c)
            continue

        # ATR и NATR
        atr, natr = calculate_atr_natr(symbol)
        c['atr']  = atr
        c['natr'] = natr

        # Корреляция
        corr = calculate_correlation(symbol, btc_returns) \
               if btc_returns is not None else 0.5
        c['btc_corr'] = corr

        # Финальные фильтры
        natr_ok = natr >= MIN_NATR * 0.1   # адаптивный для тестнета
        corr_ok = corr <= MAX_BTC_CORR

        if natr_ok and corr_ok:
            c['score'] = score_symbol(c)
            results.append(c)

        time.sleep(0.15)

        # Прогресс каждые 10 пар
        if (i + 1) % 10 == 0:
            log(f"   Обработано: {i+1}/{len(candidates)}")

    # Шаг 5: Финальный топ
    top = sorted(results, key=lambda x: x['score'], reverse=True)[:TOP_N]

    log(f"   Прошли все фильтры: {len(results)} пар")
    log(f"   Финальный топ: {len(top)} пар")

    return top

def save_active_symbols(top):
    """Сохраняем список в файл"""
    symbols = [t['symbol'] for t in top]
    with open(ACTIVE_FILE, 'w') as f:
        f.write('\n'.join(symbols))
    return symbols

def load_active_symbols():
    """Загружаем список из файла"""
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE, 'r') as f:
            symbols = [l.strip() for l in f.readlines() if l.strip()]
        if symbols:
            return symbols
    return ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'DOGEUSDT', 'SOLUSDT']

def print_results(top):
    """Выводим результаты"""
    print("\n" + "=" * 70)
    print("        🎯 ТОП ФЬЮЧЕРСНЫХ ПАР ДЛЯ ТОРГОВЛИ")
    print("=" * 70)
    print(f"  {'#':<3} {'Символ':<12} {'Объём 24ч':>11} "
          f"{'Сделок':>9} {'NATR':>6} {'Корр':>6} {'Балл':>6}")
    print("-" * 70)

    for i, t in enumerate(top, 1):
        vol_str    = f"${t['volume']/1e6:.0f}M"
        trades_str = f"{t['trades']/1e3:.0f}K"
        chg_icon   = "📈" if t['price_chg'] > 0 else "📉"
        corr_icon  = "🟢" if t['btc_corr'] < 0.5 else "🟡"

        print(
            f"  {i:<3} {t['symbol']:<12} {vol_str:>11} "
            f"{trades_str:>9} {t['natr']:>5.2f}% "
            f"{corr_icon}{t['btc_corr']:>5.2f} "
            f"{t['score']:>6.1f} {chg_icon}"
        )

    print("=" * 70)

# ── ЗАПУСК ──────────────────────────────────────────────────
if __name__ == "__main__":
    while True:
        top     = scan()
        symbols = save_active_symbols(top)
        print_results(top)
        print(f"\n✅ Сохранено {len(symbols)} пар в {ACTIVE_FILE}")
        print(f"   Следующий скан через {SCAN_INTERVAL//60} минут")
        time.sleep(SCAN_INTERVAL)