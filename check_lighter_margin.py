import os
import asyncio
from dotenv import load_dotenv
from lighter import SignerClient

async def main():
    load_dotenv()
    
    account_index = int(os.getenv("LIGHTER_ACCOUNT_INDEX", "1"))
    api_key_index = int(os.getenv("LIGHTER_API_KEY_INDEX", "3"))
    api_priv = os.getenv("API_KEY_PRIVATE_KEY")
    
    print("--- Lighter 账户诊断 ---")
    print(f"Account Index: {account_index}, API Key Index: {api_key_index}")
    
    client = SignerClient(
        url="https://api.lighter.xyz",
        account_index=account_index,
        api_private_keys={api_key_index: api_priv},
    )
    
    err = await client.check_client()
    if err:
        print(f"Client check failed: {err}")
        return
    print("✅ Client check passed")
    
    print("\n[1] Portfolio Detail:")
    detail = await client.get_portfolio_detail()
    print(detail)
    
    print("\n[2] Positions:")
    positions = await client.get_positions()
    print(positions)

if __name__ == "__main__":
    asyncio.run(main())
