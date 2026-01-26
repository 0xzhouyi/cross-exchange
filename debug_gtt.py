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

async def run_debug():
    idx = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    raw_key = os.getenv("API_KEY_PRIVATE_KEY")
    if raw_key.startswith("0x"): raw_key = raw_key[2:]
    
    client = SignerClient(
        url="https://mainnet.zklighter.elliot.ai",
        account_index=idx,
        api_private_keys={int(os.getenv('LIGHTER_API_KEY_INDEX', '0')): raw_key}
    )

    print("\n--- 🕵️‍♂️ 寻找 GTT 常量 ---")
    # 尝试反射获取所有 TIF 常量
    tif_gtt = getattr(client, 'ORDER_TIME_IN_FORCE_GOOD_TILL_TIME', None)
    
    # 如果 SDK 没暴露，我们盲测常见的 Enum 值
    # 通常: GTC=0, IOC=1, FOK=2, GTT=3 (或者其他顺序)
    # Lighter 源码暗示: GTC=0, IOC=2, FOK=3... 需要实测
    if tif_gtt is None:
        print("⚠️ SDK 未暴露 GTT 常量，准备盲测 (尝试 1, 2, 3)...")
        candidates = [1, 2, 3]
    else:
        print(f"✅ 找到 GTT 常量: {tif_gtt}")
        candidates = [tif_gtt]

    # 准备 8位精度的 Amount
    base_amount = 13 * (10 ** 7) # 0.13 * 10^8
    # 准备 秒级时间戳 (Unix Seconds)
    expiry_seconds = int(time.time()) + 3600 # 1小时后过期

    for tif_val in candidates:
        print(f"\n🧪 尝试 TimeInForce = {tif_val} + Expiry Seconds...")
        try:
            res = await client.create_order(
                market_index=1,
                price=80000 * 10**6,
                base_amount=base_amount,
                is_ask=True, 
                order_type=1, # Limit
                client_order_index=int(time.time() * 1000) & 0x7FFFFFFF,
                time_in_force=tif_val, 
                order_expiry=expiry_seconds
            )
            print(f"🎉🎉🎉 成功！GTT 策略有效！TIF={tif_val}")
            print(f"订单结果: {res}")
            return # 成功就退出
        except Exception as e:
            err = str(e)
            print(f"❌ 失败: {err}")
            if "invalid signature" in err:
                print("   -> 签名依然不对 (可能是此 TIF 不支持带 Expiry?)")
            elif "OrderExpiry is invalid" in err:
                print("   -> 业务拒绝 (说明此 TIF 也许不是 GTT)")

if __name__ == "__main__":
    asyncio.run(run_debug())
