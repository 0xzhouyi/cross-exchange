python3 << 'EOF'
import os
import asyncio
from dotenv import load_dotenv
load_dotenv(override=True)

from lighter.signer_client import SignerClient

async def test_account(index):
    private_key = os.getenv('API_KEY_PRIVATE_KEY')
    if private_key.startswith("0x"):
        private_key = private_key[2:]
    
    print(f"\n{'='*50}")
    print(f"测试 Account Index: {index}")
    print(f"{'='*50}")
    
    try:
        client = SignerClient(
            url="https://mainnet.zklighter.elliot.ai",
            account_index=index,
            api_private_keys={0: private_key}
        )
        
        # 尝试获取一个很小的订单来测试
        # 用一个不可能成交的价格
        import time
        client_id = int(time.time() * 1000) % 2147483647
        
        print(f"尝试下一个测试订单...")
        res = await client.create_order(
            market_index=1,              # BTC
            client_order_index=client_id,
            base_amount=100,             # 0.001 BTC
            price=50000000000,           # $50000 - 很低的买价，不会成交
            is_ask=False,                # 买单
            order_type=0,                # limit
            time_in_force=1,             # IOC - 立即取消
            reduce_only=False,
        )
        
        print(f"响应: {res}")
        
        if isinstance(res, tuple) and len(res) >= 3:
            if res[2] is None:
                print(f"✅ Account Index {index} 可以下单！")
                return True
            else:
                error = str(res[2])
                print(f"错误: {error}")
                if "margin" in error.lower():
                    print(f"⚠️ Account {index} 存在但保证金不足")
                elif "invalid" in error.lower():
                    print(f"❌ Account {index} 无效")
                return False
        
        print(f"✅ Account Index {index} 似乎可用")
        return True
        
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

async def main():
    for idx in [1, 2, 3, 4]:  # 跳过已知无效的 0
        await test_account(idx)

asyncio.run(main())
EOF
