import aiohttp
import asyncio
import json
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MarketMap")

async def fetch_markets():
    # Lighter 的 API 基础地址
    base_url = "https://mainnet.zklighter.elliot.ai"
    
    # 常见的市场信息端点 (根据经验猜测)
    endpoints = [
        "/markets",
        "/pairs",
        "/info",
        "/exchange-info",
        "/v1/markets",
        "/api/v1/markets"
    ]

    async with aiohttp.ClientSession() as session:
        found = False
        print("\n" + "="*50)
        print("🔍 开始扫描 Lighter API 市场配置...")
        print("="*50)

        for ep in endpoints:
            url = base_url + ep
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"\n✅ 成功连接端点: {ep}")
                        print("-" * 30)
                        
                        # 尝试格式化输出，寻找 BTC-PERP
                        print(json.dumps(data, indent=2))
                        found = True
                        break # 找到了就停止
                    else:
                        print(f"❌ 端点 {ep} 返回状态码: {resp.status}")
            except Exception as e:
                print(f"⚠️ 访问 {ep} 失败: {e}")

        if not found:
            print("\n❌ 自动扫描失败。尝试通过 SDK 内部对象查找...")
            # 备选方案：如果有 SDK 环境，尝试打印 client.api_client 的属性
            # (这部分需要您在有 SDK 的环境运行，这里仅做 HTTP 探测)

if __name__ == "__main__":
    asyncio.run(fetch_markets())
