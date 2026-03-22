from dotenv import load_dotenv
import pandas as pd
import requests
import json
import os
from datetime import datetime

load_dotenv()

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

def load_trade_history():
    """
    Загружаем историю сделок из trade_history.json.
    """
    history_file = 'data/trade_history.json'

    if not os.path.exists(history_file):
        print("⚠️  Файл истории сделок не найден. Используем тестовые данные.")
        return get_test_trades()

    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            raw_history = json.load(f)

        trades = []
        for t in raw_history:
            if t['status'] == 'CLOSED':
                trades.append({
                    'open_time':  t['open_time'],
                    'close_time': t['close_time'],
                    'type':       t['direction'],
                    'entry':      t['entry_price'],
                    'exit':       t['exit_price'],
                    'result':     'WIN' if t['pnl_usdt'] > 0 else 'LOSS',
                    'exit_reason': t.get('exit_reason', 'Unknown'),
                    'pnl_pct':    t['pnl_pct'],
                    'symbol':     t['symbol']
                })
        return trades if trades else get_test_trades()
    except Exception as e:
        print(f"❌ Ошибка чтения истории: {e}")
        return get_test_trades()

def get_test_trades():
    """
    Тестовые данные для демонстрации работы анализатора.
    Когда бот накопит реальную историю — они заменятся.
    """
    return [
        {
            'type': 'LONG', 'entry': 68500.0, 'exit': 67150.0,
            'result': 'LOSS', 'exit_reason': 'Stop-Loss',
            'open_time': '2026-03-15 10:00:00',
            'close_time': '2026-03-15 14:00:00',
            'reason_open': 'Цена у поддержки 68200, бычья структура'
        },
        {
            'type': 'LONG', 'entry': 67000.0, 'exit': 70500.0,
            'result': 'WIN', 'exit_reason': 'Take-Profit',
            'open_time': '2026-03-16 09:00:00',
            'close_time': '2026-03-17 08:00:00',
            'reason_open': 'Цена у поддержки 66800, объём 2.1x'
        },
        {
            'type': 'SHORT', 'entry': 71000.0, 'exit': 69500.0,
            'result': 'WIN', 'exit_reason': 'Take-Profit',
            'open_time': '2026-03-17 15:00:00',
            'close_time': '2026-03-18 10:00:00',
            'reason_open': 'Цена у сопротивления 71200, медвежья структура'
        },
        {
            'type': 'LONG', 'entry': 69800.0, 'exit': 68400.0,
            'result': 'LOSS', 'exit_reason': 'Stop-Loss',
            'open_time': '2026-03-18 16:00:00',
            'close_time': '2026-03-18 22:00:00',
            'reason_open': 'Цена у поддержки 69600, нейтральная структура'
        },
        {
            'type': 'SHORT', 'entry': 70500.0, 'exit': 71100.0,
            'result': 'LOSS', 'exit_reason': 'Stop-Loss',
            'open_time': '2026-03-19 08:00:00',
            'close_time': '2026-03-19 11:00:00',
            'reason_open': 'Цена у сопротивления 70800, медвежья структура'
        }
    ]

def calculate_stats(trades):
    """Считаем базовую статистику по сделкам"""
    if not trades:
        return None

    total   = len(trades)
    wins    = len([t for t in trades if t['result'] == 'WIN'])
    losses  = len([t for t in trades if t['result'] == 'LOSS'])
    winrate = (wins / total * 100) if total > 0 else 0

    # Считаем P&L для каждой сделки
    pnl_list = []
    for t in trades:
        if t['entry'] and t['exit']:
            if t['type'] == 'LONG':
                pnl = (t['exit'] - t['entry']) / t['entry'] * 100
            else:
                pnl = (t['entry'] - t['exit']) / t['entry'] * 100
            pnl_list.append(pnl)

    avg_pnl    = sum(pnl_list) / len(pnl_list) if pnl_list else 0
    total_pnl  = sum(pnl_list)

    return {
        'total':    total,
        'wins':     wins,
        'losses':   losses,
        'winrate':  winrate,
        'avg_pnl':  avg_pnl,
        'total_pnl': total_pnl,
        'pnl_list': pnl_list
    }

