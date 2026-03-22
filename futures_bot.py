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
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
TELEGRAM_TOKEN     = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')

BINANCE_KEY        = os.getenv('BINANCE_API_KEY')
BINANCE_SECRET     = os.getenv('BINANCE_API_SECRET')

LEVERAGE           = int(os.getenv('FUTURES_LEVERAGE', 3))
TRADE_AMOUNT_USDT  = float(os.getenv('TRADE_AMOUNT_USDT', 500))
RISK_PER_TRADE     = float(os.getenv('RISK_PER_TRADE', 0.02))
STOP_LOSS_PCT      = 1.5
TAKE_PROFIT_PCT    = 3.0
TRAILING_STOP_PCT  = 2.0
PARTIAL_CLOSE_PCT  = 50

DEFAULT_SYMBOLS    = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
POSITIONS_FILE     = 'data/active_positions.json'
EXCHANGE_CACHE     = {}

# ── ИНИЦИАЛИЗАЦИЯ ────────────────────────────────────────────

data_client = UMFutures(
    key=BINANCE_KEY,
    secret=BINANCE_SECRET,
    base_url="https://fapi.binance.com"
)

def update_exchange_cache():
    global EXCHANGE_CACHE
    try:
        log("🔄 Обновляем кэш параметров биржи...")
        info = data_client.exchange_info()
        for s in info['symbols']:
            EXCHANGE_CACHE[s['symbol']] = s
        log(f"✅ Кэш обновлен ({len(EXCHANGE_CACHE)} пар)")
    except Exception as e:
        log(f"❌ Ошибка обновления кэша: {e}")

def get_lot_precision(symbol):
    if symbol in EXCHANGE_CACHE:
        for f in EXCHANGE_CACHE[symbol]['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step = float(f['stepSize'])
                return len(str(step).rstrip('0').split('.')[-1])
    return 3

def get_price_precision(symbol):
    if symbol in EXCHANGE_CACHE:
        for f in EXCHANGE_CACHE[symbol]['filters']:
            if f['filterType'] == 'PRICE_FILTER':
                tick = float(f['tickSize'])
                return len(str(tick).rstrip('0').split('.')[-1])
    return 2

# ── СОХРАНЕНИЕ СОСТОЯНИЯ ──────────────────────────────────────

def save_positions(positions):
    try:
        with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"❌ Ошибка сохранения позиций: {e}")

def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log(f"❌ Ошибка загрузки позиций: {e}")
    return {}

# ── УТИЛИТЫ ──────────────────────────────────────────────────

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    if not os.path.exists('logs'): os.makedirs('logs')
    with open('logs/futures_bot.log', 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown"
        }, timeout=5)
    except Exception:
        pass

def get_active_symbols():
    asset_file = 'data/futures_active.txt'
    if os.path.exists(asset_file):
        with open(asset_file, 'r') as f:
            symbols = [s.strip() for s in f.readlines() if s.strip()]
        if symbols:
            return symbols
    return DEFAULT_SYMBOLS

# ── РЕАЛЬНЫЕ РЫНОЧНЫЕ ДАННЫЕ ─────────────────────────────────

def get_price(symbol):
    """Текущая цена с реального Binance."""
    try:
        return float(data_client.ticker_price(symbol=symbol)['price'])
    except Exception as e:
        log(f"Ошибка цены {symbol}: {e}")
        return None

