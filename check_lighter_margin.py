import os
import asyncio
from dotenv import load_dotenv
from lighter.signer_client import SignerClient
from lighter import ApiClient, Configuration, AccountApi

async def main():
    # 加载环境变量
    load_dotenv(override=True)
    
    # 1. 获取配置
    try:
        account_index = int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0"))
        api_key_index = int(os.getenv("LIGHTER_API_KEY_INDEX", "0"))
        api_priv = os.getenv("API_KEY_PRIVATE_KEY")
        if api_priv and api_priv.startswith("0x"):
            api_priv = api_priv[2:]
            
        if not api_priv:
            print("❌ 错误: .env 文件中未找到 API_KEY_PRIVATE_KEY")
            return
            
    except Exception as e:
        print(f"❌ 环境变量读取错误: {e}")
        return

    # 正确的 URL
    correct_url = "https://mainnet.zklighter.elliot.ai"

    print("--- Lighter 账户诊断 ---")
    print(f"🔗 连接 URL: {correct_url}")
    print(f"👤 Account Index: {account_index}")
    
    # 2. 初始化交易客户端 (SignerClient)
    try:
        client = SignerClient(
            url=correct_url,
            account_index=account_index,
            api_private_keys={api_key_index: api_priv},
        )
        print("✅ SignerClient 初始化成功")
    except Exception as e:
        print(f"❌ SignerClient 初始化失败: {e}")
        return
    
    # 3. 检查资金 (USDC)
    try:
        conf = Configuration(host=correct_url)
        api_client = ApiClient(configuration=conf)
        account_api = AccountApi(api_client)
        
        print("\n🔍 正在查询账户资金...")
        # 查询账户信息
        account_data = await account_api.account(by="index", value=str(account_index))
        
        # 兼容 SDK 返回结构: 可能是 DetailedAccounts(含列表) 或 直接是 DetailedAccount
        account_info = None
        if hasattr(account_data, 'accounts') and account_data.accounts:
            account_info = account_data.accounts[0]
        elif hasattr(account_data, 'index'): # 假如直接返回了单体对象
            account_info = account_data
            
        if account_info:
            # === 🔥 修复点：使用 .index 而不是 .id ===
            print(f"   账户 Index: {account_info.index} (Name: {getattr(account_info, 'name', 'N/A')})")
            print(f"   可用余额 (Available): {account_info.available_balance}")
            print(f"   总资产值 (Total Value): {account_info.total_asset_value}")
            
            print("   --- 资产详情 (Assets) ---")
            # 遍历 assets 查找 USDC (通常 USDC 是主要的资产)
            if hasattr(account_info, 'assets') and account_info.assets:
                for asset in account_info.assets:
                    # 打印 asset 的属性，DetailedAccount 定义里 assets 是 AccountAsset 类型
                    # 假设 AccountAsset 有 .asset_id 和 .balance
                    print(f"   - 资产 ID {getattr(asset, 'asset_id', '?')}: {getattr(asset, 'balance', getattr(asset, 'available_balance', '?'))}")
            else:
                print("   (无资产信息)")

            print("   --- 持仓详情 (Positions) ---")
            if hasattr(account_info, 'positions') and account_info.positions:
                for pos in account_info.positions:
                     print(f"   - Market {getattr(pos, 'market_id', '?')}: {getattr(pos, 'position', '?')} (Cost: {getattr(pos, 'entry_value', '?')})")
            else:
                print("   (无持仓)")

        else:
            print("❌ 未找到账户信息 (account_data 空)")
            
        print("\n💡 提示:")
        print("   请确认上方显示的【可用余额】或资产列表中是否有 >20 的数值(USDC)。")

    except Exception as e:
        print(f"❌ 查询余额失败: {e}")

if __name__ == "__main__":
    asyncio.run(main())
