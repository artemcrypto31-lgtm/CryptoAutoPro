from binance.um_futures import UMFutures
from dotenv import load_dotenv
import pandas as pd
import requests
import json
import os
import time
from datetime import datetime
from trade_stats import record_open, record_close, format_stats_telegram

load_dotenv()

# ── НАСТРОЙКИ ────────────────────────────────────────────────
FUTURES_API_KEY    = os.getenv('FUTURES_API_KEY')
FUTURES_API_SECRET = os.getenv('FUTURES_API_SECRET')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
TELEGRAM_TOKEN     = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')

LEVERAGE           = int(os.getenv('FUTURES_LEVERAGE', 3))
TRADE_AMOUNT_USDT  = float(os.getenv('TRADE_AMOUNT_USDT', 500))
RISK_PER_TRADE     = float(os.getenv('RISK_PER_TRADE', 0.02))  # 2% от депо
STOP_LOSS_PCT      = 1.5    # стоп 1.5%
TAKE_PROFIT_PCT    = 3.0    # тейк 3.0% (соотношение 1:2)

# Список активов (обновляется сканером)
DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']

client = UMFutures(
    key=FUTURES_API_KEY,
    secret=FUTURES_API_SECRET,
    base_url="https://testnet.binancefuture.com"
)

# ── УТИЛИТЫ ──────────────────────────────────────────────────

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open('logs/futures_bot.log', 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def send_telegram(message):
    """Отправляем уведомление в Telegram"""
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown"
        }, timeout=5)
    except Exception:
        pass

def get_active_symbols():
    """Загружаем список активов от сканера или используем дефолтный"""
    asset_file = 'data/active_assets.txt'
    if os.path.exists(asset_file):
        with open(asset_file, 'r') as f:
            symbols = [s.strip() for s in f.readlines() if s.strip()]
        if symbols:
            return symbols
    return DEFAULT_SYMBOLS

# ── РЫНОЧНЫЕ ДАННЫЕ ──────────────────────────────────────────

def get_candles(symbol, interval='15m', limit=200):
    """
    Получаем свечи с фьючерсного рынка.
    Используем 15-минутный таймфрейм для скальпинга
    и 1-часовой для свинга.
    """
    try:
        raw = client.klines(
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
    except Exception as e:
        log(f"❌ Ошибка получения свечей {symbol}: {e}")
        return None

# ── СТРУКТУРА РЫНКА ──────────────────────────────────────────

def find_swing_points(df, window=10):
    highs, lows = [], []
    for i in range(window, len(df) - window):
        lh = df['high'].iloc[i-window:i]
        rh = df['high'].iloc[i+1:i+window+1]
        ll = df['low'].iloc[i-window:i]
        rl = df['low'].iloc[i+1:i+window+1]
        if df['high'].iloc[i] > lh.max() and df['high'].iloc[i] > rh.max():
            highs.append({'index': i, 'price': df['high'].iloc[i]})
        if df['low'].iloc[i] < ll.min() and df['low'].iloc[i] < rl.min():
            lows.append({'index': i, 'price': df['low'].iloc[i]})
    return highs, lows

def analyze_structure(highs, lows):
    if len(highs) < 2 or len(lows) < 2:
        return {'trend': 'NEUTRAL', 'description': 'Недостаточно данных',
                'last_high': None, 'last_low': None,
                'prev_high': None, 'prev_low': None}
    lh = highs[-1]['price']
    ph = highs[-2]['price']
    ll = lows[-1]['price']
    pl = lows[-2]['price']
    if lh > ph and ll > pl:
        trend = 'BULLISH'
        desc  = 'Бычий тренд (HH/HL)'
    elif lh < ph and ll < pl:
        trend = 'BEARISH'
        desc  = 'Медвежий тренд (LH/LL)'
    else:
        trend = 'NEUTRAL'
        desc  = 'Нейтральная структура'
    return {
        'trend': trend, 'description': desc,
        'last_high': lh, 'last_low': ll,
        'prev_high': ph, 'prev_low': pl
    }

def find_liquidity_zones(highs, lows, current_price):
    resistance = sorted(
        [h['price'] for h in highs if h['price'] > current_price]
    )[:3]
    support = sorted(
        [l['price'] for l in lows if l['price'] < current_price],
        reverse=True
    )[:3]
    return {'resistance': resistance, 'support': support}

# ── ОБЪЁМ ────────────────────────────────────────────────────

def analyze_volume(df):
    df            = df.copy()
    df['avg_vol'] = df['volume'].rolling(20).mean()
    df['ratio']   = df['volume'] / df['avg_vol']
    return df['ratio'].iloc[-1]

# ── AI-ВАЛИДАТОР ─────────────────────────────────────────────

def validate_trade(symbol, direction, entry, stop, take,
                   structure, volume_ratio, current_price):
    if direction == 'LONG':
        risk   = entry - stop
        reward = take - entry
    else:
        risk   = stop - entry
        reward = entry - take

    rr = reward / risk if risk > 0 else 0

    prompt = f"""
Ты — профессиональный фьючерсный трейдер.
Оцени сделку и ответь ТОЛЬКО в JSON.

СДЕЛКА: {direction} {symbol}
Вход: {entry:.4f} | Стоп: {stop:.4f} | Тейк: {take:.4f}
Риск: {(risk/entry*100):.2f}% | Прибыль: {(reward/entry*100):.2f}% | R:R = 1:{rr:.2f}
Тренд: {structure['trend']} | Объём: {volume_ratio:.2f}x

{{"verdict":"APPROVE"/"REJECT","confidence":0-100,"main_reason":"причина","risks":["риск1"]}}
Только JSON.
"""
    try:
        resp    = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json"
            },
            json={
                "model":       "anthropic/claude-3-haiku",
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.1
            },
            timeout=30
        )
        content = resp.json()['choices'][0]['message']['content']
        return json.loads(content), rr
    except Exception as e:
        log(f"⚠️ AI-валидатор недоступен: {e}")
        return None, rr

