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
RISK_PER_TRADE     = float(os.getenv('RISK_PER_TRADE', 0.02))
STOP_LOSS_PCT      = 1.5
TAKE_PROFIT_PCT    = 3.0

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

# ── РЫНОЧНЫЕ ДАННЫЕ ──────────────────────────────────────────

def get_candles(symbol, interval='15m', limit=200):
    try:
        raw = client.klines(symbol=symbol, interval=interval, limit=limit)
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
        log(f"Ошибка получения свечей {symbol}: {e}")
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

def validate_trade(symbol, direction, entry, stop, take, structure, volume_ratio, current_price):
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
        resp    = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": "anthropic/claude-3-haiku", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=30
        )
        content = resp.json()['choices'][0]['message']['content']
        return json.loads(content), rr
    except Exception as e:
        log(f"AI недоступен: {e}")
        return None, rr

# ── ТОРГОВЫЕ ОПЕРАЦИИ ────────────────────────────────────────

def set_leverage(symbol):
    try:
        client.change_leverage(symbol=symbol, leverage=LEVERAGE)
    except Exception as e:
        log(f"Ошибка плеча {symbol}: {e}")

def get_lot_precision(symbol):
    try:
        info = client.exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step = float(f['stepSize'])
                        return len(str(step).rstrip('0').split('.')[-1])
    except:
        pass
    return 3

def get_price_precision(symbol):
    try:
        info = client.exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'PRICE_FILTER':
                        tick = float(f['tickSize'])
                        return len(str(tick).rstrip('0').split('.')[-1])
    except:
        pass
    return 2

def get_balance():
    try:
        account = client.account()
        for asset in account['assets']:
            if asset['asset'] == 'USDT':
                return float(asset['availableBalance'])
    except Exception as e:
        log(f"Ошибка баланса: {e}")
    return 0.0

def get_real_position(symbol):
    try:
        positions = client.get_position_risk(symbol=symbol)
        for p in positions:
            if p['symbol'] == symbol:
                return float(p['positionAmt'])
    except:
        pass
    return 0.0

def cancel_open_orders(symbol):
    try:
        client.cancel_open_orders(symbol=symbol)
        log(f"   Ордера {symbol} отменены")
    except Exception as e:
        log(f"   Ошибка отмены ордеров {symbol}: {e}")

def open_position(symbol, direction, entry_price, stop, take):
    """Открываем позицию с детальным уведомлением в Telegram."""
    try:
        set_leverage(symbol)

        risk_usdt    = TRADE_AMOUNT_USDT * RISK_PER_TRADE
        stop_pct     = abs(entry_price - stop) / entry_price
        size_usdt    = min(risk_usdt / stop_pct, TRADE_AMOUNT_USDT * 0.2)
        lot_prec     = get_lot_precision(symbol)
        price_prec   = get_price_precision(symbol)
        quantity     = round(size_usdt / entry_price, lot_prec)
        stop_r       = round(stop, price_prec)
        take_r       = round(take, price_prec)
        side         = 'BUY'  if direction == 'LONG' else 'SELL'
        close_side   = 'SELL' if direction == 'LONG' else 'BUY'

        # R:R для уведомления
        if direction == 'LONG':
            rr = round((take - entry_price) / (entry_price - stop), 2) if (entry_price - stop) > 0 else 0
        else:
            rr = round((entry_price - take) / (stop - entry_price), 2) if (stop - entry_price) > 0 else 0

        # Шаг 1 — рыночный ордер
        order = client.new_order(symbol=symbol, side=side, type='MARKET', quantity=quantity)
        log(f"{'LONG' if direction=='LONG' else 'SHORT'} {symbol}: {quantity} @ {entry_price:.5f}")
        time.sleep(0.5)

        # Шаг 2 — SL
        sl_ok = False
        try:
            client.new_order(symbol=symbol, side=close_side, type='STOP_MARKET',
                           stopPrice=stop_r, closePosition='true', timeInForce='GTE_GTC')
            log(f"   SL: {stop_r}")
            sl_ok = True
        except Exception as e:
            log(f"   SL ошибка: {e}")

        # Шаг 3 — TP
        tp_ok = False
        try:
            client.new_order(symbol=symbol, side=close_side, type='TAKE_PROFIT_MARKET',
                           stopPrice=take_r, closePosition='true', timeInForce='GTE_GTC')
            log(f"   TP: {take_r}")
            tp_ok = True
        except Exception as e:
            log(f"   TP ошибка: {e}")

        trade_id  = record_open(symbol, direction, entry_price, stop, take, size_usdt, LEVERAGE)
        dir_icon  = "🟢" if direction == 'LONG' else "🔴"
        sl_status = "✅ биржа" if sl_ok else "⚠️ только бот"
        tp_status = "✅ биржа" if tp_ok else "⚠️ только бот"

        # Детальное уведомление об открытии
        send_telegram(
            f"{dir_icon} *{direction} ОТКРЫТ*\n"
            f"{'─' * 28}\n"
            f"📊 Пара:     *{symbol}*\n"
            f"💵 Вход:     `{entry_price:.5f}`\n"
            f"🛑 Стоп:     `{stop_r}` {sl_status}\n"
            f"🎯 Тейк:     `{take_r}` {tp_status}\n"
            f"{'─' * 28}\n"
            f"📦 Размер:   `${size_usdt:.2f}` x{LEVERAGE}\n"
            f"⚠️ Риск:     `${risk_usdt:.2f}` ({RISK_PER_TRADE*100:.0f}%)\n"
            f"📐 R:R:      `1:{rr}`"
        )

        return order, quantity, trade_id

    except Exception as e:
        log(f"Ошибка открытия {symbol}: {e}")
        return None, None, None

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

