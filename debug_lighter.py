import asyncio
import aiohttp
import json

async def main():
    # ==========================================
    # 👇👇👇 请在这里粘贴您的钱包地址 👇👇👇
    # 比如: my_address = "0x1234..."
    my_address = "0x28fce3a4aa63abb62e20a76f36041e21fa142a14"
    # ==========================================

    if my_address == "REPLACE_ME":
        print("❌ 错误：您忘记修改代码里的地址了！请先修改 my_address 变量。")
        return

    # 1. 自动清洗数据（去空格，转小写）
    clean_address = my_address.strip().lower()
    print(f"🧹 清洗后的地址: {clean_address}")

    # 2. 查询 API
    url = f"https://mainnet.zklighter.elliot.ai/api/v1/accountsByL1Address?l1Address={clean_address}"
    print(f"📡 请求 URL: {url}")
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            text = await response.text()
            print(f"📝 服务器返回: {text}")
            
            try:
                data = json.loads(text)
                # 检查是否包含账号列表
                if isinstance(data, list) and len(data) > 0:
                    print("\n🎉 成功找到账号！")
                    account = data[0]
                    # 获取 index，兼容不同字段名
                    idx = account.get('index') or account.get('accountIndex')
                    print("========================================")
                    print(f"✅ 您的 LIGHTER_ACCOUNT_INDEX 是: {idx}")
                    print("========================================")
                    print("👉 请立即把这个数字填入 .env 文件！")
                elif isinstance(data, dict) and data.get('code'):
                     print(f"❌ API 依然报错: {data.get('message')}")
                else:
                    print("❌ 查询结果为空。这意味着该地址在 Lighter 没有账号。")
                    print("   请确认：您是否用这个钱包去 Lighter.xyz 官网【Deposit】过？")
            except:
                pass

if __name__ == "__main__":
    asyncio.run(main())
