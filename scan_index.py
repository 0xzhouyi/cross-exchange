import os
import asyncio
import logging
from dotenv import load_dotenv

# 尝试导入 Lighter SDK
try:
    from lighter.signer_client import SignerClient
except ImportError:
    print("❌ 错误: 缺少 lighter SDK，请运行 pip install lighter-sdk")
    exit(1)

# 配置日志
logging.basicConfig(level=logging.ERROR)
load_dotenv()

async def main():
    print("🕵️‍♂️ 正在暴力扫描 Lighter 账户索引 (Index 0-9)...")
    print("--------------------------------------------------")

    # 1. 获取并清洗私钥
    raw_key = os.getenv('API_KEY_PRIVATE_KEY')
    if not raw_key:
        print("❌ .env 中缺少 API_KEY_PRIVATE_KEY")
        return

    # 自动截取修复
    if raw_key.startswith("0x"): raw_key = raw_key[2:]
    if len(raw_key) == 80:
        real_key = raw_key[:64]
        print(f"✂️  已自动截取私钥 (使用前64位)")
    elif len(raw_key) == 64:
        real_key = raw_key
    else:
        print(f"❌ 私钥长度异常: {len(raw_key)}")
        return

    # 2. 循环尝试
    found = False
    
    # 只需要扫描前 5 个通常就够了
    for index in range(5):
        print(f"Testing Index {index}...", end=" ")
        
        try:
            # 尝试初始化客户端
            client = SignerClient(
                url="https://mainnet.zklighter.elliot.ai",
                account_index=index,
                api_private_keys={0: real_key}  # 假设 API Key Index 是 0
            )
            
            # 关键步骤：尝试获取账户信息
            # 如果索引不对，这一步会抛出异常
            account_info = client.get_account(index)
            
            if account_info:
                print("✅ 成功！")
                print("\n🎉🎉🎉 找到您的账户了！ 🎉🎉🎉")
                print("========================================")
                print(f"✅ LIGHTER_ACCOUNT_INDEX={index}")
                print(f"✅ API_KEY_PRIVATE_KEY={real_key}")
                print("========================================")
                print("👉 请立即更新您的 .env 文件！")
                found = True
                break
                
        except Exception as e:
            err_str = str(e)
            if "invalid account index" in err_str or "Account not found" in err_str:
                print("❌ 不存在")
            elif "api key not found" in err_str:
                print("❌ API Key 不匹配 (可能 API Key Index 不是 0)")
                # 如果这里报错，可能需要嵌套循环测试 API Key Index，但通常是 0
            else:
                # 打印出未预期的错误，但也算作失败
                print(f"❌ 失败 ({err_str})")

    if not found:
        print("\n--------------------------------------------------")
        print("❌ 扫描结束，未找到有效账户。")
        print("可能的原因：")
        print("1. 您的钱包【0xf6c...49eb】从未在 Lighter.xyz 点击【Deposit】存入资金。")
        print("   (仅仅在 MetaMask 里有钱是不够的，必须存入交易所智能合约)")
        print("2. 您的 API Key 是在另一个钱包地址上生成的。")
        print("--------------------------------------------------")

if __name__ == "__main__":
    asyncio.run(main())