# ── ТОРГОВЫЕ ОПЕРАЦИИ ────────────────────────────────────────

def set_leverage(symbol):
    """Устанавливаем плечо для символа"""
    try:
        client.change_leverage(
            symbol=symbol,
            leverage=LEVERAGE
        )
    except Exception as e:
        log(f"⚠️ Ошибка установки плеча {symbol}: {e}")

def get_precision(symbol):
    """Получаем точность количества для символа"""
    try:
        info = client.exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step = float(f['stepSize'])
                        precision = len(
                            str(step).rstrip('0').split('.')[-1]
                        )
                        return precision
    except:
        pass
    return 3

def get_balance():
    """Получаем свободный баланс USDT"""
    try:
        account = client.account()
        for asset in account['assets']:
            if asset['asset'] == 'USDT':
                return float(asset['availableBalance'])
    except Exception as e:
        log(f"❌ Ошибка получения баланса: {e}")
    return 0.0

def open_position(symbol, direction, entry_price, stop, take):
    """Открываем позицию на фьючерсном рынке"""
    try:
        set_leverage(symbol)

        # Размер позиции = риск на сделку / размер стопа
        balance     = get_balance()
        risk_usdt   = TRADE_AMOUNT_USDT * RISK_PER_TRADE
        stop_pct    = abs(entry_price - stop) / entry_price
        size_usdt   = min(
            risk_usdt / stop_pct,
            TRADE_AMOUNT_USDT * 0.2   # не более 20% депо за раз
        )
        precision   = get_precision(symbol)
        quantity    = round(size_usdt / entry_price, precision)

        side = 'BUY' if direction == 'LONG' else 'SELL'

        order = client.new_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity
        )

        log(f"{'🟢' if direction == 'LONG' else '🔴'} "
            f"{direction} {symbol}: {quantity} контрактов "
            f"по {entry_price:.4f}")
        log(f"   SL: {stop:.4f} | TP: {take:.4f} | "
            f"Размер: ${size_usdt:.2f} | Плечо: x{LEVERAGE}")

        # Записываем в статистику
        trade_id = record_open(
            symbol, direction, entry_price,
            stop, take, size_usdt, LEVERAGE
        )

        # Уведомление в Telegram
        send_telegram(
            f"{'🟢' if direction == 'LONG' else '🔴'} "
            f"*{direction} {symbol}*\n"
            f"Вход: `{entry_price:.4f}`\n"
            f"Стоп: `{stop:.4f}`\n"
            f"Тейк: `{take:.4f}`\n"
            f"Размер: `${size_usdt:.2f}` x{LEVERAGE}\n"
            f"Риск: `${risk_usdt:.2f}`"
        )

        return order, quantity, trade_id

    except Exception as e:
        log(f"❌ Ошибка открытия позиции {symbol}: {e}")
        return None, None, None

