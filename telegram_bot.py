from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv
import subprocess
import asyncio
import requests
import sys
import os

load_dotenv()

TELEGRAM_TOKEN     = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = int(os.getenv('TELEGRAM_CHAT_ID'))
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

bot_process = None

# ── ЗАЩИТА ───────────────────────────────────────────────────

def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != TELEGRAM_CHAT_ID:
            await update.message.reply_text("⛔ Доступ запрещён. Это приватный бот.")
            return
        return await func(update, context)
    return wrapper

# ── ГЛАВНОЕ МЕНЮ ─────────────────────────────────────────────

def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🟢 Запустить бота",  callback_data="start_bot"),
            InlineKeyboardButton("🔴 Остановить бота", callback_data="stop_bot")
        ],
        [
            InlineKeyboardButton("📊 Статус",          callback_data="status"),
            InlineKeyboardButton("📈 Статистика",      callback_data="stats")
        ],
        [
            InlineKeyboardButton("📌 Позиции",         callback_data="positions"),
            InlineKeyboardButton("💰 Баланс",          callback_data="balance")
        ],
        [
            InlineKeyboardButton("🧠 Самоанализ ИИ",   callback_data="analysis"),
            InlineKeyboardButton("🔍 Отчёт",           callback_data="report")
        ],
        [
            InlineKeyboardButton("📋 Последний лог",   callback_data="log")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ── КОМАНДЫ ──────────────────────────────────────────────────

@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *CryptoAutoPro* — панель управления\n\nВыбери действие:",
        parse_mode='Markdown',
        reply_markup=main_keyboard()
    )

@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *Доступные команды:*\n\n"
        "/start — главное меню\n"
        "/help — эта справка"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

# ── ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ──────────────────────────────────

def run_script(script_name):
    try:
        result = subprocess.run(
            [sys.executable, "-u", script_name],
            capture_output=True,
            timeout=90,
            cwd=os.getcwd(),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )
        stdout = result.stdout.decode('utf-8', errors='ignore').strip()
        stderr = result.stderr.decode('utf-8', errors='ignore').strip()
        if stdout:
            return stdout
        elif stderr:
            return f"⚠️ Ошибка:\n{stderr}"
        else:
            return f"Скрипт завершился без вывода (код: {result.returncode})"
    except subprocess.TimeoutExpired:
        return "❌ Превышено время ожидания (90 сек)"
    except Exception as e:
        return f"❌ Ошибка запуска: {e}"