def get_candles(symbol, interval='15m', limit=200):
    """Свечи с реального Binance."""
    try:
        raw = data_client.klines(symbol=symbol, interval=interval, limit=limit)
        df  = pd.DataFrame(raw, columns=[
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
        log(f"Ошибка свечей {symbol}: {e}")
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
        trend, desc = 'BULLISH', 'Бычий тренд (HH/HL)'
    elif lh < ph and ll < pl:
        trend, desc = 'BEARISH', 'Медвежий тренд (LH/LL)'
    else:
        trend, desc = 'NEUTRAL', 'Нейтральная структура'
    return {'trend': trend, 'description': desc,
            'last_high': lh, 'last_low': ll,
            'prev_high': ph, 'prev_low': pl}

def find_liquidity_zones(highs, lows, current_price):
    resistance = sorted([h['price'] for h in highs if h['price'] > current_price])[:3]
    support    = sorted([l['price'] for l in lows  if l['price'] < current_price], reverse=True)[:3]
    return {'resistance': resistance, 'support': support}

def analyze_volume(df):
    df            = df.copy()
    df['avg_vol'] = df['volume'].rolling(20).mean()
    df['ratio']   = df['volume'] / df['avg_vol']
    return df['ratio'].iloc[-1]

# ── AI-ВАЛИДАТОР ─────────────────────────────────────────────

def validate_trade(symbol, direction, entry, stop, take, structure, volume_ratio):
    if direction == 'LONG':
        risk, reward = entry - stop, take - entry
    else:
        risk, reward = stop - entry, entry - take
    rr = reward / risk if risk > 0 else 0

    prompt = f"""
Ты профессиональный фьючерсный трейдер. Оцени сделку. Ответь ТОЛЬКО JSON.
СДЕЛКА: {direction} {symbol}
Вход: {entry:.5f} | Стоп: {stop:.5f} | Тейк: {take:.5f}
Риск: {(risk/entry*100):.2f}% | Прибыль: {(reward/entry*100):.2f}% | R:R=1:{rr:.2f}
Тренд: {structure['trend']} | Объём: {volume_ratio:.2f}x
{{"verdict":"APPROVE"/"REJECT","confidence":0-100,"main_reason":"причина","risks":["риск1"]}}
Только JSON.
"""
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": "anthropic/claude-3-haiku",
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1},
            timeout=30
        )
        content = resp.json()['choices'][0]['message']['content']
        return json.loads(content), rr
    except Exception as e:
        log(f"AI недоступен: {e}")
        return None, rr

# ── PAPER TRADING ОПЕРАЦИИ ───────────────────────────────────

def paper_open(symbol, direction, entry_price, stop, take):
    """
    Симулируем открытие позиции.
    Никаких реальных ордеров — только запись в память и статистику.
    """
    price_prec = get_price_precision(symbol)
    lot_prec   = get_lot_precision(symbol)

    risk_usdt  = TRADE_AMOUNT_USDT * RISK_PER_TRADE
    stop_pct   = abs(entry_price - stop) / entry_price
    # Увеличиваем лимит использования депозита до 50% на сделку
    size_usdt  = min(risk_usdt / stop_pct, TRADE_AMOUNT_USDT * 0.5)
    quantity   = round(size_usdt / entry_price, lot_prec)
    stop_r     = round(stop, price_prec)
    take_r     = round(take, price_prec)

    if direction == 'LONG':
        rr = round((take - entry_price) / (entry_price - stop), 2) if (entry_price - stop) > 0 else 0
    else:
        rr = round((entry_price - take) / (stop - entry_price), 2) if (stop - entry_price) > 0 else 0

    trade_id = record_open(symbol, direction, entry_price, stop, take, size_usdt, LEVERAGE)
    dir_icon = "🟢" if direction == 'LONG' else "🔴"

    log(f"{dir_icon} PAPER {direction} {symbol}: {quantity} @ {entry_price:.5f} | SL:{stop_r} TP:{take_r}")

    send_telegram(
        f"{dir_icon} *{direction} ОТКРЫТ (Paper)*\n"
        f"{'─' * 28}\n"
        f"📊 Пара:     *{symbol}*\n"
        f"💵 Вход:     `{entry_price:.5f}`\n"
        f"🛑 Стоп:     `{stop_r}` ✅ бот отслеживает\n"
        f"🎯 Тейк:     `{take_r}` ✅ бот отслеживает\n"
        f"{'─' * 28}\n"
        f"📦 Размер:   `${size_usdt:.2f}` x{LEVERAGE}\n"
        f"⚠️ Риск:     `${risk_usdt:.2f}` ({RISK_PER_TRADE*100:.0f}%)\n"
        f"📐 R:R:      `1:{rr}`\n"
        f"🔄 Трейлинг: `{TRAILING_STOP_PCT}%` после тейка\n"
        f"📡 Данные:   реальный Binance"
    )

    return True, quantity, trade_id

