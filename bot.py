from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import requests
import json
import ta
import os
import time
from datetime import datetime

load_dotenv()

API_KEY            = os.getenv('BINANCE_API_KEY')
API_SECRET         = os.getenv('BINANCE_API_SECRET')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
MAX_TRADE_USDT     = float(os.getenv('MAX_TRADE_AMOUNT_USDT', 10))
STOP_LOSS_PCT      = float(os.getenv('STOP_LOSS_PERCENT', 2))

client = Client(
    api_key=API_KEY,
    api_secret=API_SECRET,
    testnet=True
)

# ── УТИЛИТЫ ─────────────────────────────────────────────────

def sync_time():
    server_time = client.get_server_time()
    client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open('logs/bot.log', 'a', encoding='utf-8') as f:
        f.write(line + '\n')

# ── ДАННЫЕ ──────────────────────────────────────────────────

def get_candles(symbol, interval, limit=200):
    raw = client.get_klines(
        symbol=symbol,
        interval=interval,
        limit=limit
    )
    df = pd.DataFrame(raw, columns=[
        'time','open','high','low','close','volume',
        'close_time','quote_volume','trades',
        'taker_buy_base','taker_buy_quote','ignore'
    ])
    df = df[['time','open','high','low','close','volume']]
    for col in ['open','high','low','close','volume']:
        df[col] = df[col].astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    return df

# ── СТРУКТУРА РЫНКА ─────────────────────────────────────────

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
        desc  = f'📈 Бычий тренд (HH/HL)'
    elif lh < ph and ll < pl:
        trend = 'BEARISH'
        desc  = f'📉 Медвежий тренд (LH/LL)'
    else:
        trend = 'NEUTRAL'
        desc  = '⚖️  Нейтральная структура'
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

# ── ОБЪЁМ ───────────────────────────────────────────────────

def analyze_volume(df):
    df = df.copy()
    df['avg_volume']    = df['volume'].rolling(20).mean()
    df['volume_ratio']  = df['volume'] / df['avg_volume']
    last                = df.iloc[-1]
    last3               = df.tail(3)
    price_up  = last3['close'].iloc[-1] > last3['close'].iloc[0]
    volume_up = last3['volume'].iloc[-1] > last3['volume'].iloc[0]
    if price_up and volume_up:
        conclusion = 'Цена↑ + Объём↑ = сильное движение вверх'
    elif price_up and not volume_up:
        conclusion = 'Цена↑ + Объём↓ = слабый рост'
    elif not price_up and volume_up:
        conclusion = 'Цена↓ + Объём↑ = сильное давление продавцов'
    else:
        conclusion = 'Цена↓ + Объём↓ = вялое движение'
    return last['volume_ratio'], conclusion

def get_funding_rate(symbol='BTCUSDT'):
    try:
        url      = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
        response = requests.get(url, timeout=5)
        data     = response.json()
        if data and isinstance(data, list):
            return float(data[0]['fundingRate']) * 100
    except:
        pass
    return None

# ── AI-ВАЛИДАТОР ────────────────────────────────────────────

