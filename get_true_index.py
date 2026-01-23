import asyncio
import aiohttp
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

async def main():
    print("==================================================")
    print("🛠️  Lighter 账户索引查询工具 (终极版)")
    print("==================================================")

    # 1. 验证 .env 中的私钥格式 (只做格式检查，不用于查询地址)
    raw_key = os.getenv('API_KEY_PRIVATE_KEY')
    final_private_key = ""
    
    if not raw_key:
        print("❌ 错误: .env 中未找到 API_KEY_PRIVATE_KEY")
        return

    # 移除 0x
    if raw_key.startswith("0x"): raw_key = raw_key[2:]

    # 截取逻辑
    if len(raw_key) == 80:
        print(f"ℹ️  检测到原始长密钥 (80字符)，自动截取前 64 位使用。")
        final_private_key = raw_key[:64]
    elif len(raw_key) == 64:
        print(f"✅  密钥长度正确 (64字符)。")
        final_private_key = raw_key
    else:
        print(f"⚠️  警告: 密钥长度 {len(raw_key)} 非标准，可能导致签名失败。")
        final_private_key = raw_key

    print("--------------------------------------------------")
    
    # 2. 核心：手动输入主钱包地址
    print("请粘贴您的 MetaMask 主钱包地址")
    print("(即您在 Lighter 存钱的那个地址)")
    my_address = input("👉 请输入地址: ").strip()

    if not my_address.startswith("0x") or len(my_address) != 42:
        print("❌ 地址格式看起来不对，应该是 0x 开头的 42 位字符串")
        return

    # 3. 查询 Lighter API
    url = f"https://mainnet.zklighter.elliot.ai/api/v1/accountsByL1Address?l1Address={my_address}"
    print(f"\n📡 正在查询地址: {my_address} ...")
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            try:
                data = await response.json()
                
                if not data:
                    print("\n❌ 结果为空！")
                    print("原因：Lighter 找不到属于这个钱包的账户。")
                    print("解决：请确保您确实在 Lighter.xyz 连接了此钱包并存入了资金。")
                    return

                print("\n🎉 查询成功！请立即修改您的 .env 文件：")
                print("==================================================")
                
                # 兼容返回是列表还是字典
                accounts = data if isinstance(data, list) else [data]
                
                for acc in accounts:
                    # 获取索引
                    idx = acc.get('index')
                    if idx is None: idx = acc.get('accountIndex')
                    
                    print(f"✅ LIGHTER_ACCOUNT_INDEX={idx}")
                    print(f"✅ API_KEY_PRIVATE_KEY={final_private_key}")
                    print("==================================================")
                    print("(注意：PRIVATE_KEY 请使用上面显示的截取后的版本)")

            except Exception as e:
                print(f"❌ 解析错误: {e}")

if __name__ == "__main__":
    asyncio.run(main())
