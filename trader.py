from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import ta
import os
import time
import json
from datetime import datetime

load_dotenv()

API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
MAX_TRADE_USDT = float(os.getenv('MAX_TRADE_AMOUNT_USDT', 10))
STOP_LOSS_PCT = float(os.getenv('STOP_LOSS_PERCENT', 2))

client = Client(
    api_key=API_KEY,
    api_secret=API_SECRET,
    testnet=True
)

server_time = client.get_server_time()
client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)

# ── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ────────────────────────────────

def log(message):
    """Записываем все действия в журнал"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open('logs/trader.log', 'a', encoding='utf-8') as f:
        f.write(line + '\n')

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
    last = df.iloc[-1]
    prev = df.iloc[-2]
    signal = "WAIT"
    reasons = []

    buy_conditions = 0
    if prev['rsi'] < 30 and last['rsi'] >= 30:
        buy_conditions += 1
        reasons.append("RSI вышел из перепроданности")
    if prev['ema_20'] < prev['ema_50'] and last['ema_20'] >= last['ema_50']:
        buy_conditions += 1
        reasons.append("Золотой крест EMA")
    if prev['macd'] < prev['macd_signal'] and last['macd'] >= last['macd_signal']:
        buy_conditions += 1
        reasons.append("MACD пересёк сигнал вверх")

    sell_conditions = 0
    if prev['rsi'] > 70 and last['rsi'] <= 70:
        sell_conditions += 1
        reasons.append("RSI вышел из перекупленности")
    if prev['ema_20'] > prev['ema_50'] and last['ema_20'] <= last['ema_50']:
        sell_conditions += 1
        reasons.append("Мёртвый крест EMA")
    if prev['macd'] > prev['macd_signal'] and last['macd'] <= last['macd_signal']:
        sell_conditions += 1
        reasons.append("MACD пересёк сигнал вниз")

    if buy_conditions >= 2:
        signal = "BUY"
    elif sell_conditions >= 2:
        signal = "SELL"

    return signal, reasons, last

def get_balance(asset='USDT'):
    """Получаем баланс конкретной монеты"""
    account = client.get_account()
    for b in account['balances']:
        if b['asset'] == asset:
            return float(b['free'])
    return 0.0

def get_symbol_info(symbol):
    """Получаем параметры торговой пары"""
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            step_size = float(f['stepSize'])
            return step_size
    return 0.00001

def round_quantity(quantity, step_size):
    """Округляем количество по правилам биржи"""
    precision = len(str(step_size).rstrip('0').split('.')[-1])
    return round(quantity, precision)

def buy(symbol, usdt_amount):
    """Покупаем на указанную сумму USDT"""
    try:
        price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        step_size = get_symbol_info(symbol)
        quantity = round_quantity(usdt_amount / price, step_size)

        log(f"🟢 ПОКУПКА {symbol}: {quantity} по цене {price:.2f} USDT")
        log(f"   Stop-Loss будет на: {price * (1 - STOP_LOSS_PCT/100):.2f} USDT")

        order = client.order_market_buy(
            symbol=symbol,
            quantity=quantity
        )

        log(f"✅ Сделка открыта. ID: {order['orderId']}")
        return order, price

    except Exception as e:
        log(f"❌ Ошибка при покупке: {e}")
        return None, None

def sell(symbol, quantity):
    """Продаём указанное количество"""
    try:
        price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        log(f"🔴 ПРОДАЖА {symbol}: {quantity} по цене {price:.2f} USDT")

        order = client.order_market_sell(
            symbol=symbol,
            quantity=quantity
        )

        log(f"✅ Сделка закрыта. ID: {order['orderId']}")
        return order

    except Exception as e:
        log(f"❌ Ошибка при продаже: {e}")
        return None

# ── ГЛАВНЫЙ ЦИКЛ ───────────────────────────────────────────

def run():
    symbol = 'BTCUSDT'
    position = None      # текущая открытая позиция
    entry_price = None   # цена входа

    log("🤖 Бот запущен. Режим: TESTNET")
    log(f"   Макс. сумма сделки: {MAX_TRADE_USDT} USDT")
    log(f"   Stop-Loss: {STOP_LOSS_PCT}%")
    log("-" * 45)

    while True:
        try:
            # Получаем данные и анализируем
            df = get_candles(symbol, '1h', 100)
            df = add_indicators(df)
            signal, reasons, last = generate_signal(df)
            price = last['close']

            log(f"📊 Цена: {price:.2f} | RSI: {last['rsi']:.1f} | Сигнал: {signal}")

            # ── Проверка Stop-Loss ──────────────────────────
            if position and entry_price:
                loss_pct = (price - entry_price) / entry_price * 100
                if loss_pct <= -STOP_LOSS_PCT:
                    log(f"🛑 STOP-LOSS сработал! Убыток: {loss_pct:.2f}%")
                    sell(symbol, position)
                    position = None
                    entry_price = None
                    continue

            # ── Исполнение сигналов ─────────────────────────
            if signal == "BUY" and position is None:
                usdt_balance = get_balance('USDT')
                trade_amount = min(MAX_TRADE_USDT, usdt_balance * 0.95)
                log(f"📋 Причины: {', '.join(reasons)}")
                order, entry_price = buy(symbol, trade_amount)
                if order:
                    position = round_quantity(
                        trade_amount / entry_price,
                        get_symbol_info(symbol)
                    )

            elif signal == "SELL" and position:
                log(f"📋 Причины: {', '.join(reasons)}")
                sell(symbol, position)
                position = None
                entry_price = None

            else:
                log(f"⏳ Ждём сигнала...")

        except Exception as e:
            log(f"❌ Ошибка в главном цикле: {e}")

        # Ждём 60 секунд перед следующей проверкой
        log("⏱️  Следующая проверка через 60 секунд...")
        time.sleep(60)

# Запускаем
run()
