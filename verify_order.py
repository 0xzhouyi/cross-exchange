from dotenv import load_dotenv
import os
import asyncio
import logging
import time

load_dotenv(override=True)

try:
    from lighter.signer_client import SignerClient
except ImportError:
    print("❌ 缺少 lighter 库")
    exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Verify")

async def run_verification():
    # 1. 加载配置
    private_key = os.getenv("API_KEY_PRIVATE_KEY")
    if private_key.startswith("0x"): private_key = private_key[2:]
    
    env_account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
    
    print("\n" + "="*50)
    print(f"🕵️‍♂️ 身份信息:")
    print(f"   Account Index: {env_account_index}")
    print("="*50)

    client = SignerClient(
        url="https://mainnet.zklighter.elliot.ai",
        account_index=env_account_index,
        api_private_keys={api_key_index: private_key}
    )

    # 2. 发送一个“天价”测试卖单 (Maker)
    # 卖出 0.0001 BTC @ $200,000 (远高于市场价，确保不会成交，必须挂在盘口)
    print("\n🧪 正在发送测试单 (Sell 0.0001 BTC @ $200,000)...")
    
    try:
        # 使用整数原子单位
        atomic_price = 200000 * (10**6)       # 20万美金
        atomic_amount = 100000000000000       # 0.0001 BTC
        client_id = int(time.time() * 1000) % 2147483647

        # 打印我们将要发送的原始参数，方便排查
        print(f"   参数: Price={atomic_price}, Amount={atomic_amount}, Type=1(Limit)")

        res = await client.create_order(
            market_index=1,
            price=atomic_price,
            base_amount=atomic_amount,
            is_ask=True,
            order_type=1, # Limit
            client_order_index=client_id,
            time_in_force=0 # GTC
        )
        
        print("\n" + "="*30)
        print("📥 服务器响应:")
        print(res)
        print("="*30)
        
        print("\n✅ 发送完成！")
        print("👉 请立刻去 Lighter 网页端 ->【Open Orders (当前委托)】查看！")
        print("   必须看到一个价格为 200,000 USDC 的卖单。")
        print("   如果这里显示成功但网页没有，说明您登录的钱包 Account Index 不是 7926！")

    except Exception as e:
        print(f"\n❌ 下单报错: {e}")

if __name__ == "__main__":
    asyncio.run(run_verification())
