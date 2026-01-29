import os
import asyncio
from dotenv import load_dotenv
load_dotenv(override=True)

from lighter.signer_client import SignerClient

async def main():
    private_key = os.getenv('API_KEY_PRIVATE_KEY')
    account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '7926'))
    
    print(f"Account Index: {account_index}")
    print(f"私钥前10位: {private_key[:10]}...")
    
    if private_key.startswith("0x"):
        private_key = private_key[2:]
    
    client = SignerClient(
        url="https://mainnet.zklighter.elliot.ai",
        account_index=account_index,
        api_private_keys={0: private_key}
    )
    
    # 测试下单
    import time
    client_id = int(time.time() * 1000) % 2147483647
    
    print(f"\n尝试下单...")
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

asyncio.run(main())
