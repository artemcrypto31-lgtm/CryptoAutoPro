from binance.client import Client
from dotenv import load_dotenv
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

# Синхронизируем время с сервером Binance автоматически
server_time = client.get_server_time()
client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)

try:
    account = client.get_account()
    print("✅ Подключение успешно!")
    print("💰 Твои тестовые балансы:")
    for asset in account['balances']:
        free = float(asset['free'])
        if free > 0:
            print(f"   {asset['asset']}: {free}")
except Exception as e:
    print(f"❌ Ошибка подключения: {e}")