def paper_close(symbol, pos, reason, price, positions):
    """
    Симулируем закрытие позиции.
    Записываем результат в статистику.
    """
    direction = pos['direction']

    if direction == 'LONG':
        pnl_pct  = (price - pos['entry']) / pos['entry'] * 100
        pnl_usdt = pos['size_usdt'] * (pnl_pct / 100) * LEVERAGE
    else:
        pnl_pct  = (pos['entry'] - price) / pos['entry'] * 100
        pnl_usdt = pos['size_usdt'] * (pnl_pct / 100) * LEVERAGE

    record_close(pos['trade_id'], price, reason)
    save_positions(positions)
    stats_text = format_stats_telegram()

    reason_labels = {
        'SL':      ('🛑', 'УБЫТОК', 'Стоп-лосс'),
        'TP':      ('🎯', 'ПРИБЫЛЬ', 'Тейк-профит'),
        'TRAIL':   ('🔄', 'ПРИБЫЛЬ', 'Трейлинг стоп'),
        'PARTIAL': ('🎯', 'ПРИБЫЛЬ', '50% зафиксировано'),
    }
    icon, result, reason_text = reason_labels.get(reason, ('📌', 'ЗАКРЫТО', reason))
    dir_icon = "🟢" if direction == 'LONG' else "🔴"

    log(f"{icon} {reason} {symbol}: @ {price:.5f} | P&L {pnl_pct:+.2f}% ({pnl_usdt:+.2f} USDT)")

    send_telegram(
        f"{icon} *{result} — {symbol}*\n"
        f"{'─' * 28}\n"
        f"{dir_icon} Направление: *{direction}*\n"
        f"📝 Причина:   {reason_text}\n"
        f"💵 Вход:      `{pos['entry']:.5f}`\n"
        f"🏁 Выход:     `{price:.5f}`\n"
        f"🛑 Стоп был:  `{pos['stop']:.5f}`\n"
        f"🎯 Тейк был:  `{pos['take']:.5f}`\n"
        f"{'─' * 28}\n"
        f"{'📈' if pnl_pct > 0 else '📉'} P&L: `{pnl_pct:+.2f}%` / `{pnl_usdt:+.2f} USDT`\n\n"
        f"{stats_text}"
    )

# ── ПОИСК СЕТАПА ─────────────────────────────────────────────

def find_setup(symbol, df_15m, df_1h):
    highs_1h, lows_1h   = find_swing_points(df_1h, window=10)
    structure_1h         = analyze_structure(highs_1h, lows_1h)
    current_price        = df_15m['close'].iloc[-1]
    highs_15m, lows_15m = find_swing_points(df_15m, window=5)
    liquidity_15m        = find_liquidity_zones(highs_15m, lows_15m, current_price)
    volume_ratio         = analyze_volume(df_15m)

    support    = liquidity_15m['support']
    resistance = liquidity_15m['resistance']
    if not support or not resistance:
        return None

    nearest_sup = support[0]
    nearest_res = resistance[0]
    dist_sup    = (current_price - nearest_sup)  / current_price * 100
    dist_res    = (nearest_res  - current_price) / current_price * 100

    if dist_sup <= 1.5 and structure_1h['trend'] == 'BULLISH' and volume_ratio >= 0.8:
        entry = current_price
        stop  = nearest_sup * (1 - STOP_LOSS_PCT / 100)
        take  = entry * (1 + TAKE_PROFIT_PCT / 100)
        return {'direction': 'LONG', 'entry': entry, 'stop': stop, 'take': take,
                'structure': structure_1h, 'volume': volume_ratio,
                'reason': f'Поддержка {nearest_sup:.5f}, бычий 1H, объём {volume_ratio:.2f}x'}

    if dist_res <= 1.5 and structure_1h['trend'] == 'BEARISH' and volume_ratio >= 0.8:
        entry = current_price
        stop  = nearest_res * (1 + STOP_LOSS_PCT / 100)
        take  = entry * (1 - TAKE_PROFIT_PCT / 100)
        return {'direction': 'SHORT', 'entry': entry, 'stop': stop, 'take': take,
                'structure': structure_1h, 'volume': volume_ratio,
                'reason': f'Сопротивление {nearest_res:.5f}, медвежий 1H, объём {volume_ratio:.2f}x'}

    return None

# ── УПРАВЛЕНИЕ ПОЗИЦИЕЙ ───────────────────────────────────────

