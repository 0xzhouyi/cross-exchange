import os
import sys
import asyncio

# 直接读取 .env
def load_env():
    env = {}
    with open('.env') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

env = load_env()
API_PRIV = env.get("API_KEY_PRIVATE_KEY", "").strip()
ACCOUNT_INDEX = int(env.get("LIGHTER_ACCOUNT_INDEX", "7926"))

print(f"私钥长度: {len(API_PRIV)}, 前10位: {API_PRIV[:10]}")
print(f"Account Index: {ACCOUNT_INDEX}")

from lighter.signer_client import SignerClient

URL = "https://mainnet.zklighter.elliot.ai"

async def try_index(i):
    print(f"\n尝试 API Key Index = {i} ...")
    client = SignerClient(
        url=URL,
        account_index=ACCOUNT_INDEX,
        api_private_keys={i: API_PRIV},
    )
    try:
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
        res_str = str(res).lower()
        if "invalid signature" in res_str:
            return False
        return True
    except Exception as e:
        err = str(e).lower()
        print(f"异常: {e}")
        if "invalid signature" in err:
            return False
        return True

async def main():
    if not API_PRIV:
        print("私钥为空！")
        return
        
    for i in range(10):
        ok = await try_index(i)
        if ok:
            print(f"\n✅ 正确的 API Key Index = {i}")
            return
    print("\n❌ 0-9 都失败，请检查私钥是否正确")

asyncio.run(main())
