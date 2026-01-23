import os
import asyncio
import aiohttp
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()

async def main():
    raw_key = os.getenv('API_KEY_PRIVATE_KEY')
    if not raw_key:
        print("❌ 错误: .env 为空")
        return

    # === 自动修复逻辑 ===
    # 移除 0x 前缀
    if raw_key.startswith("0x"):
        raw_key = raw_key[2:]
    
    # 如果长度是 80 (40字节)，说明包含了额外的 Salt，自动截取前 64 (32字节)
    if len(raw_key) == 80:
        print(f"⚠️  检测到您填入了 Lighter 原始格式 (80字符)")
        print(f"✂️  脚本正在自动截取前 64 个字符作为私钥...")
        private_key = raw_key[:64]
    elif len(raw_key) == 64:
        private_key = raw_key
    else:
        print(f"❌ 错误: 密钥长度异常 ({len(raw_key)} 字符)。标准私钥应为 64 字符。")
        return
    # ==================

    try:
        account = Account.from_key(private_key)
        my_address = account.address
        print(f"🔑 解析成功！")
        print(f"👛 对应的钱包地址: {my_address}")
    except Exception as e:
        print(f"❌ 私钥解析失败: {e}")
        return

    # 查询 Lighter
    url = f"https://mainnet.zklighter.elliot.ai/api/v1/accountsByL1Address?l1Address={my_address}"
    print(f"📡 正在查询 Lighter 账户索引...")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                data = await response.json()
                if not data:
                    print("\n❌ 未找到账户！")
                    print("👉 请确认您是否已在 Lighter.xyz 使用此钱包【Deposit】过资金。")
                else:
                    print(f"\n✅ 找到账户！")
                    for acc in data:
                        print(f"🔥 您的 Account Index 是: 【 {acc.get('index')} 】")
                        print(f"📝 请在 .env 中设置: LIGHTER_ACCOUNT_INDEX={acc.get('index')}")
                        print(f"📝 请在 .env 中设置: API_KEY_PRIVATE_KEY={private_key}")
                        print("   (注意：请把截取后的 64位短私钥 更新进 .env 文件)")
        except Exception as e:
            print(f"网络错误: {e}")

if __name__ == "__main__":
    asyncio.run(main())
