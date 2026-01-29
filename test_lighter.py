import os
import asyncio
from dotenv import load_dotenv
load_dotenv(override=True)

from lighter.signer_client import SignerClient

async def test_account(index):
    private_key = os.getenv('API_KEY_PRIVATE_KEY')
    if private_key.startswith("0x"):
        private_key = private_key[2:]
    
    print(f"\n测试 Account Index: {index}")
    
    try:
        client = SignerClient(
            url="https://mainnet.zklighter.elliot.ai",
            account_index=index,
            api_private_keys={0: private_key}
        )
        
        import time
        client_id = int(time.time() * 1000) % 2147483647
        
        res = await client.create_order(
            market_index=1,
            client_order_index=client_id,
            base_amount=100,
            price=50000000000,
            is_ask=False,
            order_type=0,
            time_in_force=1,
            reduce_only=False,
        )
        
        print(f"响应: {res}")
        
    except Exception as e:
        print(f"异常: {e}")

async def main():
    for idx in [1, 2, 3, 4]:
        await test_account(idx)

asyncio.run(main())
