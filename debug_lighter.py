from dotenv import load_dotenv
import os
import asyncio
import logging
import time
from decimal import Decimal

load_dotenv(override=True)

try:
    from lighter.signer_client import SignerClient
except ImportError:
    print("❌ 缺少 lighter 库")
    exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LighterDebug")

async def test_order(client, amount_desc, base_amount_val, expiry_val, case_name):
    print(f"\n--- 🧪 {case_name} ---")
    print(f"📝 参数: Amount={base_amount_val} ({amount_desc}), Expiry={expiry_val}")
    
    try:
        # 尝试动态获取 IOC 常量，如果获取不到则默认用 0 (GTC)
        TIF_IOC = getattr(client, 'ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL', 3)
        # 如果不知道 IOC 是多少，先用 GTC (0) 测试下单，只要不报 Expiry invalid 就是成功
        TIF_TEST = 0 
        
        res = await client.create_order(
            market_index=1,
            price=80000 * 10**6, # 80,000 USDC (合理的测试价格)
            base_amount=base_amount_val,
            is_ask=True, # Sell
            order_type=1, # Limit
            client_order_index=int(time.time() * 1000) & 0x7FFFFFFF,
            time_in_force=TIF_TEST, 
            order_expiry=expiry_val # 这里是我们测试的核心
        )
        print(f"✅ {case_name} 发送成功: {res}")
    except Exception as e:
        print(f"⚠️ {case_name} 返回结果: {e}")
        err_str = str(e)
        if "OrderExpiry is invalid" in err_str:
            print("❌ 失败: 服务器依然不接受这个 Expiry。")
        elif "invalid signature" in err_str:
            print("❌ 失败: 签名无效。")
        else:
            print("🎉 成功迹象: 只要不是 Signature 或 Expiry 错误，就说明参数格式对了！")

async def run_debug():
    idx = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    print(f"👤 当前使用的 Account Index: {idx}")
    
    raw_key = os.getenv("API_KEY_PRIVATE_KEY")
    if raw_key.startswith("0x"): raw_key = raw_key[2:]
    
    client = SignerClient(
        url="https://mainnet.zklighter.elliot.ai",
        account_index=idx,
        api_private_keys={int(os.getenv('LIGHTER_API_KEY_INDEX', '0')): raw_key}
    )

    # === 测试 C: 核心验证 (Expiry = 0) ===
    # 金额使用正常的 10^8 精度
    normal_amount = 13 * (10 ** 7) # 0.13 * 10^8
    await test_order(client, "10^8 精度", normal_amount, 0, "测试 C (Expiry=0)")

if __name__ == "__main__":
    asyncio.run(run_debug())
