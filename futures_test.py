from binance.um_futures import UMFutures
from dotenv import load_dotenv
import os
import time

load_dotenv()

API_KEY    = os.getenv('FUTURES_API_KEY')
API_SECRET = os.getenv('FUTURES_API_SECRET')

# UMFutures — это клиент для USDT-M фьючерсов (USDT.P)
client = UMFutures(
    key=API_KEY,
    secret=API_SECRET,
    base_url="https://testnet.binancefuture.com"
)

try:
    # Проверяем баланс
    account  = client.account()
    balances = [
        b for b in account['assets']
        if float(b['walletBalance']) > 0
    ]

    print("✅ Подключение к Futures Testnet успешно!")
    print("\n💰 Балансы:")
    for b in balances:
        print(f"   {b['asset']}: {float(b['walletBalance']):.2f}")

    # Проверяем позиции
    positions = [
        p for p in account['positions']
        if float(p['positionAmt']) != 0
    ]
    if positions:
        print(f"\n📊 Открытые позиции: {len(positions)}")
    else:
        print("\n📊 Открытых позиций нет")

except Exception as e:
    print(f"❌ Ошибка: {e}")
