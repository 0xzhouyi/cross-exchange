import asyncio
import logging
import sys
import os
import csv
from datetime import datetime
from decimal import Decimal
from dotenv import load_dotenv
import websockets
import json

# 加载环境变量
load_dotenv(override=True)

# 导入必要的库
try:
    from exchanges.extended import ExtendedClient
    # 引入 Lighter SDK 用于动态查询 ID
    from lighter import ApiClient, Configuration, OrderApi 
except ImportError as e:
    print(f"❌ 导入库失败: {e}")
    print("请确保已安装依赖: pip install lighter-v1-python")
    sys.exit(1)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class DataCollector:
    def __init__(self, ticker):
        self.ticker = ticker.upper()
        self.stop_flag = False
        self.logger = logging.getLogger(f"Collector_{self.ticker}")
        
        # 价格缓存
        self.ext_bid = Decimal('0')
        self.ext_ask = Decimal('0')
        self.lighter_bid = Decimal('0')
        self.lighter_ask = Decimal('0')
        
        # 初始化为 None，稍后动态获取
        self.lighter_market_id = None 
        
        self.lighter_ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        self.lighter_api_url = "https://mainnet.zklighter.elliot.ai"
        
        # CSV 文件设置
        self.csv_file = f"spread_data_{self.ticker}.csv"
        self._init_csv()

        # 客户端
        self.extended_client = None

    def _init_csv(self):
        """初始化 CSV 文件"""
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 
                    'ext_bid', 'ext_ask', 
                    'lighter_bid', 'lighter_ask', 
                    'spread_long', 'spread_short'
                ])
    
    async def fetch_lighter_market_id(self):
        """🔥 核心功能：动态查询 Lighter Market ID"""
        self.logger.info(f"🔍 正在查询 Lighter [{self.ticker}] 的 Market ID...")
        try:
            # 使用 SDK 连接 API
            conf = Configuration(host=self.lighter_api_url)
            api_client = ApiClient(configuration=conf)
            order_api = OrderApi(api_client)
            
            # 获取所有市场
            order_books = await order_api.order_books()
            
            # 寻找匹配的 Symbol
            found_market = None
            for market in order_books.order_books:
                # 1. 精确匹配 (如 "BTC")
                if market.symbol == self.ticker:
                    found_market = market
                    break
                # 2. 常见后缀匹配 (如 "BTC-USD")
                if market.symbol == f"{self.ticker}-USD":
                    found_market = market
                    break
                # 3. 包含匹配 (如 "ETH" 匹配 "ETH/USDC")
                if self.ticker in market.symbol.split('/')[0]:
                    found_market = market
                    # 不 break，继续找更精确的，或者就用这个
            
            if found_market:
                self.lighter_market_id = found_market.market_id
                self.logger.info(f"✅ 成功找到 Market ID: {self.lighter_market_id} (Symbol: {found_market.symbol})")
            else:
                available_symbols = [m.symbol for m in order_books.order_books]
                self.logger.error(f"❌ 未找到 {self.ticker} 对应的市场！可用市场: {available_symbols}")
                self.stop_flag = True
                
            await api_client.close()
            
        except Exception as e:
            self.logger.error(f"❌ 查询 Market ID 失败: {e}")
            self.stop_flag = True

    def log_data(self):
        """记录一行数据"""
        if self.ext_bid > 0 and self.lighter_bid > 0:
            spread_long = self.lighter_bid - self.ext_ask
            spread_short = self.ext_bid - self.lighter_ask
            timestamp = datetime.now().isoformat()
            
            with open(self.csv_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp,
                    self.ext_bid, self.ext_ask,
                    self.lighter_bid, self.lighter_ask,
                    spread_long, spread_short
                ])

    async def initialize_clients(self):
        class Config:
            def __init__(self, ticker):
                self.ticker = ticker
                self.quantity = Decimal('0')
                self.tick_size = Decimal('0.1')
        
        self.extended_client = ExtendedClient(Config(self.ticker))
        await self.extended_client.get_contract_attributes()
        asyncio.create_task(self.extended_client.connect())
        self.logger.info(f"✅ [{self.ticker}] Extended 连接成功")

    async def run_lighter_ws(self):
        """Lighter WebSocket 监听"""
        # 等待 ID 获取成功
        while self.lighter_market_id is None and not self.stop_flag:
            await asyncio.sleep(0.5)
            
        if self.stop_flag: return

        while not self.stop_flag:
            try:
                async with websockets.connect(self.lighter_ws_url, ping_interval=20, ping_timeout=20) as ws:
                    self.logger.info(f"✅ [{self.ticker}] Lighter WS 已连接 (订阅 ID: {self.lighter_market_id})")
                    
                    await ws.send(json.dumps({
                        "type": "subscribe", 
                        "channel": f"order_book/{self.lighter_market_id}"
                    }))
                    
                    async for raw_msg in ws:
                        if self.stop_flag: break
                        if raw_msg == "ping": await ws.send("pong"); continue
                            
                        try: data = json.loads(raw_msg)
                        except: continue

                        if isinstance(data, dict) and data.get("type") == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
                            continue

                        if "payload" in data or "order_book" in data:
                            payload = data.get('payload') or data.get('order_book') or {}
                            bids = payload.get('bids', [])
                            asks = payload.get('asks', [])
                            
                            if bids: 
                                p = bids[0][0] if isinstance(bids[0], list) else bids[0].get('price')
                                self.lighter_bid = Decimal(str(p))
                            if asks: 
                                p = asks[0][0] if isinstance(asks[0], list) else asks[0].get('price')
                                self.lighter_ask = Decimal(str(p))
                                
            except Exception as e:
                self.logger.error(f"[{self.ticker}] Lighter WS 错误: {e} (5秒后重连)")
                await asyncio.sleep(5)

    async def run(self):
        # 1. 先获取 Market ID
        await self.fetch_lighter_market_id()
        if self.stop_flag: return

        # 2. 初始化 Extended
        await self.initialize_clients()
        
        # 3. 启动 WS
        asyncio.create_task(self.run_lighter_ws())
        
        self.logger.info(f"⏳ [{self.ticker}] 开始采集...")
        
        while not self.stop_flag:
            try:
                self.ext_bid, self.ext_ask = await self.extended_client.fetch_bbo_prices()
                self.log_data()
                
                if self.lighter_bid > 0 and self.ext_ask > 0:
                    spread_l = self.lighter_bid - self.ext_ask
                    spread_s = self.ext_bid - self.lighter_ask
                    # 打印格式：[ETH] Ext:2800.5/2800.6 | Lit:2805.0/2805.5 | Spr:+4.5/-5.0
                    print(f"[{self.ticker}] Ext:{self.ext_bid:.1f}/{self.ext_ask:.1f} | Lit:{self.lighter_bid:.1f}/{self.lighter_ask:.1f} | Spr:{spread_l:+.1f}/{spread_s:+.1f}")
                
                await asyncio.sleep(1.0)
                
            except Exception as e:
                self.logger.error(f"[{self.ticker}] 循环错误: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        self.stop_flag = True

async def main():
    print("🚀 启动双币种智能采集器 (BTC & ETH)...")
    
    # 实例化采集器
    # 注意：这里使用 "ETH"，代码会自动匹配到 API 返回的 "ETH" 或 "ETH/USDC"
    btc_collector = DataCollector("BTC")
    eth_collector = DataCollector("ETH") 
    
    try:
        # 并发运行
        await asyncio.gather(
            btc_collector.run(),
            eth_collector.run()
        )
    except asyncio.CancelledError:
        pass
    finally:
        await btc_collector.stop()
        await eth_collector.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 程序已停止")