def close_position(symbol, direction, quantity, trade_id, reason):
    """Закрываем позицию"""
    try:
        side  = 'SELL' if direction == 'LONG' else 'BUY'
        price = float(client.ticker_price(symbol=symbol)['price'])

        client.new_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity,
            reduceOnly=True
        )

        record_close(trade_id, price, reason)
        stats_text = format_stats_telegram()

        icon = "🎯" if reason == 'TP' else "🛑"
        log(f"{icon} {reason} {symbol}: закрыто по {price:.4f}")

        send_telegram(
            f"{icon} *{reason} — {symbol}*\n"
            f"Закрыто по: `{price:.4f}`\n\n"
            f"{stats_text}"
        )

        return price

    except Exception as e:
        log(f"❌ Ошибка закрытия {symbol}: {e}")
        return None

# ── ПОИСК СЕТАПА ─────────────────────────────────────────────

def find_setup(symbol, df_15m, df_1h):
    """
    Ищем торговый сетап на двух таймфреймах.

    Логика:
    - 1H определяет ТРЕНД (старший таймфрейм)
    - 15M определяет ТОЧКУ ВХОДА (младший таймфрейм)

    Вход только когда оба таймфрейма согласованы.
    """
    # Анализ на 1H (тренд)
    highs_1h, lows_1h   = find_swing_points(df_1h, window=10)
    structure_1h         = analyze_structure(highs_1h, lows_1h)
    current_price        = df_15m['close'].iloc[-1]

    # Анализ на 15M (точка входа)
    highs_15m, lows_15m  = find_swing_points(df_15m, window=5)
    liquidity_15m        = find_liquidity_zones(
        highs_15m, lows_15m, current_price
    )
    volume_ratio         = analyze_volume(df_15m)

    support    = liquidity_15m['support']
    resistance = liquidity_15m['resistance']

    if not support or not resistance:
        return None

    nearest_sup = support[0]
    nearest_res = resistance[0]

    dist_sup = (current_price - nearest_sup)  / current_price * 100
    dist_res = (nearest_res  - current_price) / current_price * 100

    # ── LONG сетап ───────────────────────────────────────────
    # Цена у поддержки + бычий тренд на 1H + объём
    if (dist_sup <= 1.5
            and structure_1h['trend'] == 'BULLISH'
            and volume_ratio >= 0.8):

        entry = current_price
        stop  = nearest_sup * (1 - STOP_LOSS_PCT / 100)
        take  = entry * (1 + TAKE_PROFIT_PCT / 100)

        return {
            'direction':  'LONG',
            'entry':      entry,
            'stop':       stop,
            'take':       take,
            'structure':  structure_1h,
            'volume':     volume_ratio,
            'reason':     f'Цена у поддержки {nearest_sup:.2f}, '
                          f'бычий тренд 1H, объём {volume_ratio:.2f}x'
        }

    # ── SHORT сетап ──────────────────────────────────────────
    # Цена у сопротивления + медвежий тренд на 1H + объём
    if (dist_res <= 1.5
            and structure_1h['trend'] == 'BEARISH'
            and volume_ratio >= 0.8):

        entry = current_price
        stop  = nearest_res * (1 + STOP_LOSS_PCT / 100)
        take  = entry * (1 - TAKE_PROFIT_PCT / 100)

        return {
            'direction':  'SHORT',
            'entry':      entry,
            'stop':       stop,
            'take':       take,
            'structure':  structure_1h,
            'volume':     volume_ratio,
            'reason':     f'Цена у сопротивления {nearest_res:.2f}, '
                          f'медвежий тренд 1H, объём {volume_ratio:.2f}x'
        }

    return None

# ── ГЛАВНЫЙ ЦИКЛ ─────────────────────────────────────────────