# ── ВОССТАНОВЛЕНИЕ ПОЗИЦИЙ ───────────────────────────────────

def restore_positions():
    """При старте читаем открытые позиции с биржи."""
    restored = {}
    try:
        all_positions = client.get_position_risk()
        for p in all_positions:
            amt = float(p['positionAmt'])
            if amt == 0:
                continue

            symbol    = p['symbol']
            direction = 'LONG' if amt > 0 else 'SHORT'
            entry     = float(p['entryPrice'])
            quantity  = abs(amt)
            mark      = float(p['markPrice'])
            pnl       = float(p['unRealizedProfit'])

            stop = entry * (1 - STOP_LOSS_PCT / 100) if direction == 'LONG' else entry * (1 + STOP_LOSS_PCT / 100)
            take = entry * (1 + TAKE_PROFIT_PCT / 100) if direction == 'LONG' else entry * (1 - TAKE_PROFIT_PCT / 100)

            from trade_stats import load_history
            history  = load_history()
            open_ids = [t['id'] for t in history if t['status'] == 'OPEN' and t['symbol'] == symbol]
            trade_id = open_ids[-1] if open_ids else record_open(symbol, direction, entry, stop, take, quantity * entry, LEVERAGE)

            restored[symbol] = {
                'direction': direction, 'quantity': quantity,
                'trade_id': trade_id, 'entry': entry,
                'stop': stop, 'take': take
            }

            dir_icon = "🟢" if direction == 'LONG' else "🔴"
            pnl_icon = "📈" if pnl >= 0 else "📉"
            log(f"Восстановлена: {direction} {symbol} @ {entry:.5f}")

            send_telegram(
                f"♻️ *Восстановлена позиция*\n"
                f"{'─' * 28}\n"
                f"{dir_icon} *{direction} {symbol}*\n"
                f"💵 Вход:     `{entry:.5f}`\n"
                f"📍 Текущая:  `{mark:.5f}`\n"
                f"🛑 Стоп:     `{stop:.5f}`\n"
                f"🎯 Тейк:     `{take:.5f}`\n"
                f"{pnl_icon} PnL:     `{pnl:+.4f} USDT`"
            )

    except Exception as e:
        log(f"Ошибка восстановления позиций: {e}")

    log(f"Восстановлено позиций: {len(restored)}")
    return restored

# ── ГЛАВНЫЙ ЦИКЛ ─────────────────────────────────────────────

