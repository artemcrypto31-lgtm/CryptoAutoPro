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

# Процесс торгового бота — храним чтобы можно было остановить
bot_process = None

# ── ЗАЩИТА — только владелец ─────────────────────────────────

def owner_only(func):
    """
    Декоратор защиты — блокирует всех кроме владельца.
    Если кто-то чужой напишет боту — он получит отказ.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != TELEGRAM_CHAT_ID:
            await update.message.reply_text(
                "⛔ Доступ запрещён. Это приватный бот."
            )
            return
        return await func(update, context)
    return wrapper

# ── ГЛАВНОЕ МЕНЮ ─────────────────────────────────────────────

def main_keyboard():
    """Главная клавиатура с кнопками управления"""
    keyboard = [
        [
            InlineKeyboardButton("🟢 Запустить бота",  callback_data="start_bot"),
            InlineKeyboardButton("🔴 Остановить бота", callback_data="stop_bot")
        ],
        [
            InlineKeyboardButton("📊 Статус",          callback_data="status"),
            InlineKeyboardButton("📈 Отчёт",           callback_data="report")
        ],
        [
            InlineKeyboardButton("🧠 Самоанализ ИИ",   callback_data="analysis"),
            InlineKeyboardButton("💰 Баланс",          callback_data="balance")
        ],
        [
            InlineKeyboardButton("📋 Последний лог",   callback_data="log")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ── КОМАНДЫ ──────────────────────────────────────────────────

@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — главное меню"""
    await update.message.reply_text(
        "🤖 *CryptoAutoPro* — панель управления\n\n"
        "Выбери действие:",
        parse_mode='Markdown',
        reply_markup=main_keyboard()
    )

