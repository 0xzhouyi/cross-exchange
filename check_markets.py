from dotenv import load_dotenv
import os
import asyncio
import logging
import json

load_dotenv(override=True)

try:
    from lighter.signer_client import SignerClient
except ImportError:
    print("❌ 缺少 lighter 库")
    exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Check")

async def check_info():
    private_key = os.getenv("API_KEY_PRIVATE_KEY")
    if private_key.startswith("0x"): private_key = private_key[2:]
    
    account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
    
    client = SignerClient(
        url="https://mainnet.zklighter.elliot.ai",
        account_index=account_index,
        api_private_keys={api_key_index: private_key}
    )
    
    print("\n" + "="*40)
    print("🔍 1. 查询市场配置 (确认 Market ID)")
    print("="*40)
    try:
        # 获取所有市场信息
        # 注意：方法名可能因 SDK 版本不同，通常是 get_markets 或 get_exchange_info
        # 这里尝试通过 client 的属性或方法获取
        if hasattr(client, 'get_markets'):
            markets = await client.get_markets()
            print(json.dumps(markets, indent=2, default=str))
        else:
            print("⚠️ SDK 没有 get_markets 方法，尝试直接读取配置...")
            # 如果没有直接方法，通常 client 内部有 config 属性
            print(dir(client))
            
    except Exception as e:
        logger.error(f"查询市场失败: {e}")

    print("\n" + "="*40)
    print("💰 2. 查询账户余额 (确认 USDC)")
    print("="*40)
    try:
        # 获取账户信息
        account = await client.get_account()
        print(f"Account Index: {account_index}")
        print("Raw Data:", account)
    except Exception as e:
        logger.error(f"查询账户失败: {e}")

if __name__ == "__main__":
    asyncio.run(check_info())