# ── ОБРАБОТКА КНОПОК ─────────────────────────────────────────

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != TELEGRAM_CHAT_ID:
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    data = query.data

    # ── Запуск бота ──────────────────────────────────────────
    if data == "start_bot":
        global bot_process
        if bot_process and bot_process.poll() is None:
            await query.edit_message_text("⚠️ Бот уже запущен!", reply_markup=main_keyboard())
            return
        try:
            log_file    = open('logs/bot_output.log', 'a', encoding='utf-8')
            bot_process = subprocess.Popen(
                [sys.executable, "-u", "futures_bot.py"],
                stdout=log_file, stderr=log_file
            )
            await query.edit_message_text(
                "🟢 *Бот запущен!*\n\nТорговля активна. Буду присылать уведомления о каждой сделке.",
                parse_mode='Markdown', reply_markup=main_keyboard()
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка запуска: {e}", reply_markup=main_keyboard())

    # ── Остановка бота ───────────────────────────────────────
    elif data == "stop_bot":
        if bot_process and bot_process.poll() is None:
            bot_process.terminate()
            await query.edit_message_text(
                "🔴 *Бот остановлен.*\n\nТорговля приостановлена.",
                parse_mode='Markdown', reply_markup=main_keyboard()
            )
        else:
            await query.edit_message_text("⚠️ Бот сейчас не запущен.", reply_markup=main_keyboard())

    # ── Статус ───────────────────────────────────────────────
    elif data == "status":
        # Проверяем наличие процесса futures_bot.py в системе
        import subprocess
        try:
            # Для Linux/Ubuntu проверяем через pgrep
            check_proc = subprocess.run(["pgrep", "-f", "futures_bot.py"], capture_output=True)
            running = check_proc.returncode == 0
        except:
            # Запасной вариант для других систем
            running = bot_process and bot_process.poll() is None
            
        status   = "🟢 Работает (Active)" if running else "🔴 Остановлен (Stopped)"
        last_log = "Нет данных"
        if os.path.exists('logs/futures_bot.log'):
            try:
                with open('logs/futures_bot.log', 'r', encoding='utf-8') as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                    if lines:
                        last_log = lines[-1]
            except: pass

        await query.edit_message_text(
            f"📊 *Статус системы*\n\nБот: {status}\n\nПоследнее действие:\n`{last_log}`",
            parse_mode='Markdown', reply_markup=main_keyboard()
        )

    # ── Открытые позиции (Paper Trading) ─────────────────────
    elif data == "positions":
        try:
            import json
            pos_file = 'data/active_positions.json'
            if os.path.exists(pos_file):
                with open(pos_file, 'r', encoding='utf-8') as f:
                    positions = json.load(f)
            else:
                positions = {}

            if not positions:
                text = "📌 *Открытых виртуальных позиций нет*\n\nБот ищет сигналы на реальном рынке..."
            else:
                text = f"📌 *Открытые позиции (Paper): {len(positions)}*\n\n"
                for symbol, p in positions.items():
                    # Получаем текущую цену для актуальности P&L
                    try:
                        from binance.um_futures import UMFutures
                        p_client = UMFutures(base_url="https://fapi.binance.com")
                        mark = float(p_client.ticker_price(symbol=symbol)['price'])
                    except:
                        mark = p['entry'] # если цена не пришла, берем цену входа

                    entry     = p['entry']
                    direction = p['direction']
                    dir_icon  = "🟢" if direction == 'LONG' else "🔴"
                    
                    if direction == 'LONG':
                        pnl_pct = (mark - entry) / entry * 100
                    else:
                        pnl_pct = (entry - mark) / entry * 100
                    
                    pnl_icon = "📈" if pnl_pct >= 0 else "📉"
                    pnl_usdt = p['size_usdt'] * (pnl_pct / 100) * 3 # LEVERAGE=3 по умолчанию

                    text += (
                        f"{dir_icon} *{direction} {symbol}*\n"
                        f"💵 Вход:     `{entry:.5f}`\n"
                        f"📍 Текущая:  `{mark:.5f}`\n"
                        f"🛑 Стоп:     `{p['stop']:.5f}`\n"
                        f"🎯 Тейк:     `{p['take']:.5f}`\n"
                        f"{pnl_icon} P&L:     `{pnl_pct:+.2f}%` (`{pnl_usdt:+.2f} USDT`)\n"
                        f"🔄 Трейлинг: `{'Активен' if p.get('trailing_active') else 'Ждёт TP'}`\n"
                        f"{'─' * 28}\n"
                    )
        except Exception as e:
            text = f"❌ Ошибка получения позиций: {e}"

        await query.edit_message_text(text[:4000], parse_mode='Markdown', reply_markup=main_keyboard())

    # ── Статистика ───────────────────────────────────────────
    elif data == "stats":
        try:
            from trade_stats import format_stats_telegram
            text = format_stats_telegram()
        except Exception as e:
            text = f"❌ Ошибка статистики: {e}"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=main_keyboard())

    # ── Баланс ───────────────────────────────────────────────
    elif data == "balance":
        await query.edit_message_text("💰 Запрашиваю баланс (Mainnet)...", reply_markup=main_keyboard())
        try:
            from binance.um_futures import UMFutures
            client  = UMFutures(
                key=os.getenv('BINANCE_API_KEY'),
                secret=os.getenv('BINANCE_API_SECRET'),
                base_url="https://fapi.binance.com"
            )
            account = client.account()
            text    = "💰 *Баланс Futures (Mainnet):*\n\n"
            for asset in account['assets']:
                balance = float(asset['walletBalance'])
                if balance > 0:
                    pnl   = float(asset['unrealizedProfit'])
                    text += f"  {asset['asset']}: `{balance:.2f}`"
                    if pnl != 0:
                        text += f" (PnL: {pnl:+.2f})"
                    text += "\n"
            text += "\n⚠️ _Для Paper Trading баланс не важен, но здесь показан ваш реальный счет._"
        except Exception as e:
            text = f"❌ Ошибка баланса: {e}"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=main_keyboard())

    # ── Отчёт ────────────────────────────────────────────────
    elif data == "report":
        try:
            if os.path.exists('logs/futures_bot.log'):
                with open('logs/futures_bot.log', 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                trade_lines = [
                    l.strip() for l in lines
                    if any(x in l for x in ['LONG', 'SHORT', 'SL', 'TP', 'ПРИБЫЛЬ', 'УБЫТОК'])
                ]
                if trade_lines:
                    text = "📈 *Последние торговые события:*\n\n"
                    for line in trade_lines[-10:]:
                        text += f"`{line}`\n"
                else:
                    text = "📈 Сделок пока не было. Бот ждёт сигнала."
            else:
                text = "📈 Лог пустой. Сначала запусти бота."
        except Exception as e:
            text = f"❌ Ошибка лога: {e}"
        await query.edit_message_text(text[:4000], parse_mode='Markdown', reply_markup=main_keyboard())

    # ── Самоанализ ───────────────────────────────────────────
    elif data == "analysis":
        await query.edit_message_text(
            "🧠 Запускаю самоанализ...\nПодожди около 30 секунд.",
            reply_markup=main_keyboard()
        )
        loop   = asyncio.get_running_loop()
        output = await loop.run_in_executor(None, run_script, "self_analysis.py")
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"🧠 *Результат самоанализа:*\n\n`{output[:3500]}`",
            parse_mode='Markdown'
        )

    # ── Лог ─────────────────────────────────────────────────
    elif data == "log":
        try:
            if os.path.exists('logs/futures_bot.log'):
                with open('logs/futures_bot.log', 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                last_lines = "".join(lines[-15:]).strip()
                text = f"📋 *Последние 15 записей лога:*\n\n`{last_lines}`"
            else:
                text = "📋 Лог пустой."
        except Exception as e:
            text = f"❌ Ошибка лога: {e}"
        await query.edit_message_text(text[:4000], parse_mode='Markdown', reply_markup=main_keyboard())

# ── УВЕДОМЛЕНИЯ ──────────────────────────────────────────────

def send_trade_notification(message):
    token = os.getenv('TELEGRAM_TOKEN')
    chat  = os.getenv('TELEGRAM_CHAT_ID')
    url   = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat, "text": message, "parse_mode": "Markdown"}, timeout=5)
    except Exception:
        pass

# ── ЗАПУСК ───────────────────────────────────────────────────

def main():
    print("🤖 Telegram-бот запущен...")
    print(f"   Python:   {sys.executable}")
    print(f"   Владелец: {TELEGRAM_CHAT_ID}")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CallbackQueryHandler(handle_button))

    print("✅ Готово! Напиши /start в Telegram")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

main()
