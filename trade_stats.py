import json
import os
from datetime import datetime

STATS_FILE = 'data/trade_history.json'

def load_history():
    """Загружаем историю сделок из файла"""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(history):
    """Сохраняем историю сделок в файл"""
    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def record_open(symbol, direction, entry_price,
                stop_loss, take_profit, size_usdt, leverage):
    """
    Записываем открытие сделки.
    Вызывается в момент когда бот открывает позицию.
    """
    history = load_history()
    trade = {
        'id':           len(history) + 1,
        'symbol':       symbol,
        'direction':    direction,         # LONG или SHORT
        'entry_price':  entry_price,
        'stop_loss':    stop_loss,
        'take_profit':  take_profit,
        'size_usdt':    size_usdt,         # размер позиции в USDT
        'leverage':     leverage,
        'open_time':    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'close_time':   None,
        'exit_price':   None,
        'exit_reason':  None,              # TP / SL / MANUAL
        'pnl_usdt':     None,              # прибыль/убыток в USDT
        'pnl_pct':      None,              # прибыль/убыток в %
        'status':       'OPEN'
    }
    history.append(trade)
    save_history(history)
    print(f"📝 Сделка #{trade['id']} записана: {direction} {symbol}")
    return trade['id']

def record_close(trade_id, exit_price, exit_reason):
    """
    Записываем закрытие сделки и считаем P&L.
    Вызывается когда бот закрывает позицию.
    """
    history = load_history()

    for trade in history:
        if trade['id'] == trade_id and trade['status'] == 'OPEN':
            # Считаем P&L
            if trade['direction'] == 'LONG':
                pnl_pct  = (exit_price - trade['entry_price']) / trade['entry_price'] * 100
            else:
                pnl_pct  = (trade['entry_price'] - exit_price) / trade['entry_price'] * 100

            pnl_usdt = trade['size_usdt'] * (pnl_pct / 100) * trade['leverage']

            trade['exit_price']  = exit_price
            trade['exit_reason'] = exit_reason
            trade['close_time']  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            trade['pnl_usdt']    = round(pnl_usdt, 4)
            trade['pnl_pct']     = round(pnl_pct, 4)
            trade['status']      = 'CLOSED'
            break

    save_history(history)

def calculate_stats():
    """
    Считаем полную статистику по всем закрытым сделкам.
    Это то что ты будешь видеть в Telegram.
    """
    history  = load_history()
    closed   = [t for t in history if t['status'] == 'CLOSED']

    if not closed:
        return None

    # Базовые метрики
    total    = len(closed)
    wins     = [t for t in closed if t['pnl_usdt'] > 0]
    losses   = [t for t in closed if t['pnl_usdt'] <= 0]
    win_rate = len(wins) / total * 100

    # P&L
    total_pnl    = sum(t['pnl_usdt'] for t in closed)
    avg_win      = sum(t['pnl_usdt'] for t in wins)    / len(wins)    if wins    else 0
    avg_loss     = sum(t['pnl_usdt'] for t in losses)  / len(losses)  if losses  else 0

    # Profit Factor = сумма прибылей / сумма убытков
    gross_profit = sum(t['pnl_usdt'] for t in wins)
    gross_loss   = abs(sum(t['pnl_usdt'] for t in losses))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else float('inf')

    # Максимальная просадка (Drawdown)
    equity      = 500.0   # стартовый капитал
    peak        = equity
    max_dd      = 0.0
    equity_curve = []

    for t in closed:
        equity += t['pnl_usdt']
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Серии побед/поражений
    max_win_streak  = 0
    max_loss_streak = 0
    cur_win         = 0
    cur_loss        = 0

    for t in closed:
        if t['pnl_usdt'] > 0:
            cur_win  += 1
            cur_loss  = 0
            max_win_streak = max(max_win_streak, cur_win)
        else:
            cur_loss += 1
            cur_win   = 0
            max_loss_streak = max(max_loss_streak, cur_loss)

    # Лучшая и худшая сделка
    best  = max(closed, key=lambda t: t['pnl_usdt'])
    worst = min(closed, key=lambda t: t['pnl_usdt'])

    # Статистика по символам
    symbols = {}
    for t in closed:
        s = t['symbol']
        if s not in symbols:
            symbols[s] = {'trades': 0, 'pnl': 0}
        symbols[s]['trades'] += 1
        symbols[s]['pnl']    += t['pnl_usdt']

    return {
        'total':          total,
        'wins':           len(wins),
        'losses':         len(losses),
        'win_rate':       round(win_rate, 1),
        'total_pnl':      round(total_pnl, 2),
        'final_equity':   round(equity, 2),
        'profit_factor':  profit_factor,
        'avg_win':        round(avg_win, 2),
        'avg_loss':       round(avg_loss, 2),
        'max_drawdown':   round(max_dd, 2),
        'max_win_streak': max_win_streak,
        'max_loss_streak':max_loss_streak,
        'best_trade':     best,
        'worst_trade':    worst,
        'by_symbol':      symbols,
        'equity_curve':   equity_curve
    }