def manage_position(symbol, pos, positions):
    """
    Управляем позицией используя РЕАЛЬНЫЕ цены с Binance.
    SL/TP срабатывают точно — проверяем каждые 3 секунды.
    Никаких реальных ордеров не размещается.
    """
    price = get_price(symbol)
    if price is None:
        return

    direction = pos['direction']
    entry     = pos['entry']

    # ── СТОП-ЛОСС ────────────────────────────────────────────
    sl_hit = (direction == 'LONG'  and price <= pos['stop']) or \
             (direction == 'SHORT' and price >= pos['stop'])

    if sl_hit and not pos['trailing_active']:
        del positions[symbol]
        paper_close(symbol, pos, 'SL', price, positions)
        return

    # ── ТРЕЙЛИНГ СТОП (активен после частичного закрытия) ────
    if pos['trailing_active']:
        if direction == 'LONG':
            if price > pos['max_price']:
                pos['max_price']     = price
                pos['trailing_stop'] = price * (1 - TRAILING_STOP_PCT / 100)
                log(f"   📈 {symbol} новый макс: {price:.5f} | трейлинг: {pos['trailing_stop']:.5f}")
                save_positions(positions)

            if price <= pos['trailing_stop']:
                del positions[symbol]
                paper_close(symbol, pos, 'TRAIL', price, positions)
                return

        else:  # SHORT
            if price < pos['min_price']:
                pos['min_price']     = price
                pos['trailing_stop'] = price * (1 + TRAILING_STOP_PCT / 100)
                log(f"   📉 {symbol} новый мин: {price:.5f} | трейлинг: {pos['trailing_stop']:.5f}")
                save_positions(positions)

            if price >= pos['trailing_stop']:
                del positions[symbol]
                paper_close(symbol, pos, 'TRAIL', price, positions)
                return

        # Показываем P&L при трейлинге
        pnl = (price - entry) / entry * 100 if direction == 'LONG' else (entry - price) / entry * 100
        log(f"🔄 {symbol} TRAIL: P&L {pnl:+.2f}% | цена {price:.5f} | трейлинг {pos['trailing_stop']:.5f}")
        return

    # ── ТЕЙК-ПРОФИТ (частичное закрытие + активация трейлинга)
    tp_hit = (direction == 'LONG'  and price >= pos['take']) or \
             (direction == 'SHORT' and price <= pos['take'])

    if tp_hit and not pos['partial_closed']:
        pnl_pct = (price - entry) / entry * 100 if direction == 'LONG' else (entry - price) / entry * 100
        dir_icon = "🟢" if direction == 'LONG' else "🔴"

        log(f"🎯 Тейк {symbol}! P&L {pnl_pct:+.2f}% — активируем трейлинг")

        # Активируем трейлинг (симулируем частичное закрытие)
        pos['partial_closed']  = True
        pos['trailing_active'] = True
        pos['max_price']       = price
        pos['min_price']       = price

        if direction == 'LONG':
            pos['trailing_stop'] = price * (1 - TRAILING_STOP_PCT / 100)
        else:
            pos['trailing_stop'] = price * (1 + TRAILING_STOP_PCT / 100)

        save_positions(positions)

        send_telegram(
            f"🎯 *Тейк-профит — {symbol}*\n"
            f"{'─' * 28}\n"
            f"{dir_icon} {direction} *{symbol}*\n"
            f"💵 Вход:       `{entry:.5f}`\n"
            f"🏁 Тейк:       `{price:.5f}` ({pnl_pct:+.2f}%)\n"
            f"{'─' * 28}\n"
            f"🔄 *Трейлинг активирован*\n"
            f"🛑 Трейлинг:   `{pos['trailing_stop']:.5f}`\n"
            f"📐 Отступ:     `{TRAILING_STOP_PCT}%` от максимума\n"
            f"💡 Стоп тянется за ценой автоматически"
        )
        return

    # Показываем текущий P&L
    pnl = (price - entry) / entry * 100 if direction == 'LONG' else (entry - price) / entry * 100
    log(f"📍 {symbol} {direction}: P&L {pnl:+.2f}% | цена {price:.5f}")

# ── ГЛАВНЫЙ ЦИКЛ ─────────────────────────────────────────────

