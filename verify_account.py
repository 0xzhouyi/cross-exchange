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
    
    # 这里的默认值 '0' 是最大的嫌疑犯
    env_account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
    
    print("\n" + "="*50)
    print(f"🕵️‍♂️ 正在检查配置:")
    print(f"   Account Index (账户序号): {env_account_index}")
    print(f"   API Key Index (密钥序号): {api_key_index}")
    print("="*50)

    client = SignerClient(
        url="https://mainnet.zklighter.elliot.ai",
        account_index=env_account_index,
        api_private_keys={api_key_index: private_key}
    )

    # 2. 尝试获取账户详细信息 (核对身份)
    try:
        print("\n🔍 正在向服务器查询账户信息...")
        account_info = await client.get_account()
        
        # 打印关键身份信息
        print(f"✅ 账户查询成功！")
        print(f"   Lighter ID (Index): {account_info.index}")
        print(f"   Owner Address (钱包): {account_info.owner}")
        print(f"   Nonce: {account_info.nonce}")
        
        # 打印余额（寻找 USDC）
        # 注意：不同版本 SDK 返回结构不同，这里尝试通用打印
        print("   --- 资产余额 ---")
        print(account_info) 
        
    except Exception as e:
        print(f"❌ 账户查询失败: {e}")
        print("   👉这通常意味着 Account Index 错了，或者私钥不匹配。")
        return

    # 3. 发送一个必定无法成交的“测试挂单” (Maker)
    # 卖出 0.0001 BTC @ $200,000
    print("\n🧪 正在尝试挂一个 $200,000 的测试卖单...")
    
    try:
        # 使用整数原子单位
        atomic_price = 200000 * (10**6)       # 20万美金
        atomic_amount = 100000000000000       # 0.0001 BTC (10^14 wei)
        client_id = int(time.time() * 1000) % 2147483647

        res = await client.create_order(
            market_index=1,
            price=atomic_price,
            base_amount=atomic_amount,
            is_ask=True,
            order_type=1, # Limit
            client_order_index=client_id,
            time_in_force=0 # GTC
        )
        
        print(f"📤 发送结果: {res}")
        print("\n⚠️ 请务必现在去 Lighter 网页端查看【Open Orders】！")
        print("   如果这里显示发送成功，但网页上没有，请核对上方打印的【Owner Address】是否与网页连接的钱包一致！")

    except Exception as e:
        print(f"❌ 下单报错: {e}")

if __name__ == "__main__":
    asyncio.run(run_verification())