def print_stats():
    """Выводим статистику в консоль"""
    stats = calculate_stats()

    if not stats:
        print("📊 Сделок пока нет.")
        return

    pf_str = f"{stats['profit_factor']:.3f}" if stats['profit_factor'] != float('inf') else "∞"

    print("\n" + "=" * 55)
    print("        📊 ПОЛНАЯ СТАТИСТИКА ТОРГОВЛИ")
    print("=" * 55)
    print(f"  Всего сделок:      {stats['total']}")
    print(f"  Прибыльных:        {stats['wins']} ({stats['win_rate']}%)")
    print(f"  Убыточных:         {stats['losses']}")
    print("-" * 55)
    print(f"  Общий P&L:         {stats['total_pnl']:+.2f} USDT")
    print(f"  Итоговый баланс:   {stats['final_equity']:.2f} USDT")
    print(f"  Profit Factor:     {pf_str}")
    print("-" * 55)
    print(f"  Средний выигрыш:   +{stats['avg_win']:.2f} USDT")
    print(f"  Средний проигрыш:  {stats['avg_loss']:.2f} USDT")
    print(f"  Макс. просадка:    -{stats['max_drawdown']:.2f}%")
    print("-" * 55)
    print(f"  Серия побед:       {stats['max_win_streak']}")
    print(f"  Серия поражений:   {stats['max_loss_streak']}")
    print("-" * 55)
    print(f"  Лучшая сделка:     +{stats['best_trade']['pnl_usdt']:.2f} USDT"
          f" ({stats['best_trade']['symbol']})")
    print(f"  Худшая сделка:     {stats['worst_trade']['pnl_usdt']:.2f} USDT"
          f" ({stats['worst_trade']['symbol']})")
    print("-" * 55)
    print("  По активам:")
    for sym, data in stats['by_symbol'].items():
        print(f"    {sym:<12} сделок: {data['trades']}  P&L: {data['pnl']:+.2f} USDT")
    print("=" * 55)

def format_stats_telegram():
    """Форматируем статистику для Telegram"""
    history = load_history()
    closed  = [t for t in history if t['status'] == 'CLOSED']
    opened  = [t for t in history if t['status'] == 'OPEN']
    
    stats = calculate_stats()

    if not stats:
        text = "📊 *СТАТИСТИКА ТОРГОВЛИ*\n"
        text += "─" * 30 + "\n"
        text += f"📌 Открытых позиций: *{len(opened)}*\n"
        text += "📈 Закрытых сделок пока нет.\n"
        return text

    pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "∞"
    pnl_icon = "📈" if stats['total_pnl'] > 0 else "📉"

    text = (
        f"📊 *СТАТИСТИКА ТОРГОВЛИ*\n"
        f"{'─' * 30}\n"
        f"📌 Открытых позиций: *{len(opened)}*\n"
        f"Сделок закрыто: {stats['total']} "
        f"(✅{stats['wins']} / ❌{stats['losses']})\n"
        f"Win Rate: *{stats['win_rate']}%*\n"
        f"Profit Factor: *{pf_str}*\n"
        f"{'─' * 30}\n"
        f"{pnl_icon} Общий P&L: *{stats['total_pnl']:+.2f} USDT*\n"
        f"💰 Баланс: *{stats['final_equity']:.2f} USDT*\n"
        f"📉 Макс. просадка: *{stats['max_drawdown']:.2f}%*\n"
        f"{'─' * 30}\n"
    )

    if stats['by_symbol']:
        text += f"{'─' * 30}\n*По активам:*\n"
        for sym, data in stats['by_symbol'].items():
            icon = "✅" if data['pnl'] > 0 else "❌"
            text += f"{icon} {sym}: {data['pnl']:+.2f} USDT ({data['trades']} сд.)\n"

    return text

# Тестируем модуль с демо-данными
if __name__ == "__main__":
    print("🧪 Тестируем модуль статистики с демо-данными...")

    # Очищаем тестовые данные если есть
    if os.path.exists(STATS_FILE):
        os.remove(STATS_FILE)

    # Записываем несколько тестовых сделок
    id1 = record_open('BTCUSDT',  'LONG',  69000, 67500, 72000, 100, 3)
    record_close(id1, 72000, 'TP')

    id2 = record_open('ETHUSDT',  'SHORT', 1800,  1850,  1700,  100, 3)
    record_close(id2, 1850, 'SL')

    id3 = record_open('SOLUSDT',  'LONG',  120,   115,   135,   100, 3)
    record_close(id3, 135, 'TP')

    id4 = record_open('BTCUSDT',  'SHORT', 71000, 72500, 68000, 100, 3)
    record_close(id4, 68000, 'TP')

    id5 = record_open('DOGEUSDT', 'LONG',  0.09,  0.085, 0.105, 100, 3)
    record_close(id5, 0.085, 'SL')

    print_stats()
    print("\n📱 Формат для Telegram:")
    print(format_stats_telegram())
