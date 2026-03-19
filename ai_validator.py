from dotenv import load_dotenv
import requests
import json
import os

load_dotenv()

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

def validate_trade(
    symbol,
    direction,       # 'LONG' или 'SHORT'
    entry_price,
    stop_loss,
    take_profit,
    structure,       # результат из market_structure.py
    volume_ratio,    # текущий объём / средний
    funding_rate,    # funding rate в %
    current_price
):
    """
    Отправляем все данные о потенциальной сделке в ИИ.
    ИИ анализирует и возвращает вердикт с объяснением.
    """

    # Считаем математику сделки
    if direction == 'LONG':
        risk   = entry_price - stop_loss
        reward = take_profit - entry_price
    else:
        risk   = stop_loss - entry_price
        reward = entry_price - take_profit

    risk_reward = reward / risk if risk > 0 else 0
    risk_pct    = (risk / entry_price) * 100
    reward_pct  = (reward / entry_price) * 100

    # Формируем запрос к ИИ
    prompt = f"""
Ты — профессиональный трейдер с 20-летним опытом на криптовалютном рынке.
Твоя задача — оценить потенциальную сделку и вынести вердикт.

ДАННЫЕ СДЕЛКИ:
- Инструмент: {symbol}
- Направление: {direction}
- Текущая цена: {current_price:.2f} USDT
- Цена входа: {entry_price:.2f} USDT
- Стоп-лосс: {stop_loss:.2f} USDT
- Тейк-профит: {take_profit:.2f} USDT

МАТЕМАТИКА СДЕЛКИ:
- Риск: {risk:.2f} USDT ({risk_pct:.2f}%)
- Потенциальная прибыль: {reward:.2f} USDT ({reward_pct:.2f}%)
- Соотношение риск/прибыль: 1:{risk_reward:.2f}

КОНТЕКСТ РЫНКА:
- Структура тренда: {structure['trend']}
- Описание структуры: {structure['description']}
- Последний максимум: {structure['last_high']}
- Последний минимум: {structure['last_low']}
- Объём (относительно среднего): {volume_ratio:.2f}x
- Funding Rate: {funding_rate if funding_rate else 'недоступен'}%

ТВОЯ ЗАДАЧА — ответить СТРОГО в формате JSON:
{{
  "verdict": "APPROVE" или "REJECT",
  "confidence": число от 0 до 100,
  "risk_reward_ok": true или false,
  "stop_loss_quality": "GOOD", "ACCEPTABLE" или "BAD",
  "take_profit_quality": "GOOD", "ACCEPTABLE" или "BAD",
  "market_context": "краткое описание ситуации на рынке",
  "main_reason": "главная причина решения одним предложением",
  "risks": ["риск 1", "риск 2"],
  "recommendation": "конкретный совет трейдеру"
}}

Минимально приемлемое соотношение риск/прибыль: 1:2
Отвечай ТОЛЬКО JSON, без лишнего текста.
"""

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json"
            },
            json={
                "model": "anthropic/claude-3-haiku",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1   # низкая температура = более чёткие ответы
            },
            timeout=30
        )

        result   = response.json()
        content  = result['choices'][0]['message']['content']

        # Парсим JSON ответ
        analysis = json.loads(content)
        return analysis

    except Exception as e:
        return {
            "verdict":            "REJECT",
            "confidence":         0,
            "risk_reward_ok":     False,
            "stop_loss_quality":  "BAD",
            "take_profit_quality":"BAD",
            "market_context":     "Ошибка анализа",
            "main_reason":        f"Ошибка подключения к ИИ: {e}",
            "risks":              ["Нет доступа к AI-валидатору"],
            "recommendation":     "Не торговать без подтверждения ИИ"
        }

def print_analysis(analysis, direction, entry, stop, take):
    """Красиво выводим результат анализа"""

    verdict_icon = '✅ ОДОБРЕНО' if analysis['verdict'] == 'APPROVE' else '❌ ОТКЛОНЕНО'

    print("\n" + "=" * 50)
    print("     🤖 AI-ВАЛИДАТОР СДЕЛКИ")
    print("=" * 50)
    print(f"\n  Вердикт:      {verdict_icon}")
    print(f"  Уверенность:  {analysis['confidence']}%")
    print(f"\n  Направление:  {direction}")
    print(f"  Вход:         {entry:.2f} USDT")
    print(f"  Стоп-лосс:    {stop:.2f} USDT")
    print(f"  Тейк-профит:  {take:.2f} USDT")
    print(f"\n  📊 Оценка параметров:")
    print(f"  Риск/Прибыль: {'✅' if analysis['risk_reward_ok'] else '❌'}")
    print(f"  Стоп-лосс:    {analysis['stop_loss_quality']}")
    print(f"  Тейк-профит:  {analysis['take_profit_quality']}")
    print(f"\n  📈 Контекст рынка:")
    print(f"  {analysis['market_context']}")
    print(f"\n  💡 Главная причина:")
    print(f"  {analysis['main_reason']}")

    if analysis['risks']:
        print(f"\n  ⚠️  Риски:")
        for r in analysis['risks']:
            print(f"     • {r}")

    print(f"\n  📝 Рекомендация:")
    print(f"  {analysis['recommendation']}")
    print("\n" + "=" * 50)

# ── ТЕСТ ────────────────────────────────────────────────────
# Симулируем потенциальную сделку для теста

test_structure = {
    'trend':       'BEARISH',
    'description': '📉 Медвежий тренд (LH/LL)',
    'last_high':   74815.66,
    'last_low':    62295.12
}

# Тестовая сделка: LONG на текущей цене
entry       = 69208.00
stop        = 67500.00   # стоп ниже зоны поддержки
take        = 72000.00   # тейк у зоны сопротивления

print("📡 Отправляем данные на анализ к ИИ...")

analysis = validate_trade(
    symbol        = 'BTCUSDT',
    direction     = 'LONG',
    entry_price   = entry,
    stop_loss     = stop,
    take_profit   = take,
    structure     = test_structure,
    volume_ratio  = 0.16,
    funding_rate  = 0.0012,
    current_price = 69208.00
)

print_analysis(analysis, 'LONG', entry, stop, take)
