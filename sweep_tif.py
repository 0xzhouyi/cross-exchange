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
logger = logging.getLogger("LighterSweep")

async def run_sweep():
    idx = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    raw_key = os.getenv("API_KEY_PRIVATE_KEY")
    if raw_key.startswith("0x"): raw_key = raw_key[2:]
    
    client = SignerClient(
        url="https://mainnet.zklighter.elliot.ai",
        account_index=idx,
        api_private_keys={int(os.getenv('LIGHTER_API_KEY_INDEX', '0')): raw_key}
    )

    print("\n🚀 开始暴力扫描 TIF (TimeInForce) 值...")
    
    # 准备参数：8位精度金额 + 秒级时间戳 (这俩是为了过签名)
    base_amount = 13 * (10 ** 7) # 0.13 * 10^8
    expiry_seconds = int(time.time()) + 3600 # 1小时后

    # 尝试 TIF 0 到 5
    for tif_val in range(6):
        print(f"\n🧪 测试 TIF = {tif_val} (带 Expiry)...")
        try:
            res = await client.create_order(
                market_index=1,
                price=80000 * 10**6, # 价格低点，确保能成
                base_amount=base_amount,
                is_ask=True, 
                order_type=1, # Limit
                client_order_index=int(time.time() * 1000) & 0x7FFFFFFF,
                time_in_force=tif_val, # 暴力尝试
                order_expiry=expiry_seconds # 始终带 Expiry 以绕过签名Bug
            )
            
            # 检查结果元组
            if res and isinstance(res, tuple):
                error_msg = res[2]
                if error_msg:
                    print(f"❌ 失败: {error_msg}")
                    if "OrderTimeInForce is not valid" in error_msg:
                        print("   -> 这个 TIF 值不对")
                    elif "OrderExpiry is invalid" in error_msg:
                        print("   -> 这个 TIF 不允许带 Expiry")
                else:
                    print(f"🎉🎉🎉 发现可用 TIF: {tif_val}！")
                    print(f"完整响应: {res}")
                    print("✅ 解决方案: 在 arbitrage.py 中使用这个 TIF 值 + 秒级 Expiry！")
                    return
            else:
                 # 如果 SDK 返回结构不同
                print(f"🎉 可能成功? 响应: {res}")
                return

        except Exception as e:
            print(f"⚠️ 报错: {e}")

if __name__ == "__main__":
    asyncio.run(run_sweep())