def run():
    # Активные позиции: symbol → {direction, quantity, trade_id, stop, take}
    positions = {}

    log("=" * 55)
    log("🤖 CryptoAutoPro FUTURES запущен")
    log(f"   Депозит:  ${TRADE_AMOUNT_USDT} USDT (виртуальный)")
    log(f"   Плечо:    x{LEVERAGE}")
    log(f"   Риск/сд:  {RISK_PER_TRADE*100:.0f}% от депо")
    log(f"   SL/TP:    {STOP_LOSS_PCT}% / {TAKE_PROFIT_PCT}%")
    log("=" * 55)

    send_telegram(
        "🤖 *CryptoAutoPro FUTURES запущен*\n"
        f"Депозит: `${TRADE_AMOUNT_USDT}` USDT\n"
        f"Плечо: `x{LEVERAGE}`\n"
        f"Риск на сделку: `{RISK_PER_TRADE*100:.0f}%`\n"
        f"SL: `{STOP_LOSS_PCT}%` | TP: `{TAKE_PROFIT_PCT}%`"
    )

    while True:
        try:
            symbols = get_active_symbols()

            for symbol in symbols:
                # ── Проверяем открытую позицию ───────────────
                if symbol in positions:
                    pos   = positions[symbol]
                    price = float(
                        client.ticker_price(symbol=symbol)['price']
                    )

                    # Take-Profit
                    if (pos['direction'] == 'LONG'  and price >= pos['take'] or
                        pos['direction'] == 'SHORT' and price <= pos['take']):
                        close_position(
                            symbol, pos['direction'],
                            pos['quantity'], pos['trade_id'], 'TP'
                        )
                        del positions[symbol]

                    # Stop-Loss
                    elif (pos['direction'] == 'LONG'  and price <= pos['stop'] or
                          pos['direction'] == 'SHORT' and price >= pos['stop']):
                        close_position(
                            symbol, pos['direction'],
                            pos['quantity'], pos['trade_id'], 'SL'
                        )
                        del positions[symbol]

                    else:
                        if pos['direction'] == 'LONG':
                            pnl = (price - pos['entry']) / pos['entry'] * 100
                        else:
                            pnl = (pos['entry'] - price) / pos['entry'] * 100
                        log(f"📍 {symbol} {pos['direction']}: "
                            f"P&L {pnl:+.2f}% | "
                            f"цена {price:.4f}")

                    continue

                # ── Ищем новый сетап ─────────────────────────
                if len(positions) >= 3:
                    continue   # максимум 3 одновременные позиции

                df_15m = get_candles(symbol, '15m', 200)
                df_1h  = get_candles(symbol, '1h',  200)

                if df_15m is None or df_1h is None:
                    continue

                setup = find_setup(symbol, df_15m, df_1h)

                if not setup:
                    log(f"⏳ {symbol}: нет сетапа")
                    time.sleep(0.5)
                    continue

                log(f"🔍 {symbol}: найден сетап {setup['direction']}")
                log(f"   {setup['reason']}")

                # ── AI-валидация ──────────────────────────────
                analysis, rr = validate_trade(
                    symbol        = symbol,
                    direction     = setup['direction'],
                    entry         = setup['entry'],
                    stop          = setup['stop'],
                    take          = setup['take'],
                    structure     = setup['structure'],
                    volume_ratio  = setup['volume'],
                    current_price = setup['entry']
                )

                # Проверяем вердикт
                approved = (
                    analysis is not None
                    and analysis.get('verdict') == 'APPROVE'
                    and analysis.get('confidence', 0) >= 65
                    and rr >= 1.8
                )

                if not approved:
                    reason = analysis.get('main_reason', 'нет данных') \
                             if analysis else 'AI недоступен'
                    log(f"   ❌ Отклонено: {reason}")
                    time.sleep(0.5)
                    continue

                log(f"   ✅ AI одобрил (уверенность: "
                    f"{analysis.get('confidence')}%, R:R 1:{rr:.2f})")

                # ── Открываем позицию ─────────────────────────
                order, qty, trade_id = open_position(
                    symbol,
                    setup['direction'],
                    setup['entry'],
                    setup['stop'],
                    setup['take']
                )

                if order:
                    positions[symbol] = {
                        'direction': setup['direction'],
                        'quantity':  qty,
                        'trade_id':  trade_id,
                        'entry':     setup['entry'],
                        'stop':      setup['stop'],
                        'take':      setup['take']
                    }

                time.sleep(1)

        except Exception as e:
            log(f"❌ Ошибка главного цикла: {e}")

        log(f"⏱️  Следующий цикл через 60 сек... "
            f"(активных позиций: {len(positions)})")
        log("-" * 55)
        time.sleep(60)

run()