def run():
    positions = restore_positions()

    log("=" * 55)
    log("CryptoAutoPro FUTURES запущен")
    log(f"   Депозит:  ${TRADE_AMOUNT_USDT} USDT")
    log(f"   Плечо:    x{LEVERAGE}")
    log(f"   Риск/сд:  {RISK_PER_TRADE*100:.0f}% от депо")
    log(f"   SL/TP:    {STOP_LOSS_PCT}% / {TAKE_PROFIT_PCT}%")
    log("=" * 55)

    send_telegram(
        "🤖 *CryptoAutoPro FUTURES запущен*\n"
        f"Депозит: `${TRADE_AMOUNT_USDT}` USDT\n"
        f"Плечо: `x{LEVERAGE}`\n"
        f"Риск: `{RISK_PER_TRADE*100:.0f}%` | SL: `{STOP_LOSS_PCT}%` | TP: `{TAKE_PROFIT_PCT}%`"
    )

    while True:
        try:
            symbols = get_active_symbols()

            for symbol in symbols:

                # ── Проверяем открытую позицию ───────────────
                if symbol in positions:
                    pos      = positions[symbol]
                    real_amt = get_real_position(symbol)

                    if real_amt == 0:
                        # Позиция закрыта биржей
                        price = float(client.ticker_price(symbol=symbol)['price'])

                        if pos['direction'] == 'LONG':
                            pnl_pct = (price - pos['entry']) / pos['entry'] * 100
                        else:
                            pnl_pct = (pos['entry'] - price) / pos['entry'] * 100

                        reason   = 'TP' if pnl_pct > 0 else 'SL'
                        icon     = "🎯" if reason == 'TP' else "🛑"
                        result   = "ПРИБЫЛЬ" if reason == 'TP' else "УБЫТОК"
                        dir_icon = "🟢" if pos['direction'] == 'LONG' else "🔴"

                        cancel_open_orders(symbol)
                        record_close(pos['trade_id'], price, reason)
                        stats_text = format_stats_telegram()

                        log(f"{icon} {reason} {symbol}: закрыто @ {price:.5f} | P&L {pnl_pct:+.2f}%")

                        # Детальное уведомление о закрытии
                        send_telegram(
                            f"{icon} *{result} — {symbol}*\n"
                            f"{'─' * 28}\n"
                            f"{dir_icon} Направление: *{pos['direction']}*\n"
                            f"💵 Вход:      `{pos['entry']:.5f}`\n"
                            f"🏁 Выход:     `{price:.5f}`\n"
                            f"🛑 Стоп был:  `{pos['stop']:.5f}`\n"
                            f"🎯 Тейк был:  `{pos['take']:.5f}`\n"
                            f"{'─' * 28}\n"
                            f"{'📈' if reason=='TP' else '📉'} P&L: `{pnl_pct:+.2f}%`\n\n"
                            f"{stats_text}"
                        )
                        del positions[symbol]
                        continue

                    # Позиция открыта — показываем P&L
                    price = float(client.ticker_price(symbol=symbol)['price'])
                    if pos['direction'] == 'LONG':
                        pnl = (price - pos['entry']) / pos['entry'] * 100
                    else:
                        pnl = (pos['entry'] - price) / pos['entry'] * 100
                    log(f"📍 {symbol} {pos['direction']}: P&L {pnl:+.2f}% | цена {price:.5f}")
                    continue

                # ── Ищем новый сетап ─────────────────────────
                if len(positions) >= 3:
                    continue

                real_amt = get_real_position(symbol)
                if real_amt != 0:
                    log(f"⚠️ {symbol}: уже есть позиция на бирже, пропускаем")
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

                log(f"Сетап {setup['direction']} {symbol}: {setup['reason']}")

                analysis, rr = validate_trade(
                    symbol=symbol, direction=setup['direction'],
                    entry=setup['entry'], stop=setup['stop'], take=setup['take'],
                    structure=setup['structure'], volume_ratio=setup['volume'],
                    current_price=setup['entry']
                )

                approved = (
                    analysis is not None
                    and analysis.get('verdict') == 'APPROVE'
                    and analysis.get('confidence', 0) >= 65
                    and rr >= 1.8
                )

                if not approved:
                    reason = analysis.get('main_reason', 'нет данных') if analysis else 'AI недоступен'
                    log(f"   Отклонено: {reason}")
                    time.sleep(0.5)
                    continue

                log(f"   AI одобрил (уверенность: {analysis.get('confidence')}%, R:R 1:{rr:.2f})")

                order, qty, trade_id = open_position(
                    symbol, setup['direction'], setup['entry'], setup['stop'], setup['take']
                )

                if order:
                    positions[symbol] = {
                        'direction': setup['direction'], 'quantity': qty,
                        'trade_id': trade_id, 'entry': setup['entry'],
                        'stop': setup['stop'], 'take': setup['take']
                    }

                time.sleep(1)

        except Exception as e:
            log(f"Ошибка главного цикла: {e}")

        log(f"Следующий цикл через 10 сек... (позиций: {len(positions)})")
        log("-" * 55)
        time.sleep(10)

run()