def run():
    # Инициализация параметров биржи
    update_exchange_cache()

    # Загружаем позиции из файла (Paper Trading Persistence)
    positions = load_positions()

    log("=" * 55)
    log("📄 CryptoAutoPro PAPER TRADING запущен")
    if positions:
        log(f"   Загружено активных позиций: {len(positions)}")
    log(f"   Данные:   РЕАЛЬНЫЙ Binance Futures")
    log(f"   Депозит:  ${TRADE_AMOUNT_USDT} USDT (виртуальный)")
    log(f"   Плечо:    x{LEVERAGE}")
    log(f"   SL: {STOP_LOSS_PCT}% | TP: {TAKE_PROFIT_PCT}% | Трейлинг: {TRAILING_STOP_PCT}%")
    log("=" * 55)

    send_telegram(
        "📄 *CryptoAutoPro PAPER TRADING*\n"
        f"{'─' * 28}\n"
        f"📡 Данные: *реальный Binance*\n"
        f"💰 Депозит: `${TRADE_AMOUNT_USDT}` USDT (виртуальный)\n"
        f"⚙️ Плечо: `x{LEVERAGE}`\n"
        f"🛑 SL: `{STOP_LOSS_PCT}%` | 🎯 TP: `{TAKE_PROFIT_PCT}%`\n"
        f"🔄 Трейлинг: `{TRAILING_STOP_PCT}%` после тейка\n"
        f"{'─' * 28}\n"
        f"✅ SL/TP срабатывают по реальным ценам!"
    )

    while True:
        try:
            symbols = get_active_symbols()

            for symbol in symbols:

                # ── Управляем открытой позицией ──────────────
                if symbol in positions:
                    manage_position(symbol, positions[symbol], positions)
                    continue

                # ── Ищем новый сетап ─────────────────────────
                if len(positions) >= 3:
                    continue

                df_15m = get_candles(symbol, '15m', 200)
                df_1h  = get_candles(symbol, '1h',  200)
                if df_15m is None or df_1h is None:
                    continue

                setup = find_setup(symbol, df_15m, df_1h)
                if not setup:
                    log(f"⏳ {symbol}: нет сетапа")
                    time.sleep(0.5)
                    continue

                log(f"🔍 Сетап {setup['direction']} {symbol}: {setup['reason']}")

                analysis, rr = validate_trade(
                    symbol=symbol, direction=setup['direction'],
                    entry=setup['entry'], stop=setup['stop'], take=setup['take'],
                    structure=setup['structure'], volume_ratio=setup['volume']
                )

                # ── ПРОВЕРКА ОДОБРЕНИЯ ───────────────────────
                ai_approved = analysis is not None and analysis.get('verdict') == 'APPROVE'
                conf_ok     = analysis.get('confidence', 0) >= 65 if analysis else False
                rr_ok       = rr >= 1.2  # Снижаем порог до 1.2 для гибкости

                if ai_approved and conf_ok and rr_ok:
                    log(f"   ✅ AI одобрил ({analysis.get('confidence')}%, R:R 1:{rr:.2f})")
                else:
                    if not ai_approved:
                        reason = analysis.get('main_reason', 'нет данных') if analysis else 'AI недоступен'
                        log(f"   ❌ AI отклонил: {reason}")
                    elif not conf_ok:
                        log(f"   ⚠️ Низкая уверенность AI: {analysis.get('confidence')}%")
                    elif not rr_ok:
                        log(f"   📉 Плохой R:R: 1:{rr:.2f} (нужно минимум 1.2)")
                    
                    time.sleep(0.5)
                    continue

                # Симулируем открытие
                ok, qty, trade_id = paper_open(
                    symbol, setup['direction'],
                    setup['entry'], setup['stop'], setup['take']
                )

                lot_prec = get_lot_precision(symbol)
                risk_usdt  = TRADE_AMOUNT_USDT * RISK_PER_TRADE
                stop_pct   = abs(setup['entry'] - setup['stop']) / setup['entry']
                size_usdt  = min(risk_usdt / stop_pct, TRADE_AMOUNT_USDT * 0.2)

                if ok:
                    positions[symbol] = {
                        'direction':       setup['direction'],
                        'quantity':        qty,
                        'trade_id':        trade_id,
                        'entry':           setup['entry'],
                        'stop':            setup['stop'],
                        'take':            setup['take'],
                        'size_usdt':       size_usdt,
                        # Трейлинг стоп
                        'max_price':       setup['entry'],
                        'min_price':       setup['entry'],
                        'trailing_active': False,
                        'partial_closed':  False,
                        'trailing_stop':   None,
                    }
                    save_positions(positions)

                time.sleep(1)

        except Exception as e:
            log(f"❌ Ошибка главного цикла: {e}")

        log(f"⏱️  Следующий цикл через 3 сек... (позиций: {len(positions)})")
        log("-" * 55)
        time.sleep(3)   # проверяем каждые 3 сек — SL/TP не пропустим

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log("🛑 Бот остановлен пользователем (SIGINT)")
    except Exception as e:
        log(f"💥 Критическая ошибка при запуске: {e}")
