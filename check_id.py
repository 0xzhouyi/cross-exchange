import requests
import json

def get_lighter_markets():
    print("🕵️‍♂️ 正在查询 Lighter 市场列表 (伪装模式)...")
    url = "https://mainnet.zklighter.elliot.ai/api/v1/markets"
    
    # === 关键修改：加入伪装头 ===
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            markets = resp.json()
            print(f"\n✅ 获取成功! 共 {len(markets)} 个市场:\n")
            
            for m in markets:
                print(f"   [ID: {m.get('index')}] Symbol: {m.get('symbol')} (Type: {m.get('type')})")
        else:
            print(f"❌ 依然被拦截: HTTP {resp.status_code}")
            # 如果这里还是 403，说明是硬性 IP 封锁
    except Exception as e:
        print(f"❌ 错误: {e}")

if __name__ == "__main__":
    get_lighter_markets()