@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help — список команд"""
    text = (
        "📋 *Доступные команды:*\n\n"
        "/start — главное меню\n"
        "/status — статус бота\n"
        "/balance — текущий баланс\n"
        "/report — отчёт по сделкам\n"
        "/analysis — самоанализ ИИ\n"
        "/log — последние записи лога\n"
        "/help — эта справка"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

# ── ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ЗАПУСКА СКРИПТОВ ─────────────────

def run_script(script_name):
    """
    Запускаем Python-скрипт и возвращаем его вывод.
    Используем sys.executable — это точный путь к текущему Python
    внутри нашего виртуального окружения.
    """
    try:
        result = subprocess.run(
    [sys.executable, "-u", script_name],
    capture_output=True,
    timeout=90,
    cwd=os.getcwd(),
    env={**os.environ, "PYTHONIOENCODING": "utf-8"}
)

        # Пробуем utf-8, при ошибке — игнорируем проблемные символы
        stdout = result.stdout.decode('utf-8', errors='ignore').strip()
        stderr = result.stderr.decode('utf-8', errors='ignore').strip()

        if stdout:
            return stdout
        elif stderr:
            return f"⚠️ Ошибка скрипта:\n{stderr}"
        else:
            return f"Скрипт завершился без вывода (код: {result.returncode})"

    except subprocess.TimeoutExpired:
        return "❌ Превышено время ожидания (90 сек)"
    except Exception as e:
        return f"❌ Ошибка запуска: {e}"

# ── ОБРАБОТКА КНОПОК ─────────────────────────────────────────

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатываем нажатия всех кнопок"""
    query = update.callback_query
    await query.answer()

    # Защита на кнопки
    if query.from_user.id != TELEGRAM_CHAT_ID:
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    data = query.data

    # ── Запуск торгового бота ────────────────────────────────
    if data == "start_bot":
        global bot_process

        if bot_process and bot_process.poll() is None:
            await query.edit_message_text(
                "⚠️ Бот уже запущен!",
                reply_markup=main_keyboard()
            )
            return

        try:
            log_file = open('logs/bot_output.log', 'a', encoding='utf-8')
            bot_process = subprocess.Popen(
                [sys.executable, "-u", "bot.py"],
                stdout=log_file,
                stderr=log_file
            )
            await query.edit_message_text(
                "🟢 *Бот запущен!*\n\n"
                "Торговля активна. Буду присылать уведомления о каждой сделке.",
                parse_mode='Markdown',
                reply_markup=main_keyboard()
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ Ошибка запуска бота: {e}",
                reply_markup=main_keyboard()
            )

    # ── Остановка торгового бота ─────────────────────────────
    elif data == "stop_bot":
        if bot_process and bot_process.poll() is None:
            bot_process.terminate()
            await query.edit_message_text(
                "🔴 *Бот остановлен.*\n\n"
                "Торговля приостановлена.",
                parse_mode='Markdown',
                reply_markup=main_keyboard()
            )
        else:
            await query.edit_message_text(
                "⚠️ Бот сейчас не запущен.",
                reply_markup=main_keyboard()
            )

    # ── Статус ───────────────────────────────────────────────
    elif data == "status":
        running = bot_process and bot_process.poll() is None
        status  = "🟢 Работает" if running else "🔴 Остановлен"

        last_log = "Нет данных"
        if os.path.exists('logs/bot.log'):
            with open('logs/bot.log', 'r', encoding='utf-8') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
                if lines:
                    last_log = lines[-1]

        await query.edit_message_text(
            f"📊 *Статус системы*\n\n"
            f"Бот: {status}\n\n"
            f"Последнее действие:\n`{last_log}`",
            parse_mode='Markdown',
            reply_markup=main_keyboard()
        )

    # ── Баланс ───────────────────────────────────────────────
    elif data == "balance":
        await query.edit_message_text(
            "💰 Запрашиваю баланс...",
            reply_markup=main_keyboard()
        )
        try:
            from binance.client import Client
            import time
            API_KEY    = os.getenv('BINANCE_API_KEY')
            API_SECRET = os.getenv('BINANCE_API_SECRET')
            client     = Client(API_KEY, API_SECRET, testnet=True)
            server_time = client.get_server_time()
            client.timestamp_offset = (
                server_time['serverTime'] - int(time.time() * 1000)
            )
            account  = client.get_account()
            balances = [
                b for b in account['balances']
                if float(b['free']) > 0 or float(b['locked']) > 0
            ]
            text = "💰 *Текущий баланс (Testnet):*\n\n"
            for b in balances[:10]:
                free   = float(b['free'])
                locked = float(b['locked'])
                text  += f"  {b['asset']}: `{free:.4f}`"
                if locked > 0:
                    text += f" (заморожено: {locked:.4f})"
                text += "\n"
        except Exception as e:
            text = f"❌ Ошибка получения баланса:\n{e}"

        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=main_keyboard()
        )

    # ── Отчёт по сделкам ─────────────────────────────────────
    elif data == "report":
        try:
            if os.path.exists('logs/bot.log'):
                with open('logs/bot.log', 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                trade_lines = [
                    l.strip() for l in lines
                    if any(x in l for x in [
                        'LONG', 'SHORT', 'STOP-LOSS',
                        'TAKE-PROFIT', 'закрыта', 'Вердикт'
                    ])
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
            text = f"❌ Ошибка чтения лога: {e}"

        await query.edit_message_text(
            text[:4000],
            parse_mode='Markdown',
            reply_markup=main_keyboard()
        )

    # ── Самоанализ ИИ ────────────────────────────────────────
    elif data == "analysis":
        await query.edit_message_text(
            "🧠 Запускаю самоанализ...\nПодожди около 30 секунд.",
            reply_markup=main_keyboard()
        )

        # Запускаем скрипт в отдельном потоке чтобы не блокировать бота
        loop   = asyncio.get_running_loop()
        output = await loop.run_in_executor(
            None,
            run_script,
            "self_analysis.py"
        )

        # Telegram ограничивает сообщение 4096 символами
        output = output[:3500]

        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"🧠 *Результат самоанализа:*\n\n`{output}`",
            parse_mode='Markdown'
        )

    # ── Последний лог ────────────────────────────────────────
    elif data == "log":
        try:
            if os.path.exists('logs/bot.log'):
                with open('logs/bot.log', 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                last_lines = "".join(lines[-15:]).strip()
                text = f"📋 *Последние 15 записей лога:*\n\n`{last_lines}`"
            else:
                text = "📋 Лог пустой. Бот ещё не запускался."
        except Exception as e:
            text = f"❌ Ошибка чтения лога: {e}"

        await query.edit_message_text(
            text[:4000],
            parse_mode='Markdown',
            reply_markup=main_keyboard()
        )

# ── УВЕДОМЛЕНИЯ О СДЕЛКАХ ────────────────────────────────────

def send_trade_notification(message):
    """
    Отправляем уведомление о сделке.
    Эта функция вызывается из bot.py при каждой сделке.
    """
    token = os.getenv('TELEGRAM_TOKEN')
    chat  = os.getenv('TELEGRAM_CHAT_ID')
    url   = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": chat, "text": message, "parse_mode": "Markdown"},
            timeout=5
        )
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