def ai_analyze_trades(trades, stats):
    """
    Отправляем всю историю сделок в ИИ для глубокого анализа.
    ИИ находит паттерны ошибок и даёт конкретные рекомендации.
    """
    trades_text = ""
    for i, t in enumerate(trades, 1):
        pnl = ""
        if t['entry'] and t['exit']:
            if t['type'] == 'LONG':
                p = (t['exit'] - t['entry']) / t['entry'] * 100
            else:
                p = (t['entry'] - t['exit']) / t['entry'] * 100
            pnl = f"{p:+.2f}%"

        trades_text += f"""
Сделка #{i}:
  Тип: {t['type']} | Результат: {t['result']} ({t.get('exit_reason','')})
  Вход: {t['entry']} | Выход: {t['exit']} | P&L: {pnl}
  Время: {t['open_time']} → {t['close_time']}
  Причина входа: {t['reason_open']}
"""

    prompt = f"""
Ты — аналитик торговых систем с 20-летним опытом.
Проанализируй историю сделок торгового бота и найди системные ошибки.

СТАТИСТИКА:
- Всего сделок: {stats['total']}
- Прибыльных: {stats['wins']} ({stats['winrate']:.1f}%)
- Убыточных: {stats['losses']}
- Средний P&L: {stats['avg_pnl']:+.2f}%
- Общий P&L: {stats['total_pnl']:+.2f}%

ИСТОРИЯ СДЕЛОК:
{trades_text}

ЗАДАЧА:
1. Найди повторяющиеся паттерны в убыточных сделках
2. Определи что общего у прибыльных сделок
3. Выяви системные ошибки в логике входа/выхода
4. Дай конкретные рекомендации по улучшению

Ответь в формате JSON:
{{
  "overall_assessment": "общая оценка системы",
  "win_patterns": ["паттерн1", "паттерн2"],
  "loss_patterns": ["паттерн1", "паттерн2"],
  "main_problems": ["проблема1", "проблема2", "проблема3"],
  "improvements": [
    {{
      "problem": "описание проблемы",
      "solution": "конкретное решение",
      "priority": "HIGH/MEDIUM/LOW"
    }}
  ],
  "strategy_score": число 0-100,
  "next_steps": ["шаг1", "шаг2", "шаг3"]
}}
Только JSON.
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
                "temperature": 0.2
            },
            timeout=30
        )
        content = resp.json()['choices'][0]['message']['content']
        return json.loads(content)
    except Exception as e:
        print(f"❌ Ошибка AI-анализа: {e}")
        return None

def save_report(stats, analysis):
    """Сохраняем отчёт в файл"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"reports/analysis_{timestamp}.json"
    report    = {
        'timestamp': timestamp,
        'stats':     stats,
        'analysis':  analysis
    }
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return filename

# ── ЗАПУСК ──────────────────────────────────────────────────
print("🔍 Загружаем историю сделок...")
trades = load_trade_history()
stats  = calculate_stats(trades)

print(f"📊 Найдено сделок: {stats['total']}")
print("🤖 Отправляем на анализ к ИИ...\n")

analysis = ai_analyze_trades(trades, stats)

if analysis:
    print("=" * 55)
    print("        🧠 САМОАНАЛИЗ ТОРГОВОЙ СИСТЕМЫ")
    print("=" * 55)

    print(f"\n  📊 СТАТИСТИКА:")
    print(f"  Всего сделок:   {stats['total']}")
    print(f"  Винрейт:        {stats['winrate']:.1f}%")
    print(f"  Средний P&L:    {stats['avg_pnl']:+.2f}%")
    print(f"  Общий P&L:      {stats['total_pnl']:+.2f}%")
    print(f"  Оценка системы: {analysis['strategy_score']}/100")

    print(f"\n  💬 Общая оценка:")
    print(f"  {analysis['overall_assessment']}")

    print(f"\n  ✅ Паттерны ПРИБЫЛЬНЫХ сделок:")
    for p in analysis['win_patterns']:
        print(f"     • {p}")

    print(f"\n  ❌ Паттерны УБЫТОЧНЫХ сделок:")
    for p in analysis['loss_patterns']:
        print(f"     • {p}")

    print(f"\n  🔧 Что нужно исправить:")
    for imp in analysis['improvements']:
        priority_icon = {'HIGH': '🔴', 'MEDIUM': '🟡', 'LOW': '🟢'}.get(
            imp['priority'], '⚪'
        )
        print(f"\n  {priority_icon} [{imp['priority']}] {imp['problem']}")
        print(f"     → {imp['solution']}")

    print(f"\n  📋 Следующие шаги:")
    for i, step in enumerate(analysis['next_steps'], 1):
        print(f"     {i}. {step}")

    filename = save_report(stats, analysis)
    print(f"\n  💾 Отчёт сохранён: {filename}")
    print("=" * 55)
