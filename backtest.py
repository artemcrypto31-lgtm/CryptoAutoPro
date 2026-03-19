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

def get_historical_data(symbol, interval, days=180):
    """
    Получаем исторические данные порциями по 1000 свечей
    и склеиваем их в одну таблицу
    """
    print(f"📥 Загружаем {days} дней истории для {symbol}...")
    
    all_candles = []
    # Сколько миллисекунд в одном периоде (1 час = 3600000 мс)
    interval_ms = 3600000
    # Сколько миллисекунд нам нужно всего
    total_ms = days * 24 * interval_ms
    # Стартовое время — days дней назад
    start_time = int(time.time() * 1000) - total_ms

    while True:
        raw = client.get_klines(
            symbol=symbol,
            interval=interval,
            startTime=start_time,
            limit=1000
        )
        if not raw:
            break

        all_candles.extend(raw)
        # Следующая порция начинается после последней свечи
        start_time = raw[-1][0] + interval_ms

        # Если дошли до текущего времени — стоп
        if start_time >= int(time.time() * 1000):
            break

        time.sleep(0.3)  # небольшая пауза чтобы не перегрузить API

    df = pd.DataFrame(all_candles, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df = df[['time', 'open', 'high', 'low', 'close', 'volume']]
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df = df.drop_duplicates(subset='time').reset_index(drop=True)

    print(f"✅ Загружено {len(df)} свечей")
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
    df = df.dropna().reset_index(drop=True)
    return df

def run_backtest(df, initial_balance=1000, stop_loss_pct=2):
    balance = initial_balance
    position = None
    entry_price = None
    spent = 0       # сколько USDT потратили на покупку
    trades = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row['close']

        # ── Проверка Stop-Loss ──────────────────────────────
        if position and entry_price:
            loss_pct = (price - entry_price) / entry_price * 100
            if loss_pct <= -stop_loss_pct:
                proceeds = position * price
                profit = proceeds - spent
                balance += proceeds          # возвращаем выручку
                trades.append({
                    'type': 'SELL (Stop-Loss)',
                    'time': row['time'],
                    'price': price,
                    'profit': round(profit, 2),
                    'balance': round(balance, 2)
                })
                position = None
                entry_price = None
                spent = 0
                continue

        # ── Сигналы ─────────────────────────────────────────
        buy_conditions = 0
        if prev['rsi'] < 30 and row['rsi'] >= 30:
            buy_conditions += 1
        if prev['ema_20'] < prev['ema_50'] and row['ema_20'] >= row['ema_50']:
            buy_conditions += 1
        if prev['macd'] < prev['macd_signal'] and row['macd'] >= row['macd_signal']:
            buy_conditions += 1

        sell_conditions = 0
        if prev['rsi'] > 70 and row['rsi'] <= 70:
            sell_conditions += 1
        if prev['ema_20'] > prev['ema_50'] and row['ema_20'] <= row['ema_50']:
            sell_conditions += 1
        if prev['macd'] > prev['macd_signal'] and row['macd'] <= row['macd_signal']:
            sell_conditions += 1

        # ── Исполнение ──────────────────────────────────────
        if buy_conditions >= 2 and position is None and balance > 10:
            spent = balance * 0.95
            position = spent / price
            balance -= spent             # ← вычитаем из баланса
            entry_price = price
            trades.append({
                'type': 'BUY',
                'time': row['time'],
                'price': price,
                'profit': 0,
                'balance': round(balance, 2)
            })

        elif sell_conditions >= 2 and position:
            proceeds = position * price
            profit = proceeds - spent
            balance += proceeds
            trades.append({
                'type': 'SELL',
                'time': row['time'],
                'price': price,
                'profit': round(profit, 2),
                'balance': round(balance, 2)
            })
            position = None
            entry_price = None
            spent = 0

    # Если позиция ещё открыта — закрываем по последней цене
    if position:
        last_price = df.iloc[-1]['close']
        proceeds = position * last_price
        profit = proceeds - spent
        balance += proceeds
        trades.append({
            'type': 'SELL (закрытие)',
            'time': df.iloc[-1]['time'],
            'price': last_price,
            'profit': round(profit, 2),
            'balance': round(balance, 2)
        })

    return trades, balance

# ── ЗАПУСК ──────────────────────────────────────────────────
df = get_historical_data('BTCUSDT', '1h', 180)
df = add_indicators(df)
trades, final_balance = run_backtest(df, initial_balance=1000, stop_loss_pct=2)

# ── ОТЧЁТ ───────────────────────────────────────────────────
trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

total_trades = len(trades_df[trades_df['type'] == 'BUY']) if not trades_df.empty else 0
stop_losses  = len(trades_df[trades_df['type'] == 'SELL (Stop-Loss)']) if not trades_df.empty else 0
profitable   = len(trades_df[trades_df['profit'] > 0]) if not trades_df.empty else 0
total_profit = trades_df['profit'].sum() if not trades_df.empty else 0
profit_pct   = (final_balance - 1000) / 1000 * 100

print("\n" + "=" * 45)
print("        📊 РЕЗУЛЬТАТЫ БЭКТЕСТИНГА")
print("=" * 45)
print(f"  Период:          180 дней (6 месяцев)")
print(f"  Стартовый депо:  1000 USDT")
print(f"  Финальный депо:  {final_balance:.2f} USDT")
print(f"  Итог:            {profit_pct:+.2f}%")
print("-" * 45)
print(f"  Всего сделок:    {total_trades}")
print(f"  Прибыльных:      {profitable}")
print(f"  Stop-Loss:       {stop_losses}")
print(f"  Общая прибыль:   {total_profit:.2f} USDT")
print("=" * 45)

if not trades_df.empty:
    trades_df.to_csv('reports/backtest_results.csv', index=False)
    print("\n📁 Детальный отчёт: reports/backtest_results.csv")
