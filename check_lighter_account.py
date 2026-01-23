# check_lighter_account.py
import os
import asyncio
from dotenv import load_dotenv
from lighter.signer_client import SignerClient

# 加载 .env
load_dotenv()

async def main():
    private_key = os.getenv('API_KEY_PRIVATE_KEY')
    if not private_key:
        print("❌ 错误: .env 中找不到 API_KEY_PRIVATE_KEY")
        return

    print(f"🔑 正在使用私钥 (前5位): {private_key[:5]}... 进行测试")

    # 尝试遍历索引 0 到 5
    found = False
    for index in range(5):
        print(f"\n🔍 正在尝试 Account Index: {index} ...")
        try:
            client = SignerClient(
                url="https://mainnet.zklighter.elliot.ai",
                account_index=index,
                api_private_keys={0: private_key}
            )

            # 尝试获取账户信息
            # 注意：如果索引不存在，这里通常会直接抛出异常
            print("   ✅ 客户端初始化成功，尝试验证...")
            if client.check_client() is None:
                print(f"   🎉 成功！您的正确 Account Index 是: {index}")
                print(f"   👉 请修改 .env 文件: LIGHTER_ACCOUNT_INDEX={index}")
                found = True
                break
            else:
                print("   ❌ 验证失败")
        except Exception as e:
            print(f"   ❌ 失败: 该索引无效 ({str(e)})")

    if not found:
        print("\n❌ 未找到有效账户。")
        print("原因可能是：")
        print("1. 该 API Key 对应的钱包从未在 Lighter 官网【Deposit/存款】过资金。")
        print("2. API Key 私钥填错了（请重新去官网生成一个新的）。")

if __name__ == "__main__":
    asyncio.run(main())