def validate_trade(symbol, direction, entry, stop, take,
                   structure, volume_ratio, funding_rate, current_price):
    if direction == 'LONG':
        risk   = entry - stop
        reward = take - entry
    else:
        risk   = stop - entry
        reward = entry - take

    rr       = reward / risk if risk > 0 else 0
    risk_pct = (risk / entry) * 100
    rew_pct  = (reward / entry) * 100

    prompt = f"""
Ты — профессиональный трейдер с 20-летним опытом на криптовалютном рынке.
Оцени потенциальную сделку и вынеси вердикт.

СДЕЛКА:
- Инструмент: {symbol}
- Направление: {direction}
- Текущая цена: {current_price:.2f} USDT
- Вход: {entry:.2f} | Стоп: {stop:.2f} | Тейк: {take:.2f}

МАТЕМАТИКА:
- Риск: {risk:.2f} USDT ({risk_pct:.2f}%)
- Прибыль: {reward:.2f} USDT ({rew_pct:.2f}%)
- R:R = 1:{rr:.2f}

РЫНОК:
- Тренд: {structure['trend']}
- Структура: {structure['description']}
- Объём: {volume_ratio:.2f}x от среднего
- Funding Rate: {funding_rate}%

Минимально приемлемый R:R = 1:2
Ответь СТРОГО в JSON:
{{
  "verdict": "APPROVE" или "REJECT",
  "confidence": число 0-100,
  "risk_reward_ok": true/false,
  "stop_loss_quality": "GOOD"/"ACCEPTABLE"/"BAD",
  "take_profit_quality": "GOOD"/"ACCEPTABLE"/"BAD",
  "market_context": "описание рынка",
  "main_reason": "главная причина",
  "risks": ["риск1", "риск2"],
  "recommendation": "совет"
}}
Только JSON, без лишнего текста.
"""
    try:
        resp = requests.post(
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
        log(f"❌ Ошибка AI-валидатора: {e}")
        return None, rr

# ── ТОРГОВЛЯ ─────────────────────────────────────────────────

def get_balance(asset='USDT'):
    account = client.get_account()
    for b in account['balances']:
        if b['asset'] == asset:
            return float(b['free'])
    return 0.0

def get_step_size(symbol):
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            return float(f['stepSize'])
    return 0.00001

def round_qty(qty, step):
    precision = len(str(step).rstrip('0').split('.')[-1])
    return round(qty, precision)

def open_long(symbol, usdt_amount, stop, take):
    try:
        price    = float(client.get_symbol_ticker(symbol=symbol)['price'])
        step     = get_step_size(symbol)
        quantity = round_qty(usdt_amount / price, step)
        order    = client.order_market_buy(symbol=symbol, quantity=quantity)
        log(f"🟢 LONG открыт: {quantity} {symbol} по {price:.2f}")
        log(f"   Стоп: {stop:.2f} | Тейк: {take:.2f}")
        return order, price, quantity
    except Exception as e:
        log(f"❌ Ошибка открытия LONG: {e}")
        return None, None, None

def close_position(symbol, quantity, reason=''):
    try:
        price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        order = client.order_market_sell(symbol=symbol, quantity=quantity)
        log(f"🔴 Позиция закрыта: {quantity} {symbol} по {price:.2f} {reason}")
        return order, price
    except Exception as e:
        log(f"❌ Ошибка закрытия позиции: {e}")
        return None, None

# ── ОПРЕДЕЛЕНИЕ ТОЧЕК ВХОДА ──────────────────────────────────

def find_trade_setup(structure, liquidity, current_price):
    """
    Ищем торговую возможность на основе структуры рынка.

    Логика профессионального входа:
    - При бычьем тренде ищем лонг от зоны поддержки
      после того как ликвидность ниже была собрана
    - При медвежьем тренде ищем шорт от зоны сопротивления
      после того как ликвидность выше была собрана
    - Стоп ставим ЗА зону ликвидности
    - Тейк ставим на следующую зону ликвидности
    """
    support    = liquidity['support']
    resistance = liquidity['resistance']

    if not support or not resistance:
        return None

    nearest_support    = support[0]
    nearest_resistance = resistance[0]

    # Насколько близко цена к зоне поддержки (в %)
    dist_to_support    = (current_price - nearest_support) / current_price * 100
    dist_to_resistance = (nearest_resistance - current_price) / current_price * 100

    # Лонг: цена близко к поддержке (в пределах 1.5%) + бычья структура
    if dist_to_support <= 1.5 and structure['trend'] == 'BULLISH':
        entry = current_price
        stop  = nearest_support * 0.995   # стоп на 0.5% ниже поддержки
        # Тейк у ближайшего сопротивления
        take  = nearest_resistance * 0.998
        return {
            'direction': 'LONG',
            'entry': entry,
            'stop':  stop,
            'take':  take,
            'reason': f'Цена у поддержки {nearest_support:.0f}, бычья структура'
        }

    # Шорт: цена близко к сопротивлению (в пределах 1.5%) + медвежья структура
    if dist_to_resistance <= 1.5 and structure['trend'] == 'BEARISH':
        entry = current_price
        stop  = nearest_resistance * 1.005  # стоп на 0.5% выше сопротивления
        take  = nearest_support * 1.002
        return {
            'direction': 'SHORT',
            'entry': entry,
            'stop':  stop,
            'take':  take,
            'reason': f'Цена у сопротивления {nearest_resistance:.0f}, медвежья структура'
        }

    return None

# ── ГЛАВНЫЙ ЦИКЛ ─────────────────────────────────────────────

def run():
    symbol   = 'BTCUSDT'
    position = None   # текущая позиция
    entry_price = None
    quantity    = None
    stop_price  = None
    take_price  = None

    log("=" * 50)
    log("🤖 CryptoAutoPro запущен. Режим: TESTNET")
    log(f"   Макс. сделка: {MAX_TRADE_USDT} USDT")
    log(f"   Stop-Loss:    {STOP_LOSS_PCT}%")
    log("=" * 50)

    sync_time()

    while True:
        try:
            sync_time()

            # ── 1. Получаем данные ───────────────────────────
            df            = get_candles(symbol, '1h', 200)
            current_price = df['close'].iloc[-1]

            # ── 2. Анализ структуры ──────────────────────────
            highs, lows   = find_swing_points(df, window=10)
            structure     = analyze_structure(highs, lows)
            liquidity     = find_liquidity_zones(highs, lows, current_price)

            # ── 3. Объём и Funding ───────────────────────────
            volume_ratio, vol_conclusion = analyze_volume(df)
            funding_rate  = get_funding_rate(symbol)

            log(f"📊 Цена: {current_price:.2f} | "
                f"Тренд: {structure['trend']} | "
                f"Объём: {volume_ratio:.2f}x")

            # ── 4. Проверяем открытую позицию ───────────────
            if position == 'LONG' and entry_price and quantity:
                pnl_pct = (current_price - entry_price) / entry_price * 100

                # Stop-Loss
                if current_price <= stop_price:
                    log(f"🛑 STOP-LOSS: {pnl_pct:.2f}%")
                    close_position(symbol, quantity, '(Stop-Loss)')
                    position = entry_price = quantity = None
                    stop_price = take_price = None

                # Take-Profit
                elif current_price >= take_price:
                    log(f"🎯 TAKE-PROFIT: +{pnl_pct:.2f}%")
                    close_position(symbol, quantity, '(Take-Profit)')
                    position = entry_price = quantity = None
                    stop_price = take_price = None

                else:
                    log(f"📍 Позиция открыта: P&L {pnl_pct:+.2f}%")

            # ── 5. Ищем новую сделку ─────────────────────────
            if position is None:

                # Фильтр объёма — не торгуем при вялом рынке
                if volume_ratio < 0.5:
                    log(f"😴 Объём слишком низкий ({volume_ratio:.2f}x) — ждём")
                else:
                    setup = find_trade_setup(structure, liquidity, current_price)

                    if setup:
                        log(f"🔍 Найден сетап: {setup['direction']}")
                        log(f"   Причина: {setup['reason']}")

                        # ── 6. AI-валидация ──────────────────
                        log("🤖 Отправляем на проверку к ИИ...")
                        analysis, rr = validate_trade(
                            symbol        = symbol,
                            direction     = setup['direction'],
                            entry         = setup['entry'],
                            stop          = setup['stop'],
                            take          = setup['take'],
                            structure     = structure,
                            volume_ratio  = volume_ratio,
                            funding_rate  = funding_rate,
                            current_price = current_price
                        )

                        if analysis:
                            log(f"   Вердикт ИИ: {analysis['verdict']} "
                                f"(уверенность {analysis['confidence']}%)")
                            log(f"   Причина: {analysis['main_reason']}")

                            # ── 7. Исполнение ────────────────
                            if (analysis['verdict'] == 'APPROVE'
                                    and analysis['confidence'] >= 70
                                    and rr >= 2.0):

                                balance      = get_balance('USDT')
                                trade_amount = min(MAX_TRADE_USDT, balance * 0.95)

                                if setup['direction'] == 'LONG':
                                    order, ep, qty = open_long(
                                        symbol, trade_amount,
                                        setup['stop'], setup['take']
                                    )
                                    if order:
                                        position    = 'LONG'
                                        entry_price = ep
                                        quantity    = qty
                                        stop_price  = setup['stop']
                                        take_price  = setup['take']
                            else:
                                log(f"⏳ Сделка отклонена ИИ — ждём")
                    else:
                        log("⏳ Нет сетапа — ждём")

        except Exception as e:
            log(f"❌ Ошибка: {e}")

        log("⏱️  Следующий цикл через 60 сек...")
        log("-" * 50)
        time.sleep(60)

run()
