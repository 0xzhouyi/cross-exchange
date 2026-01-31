import asyncio
import logging
import sys
import time
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
except ImportError as e:
    print(f"❌ 导入库失败: {e}")
    sys.exit(1)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("DataCollector")

class DataCollector:
    def __init__(self, ticker="BTC"):
        self.ticker = ticker.upper()
        self.stop_flag = False
        
        # 价格缓存
        self.ext_bid = Decimal('0')
        self.ext_ask = Decimal('0')
        self.lighter_bid = Decimal('0')
        self.lighter_ask = Decimal('0')
        
        # Lighter 配置
        self.lighter_market_id = 1 # BTC
        self.lighter_ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        
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
        logger.info(f"💾 数据将保存至: {self.csv_file}")

    def log_data(self):
        """记录一行数据"""
        # 只有当两边都有有效价格时才记录
        if self.ext_bid > 0 and self.lighter_bid > 0:
            # 计算价差
            # Long 方向: Lighter Bid - Ext Ask (在 Ext 买，在 Lighter 卖)
            spread_long = self.lighter_bid - self.ext_ask
            # Short 方向: Ext Bid - Lighter Ask (在 Lighter 买，在 Ext 卖)
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
        # === 🔥 修复 1: 正确定义 Config 类 ===
        class Config:
            def __init__(self, ticker):
                self.ticker = ticker
                self.quantity = Decimal('0')
                self.tick_size = Decimal('0.1')
        
        # 实例化 Extended 客户端
        self.extended_client = ExtendedClient(Config(self.ticker))
        
        # 连接 Extended
        await self.extended_client.get_contract_attributes()
        asyncio.create_task(self.extended_client.connect())
        logger.info("✅ Extended 连接成功")

    async def run_lighter_ws(self):
        """Lighter WebSocket 监听 (修复心跳版)"""
        while not self.stop_flag:
            try:
                # === 🔥 修复 2: 增加 ping_interval 配置防止超时 ===
                async with websockets.connect(self.lighter_ws_url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("✅ Lighter WS 连接成功")
                    
                    # 订阅订单簿
                    await ws.send(json.dumps({
                        "type": "subscribe", 
                        "channel": f"order_book/{self.lighter_market_id}"
                    }))
                    
                    async for raw_msg in ws:
                        if self.stop_flag: break
                        
                        # === 🔥 修复 3: 处理纯文本 Ping ===
                        if raw_msg == "ping":
                            await ws.send("pong")
                            continue
                            
                        # 解析 JSON
                        try:
                            data = json.loads(raw_msg)
                        except:
                            continue

                        # === 🔥 修复 4: 处理 JSON 格式 Ping ===
                        if isinstance(data, dict) and data.get("type") == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
                            continue

                        # 处理业务数据
                        if "payload" in data or "order_book" in data:
                            payload = data.get('payload') or data.get('order_book') or {}
                            bids = payload.get('bids', [])
                            asks = payload.get('asks', [])
                            
                            # 更新买一价
                            if bids: 
                                # 兼容不同格式 [price, size] 或 {"price":...}
                                p = bids[0][0] if isinstance(bids[0], list) else bids[0].get('price')
                                self.lighter_bid = Decimal(str(p))
                            
                            # 更新卖一价
                            if asks: 
                                p = asks[0][0] if isinstance(asks[0], list) else asks[0].get('price')
                                self.lighter_ask = Decimal(str(p))
                                
            except Exception as e:
                logger.error(f"Lighter WS 错误: {e} (5秒后重连)")
                await asyncio.sleep(5)

    async def run(self):
        await self.initialize_clients()
        asyncio.create_task(self.run_lighter_ws())
        
        logger.info("⏳ 开始收集数据... (输出已精简，详细数据见 CSV)")
        
        while not self.stop_flag:
            try:
                # 定期从 Extended 获取最新价格 (ExtendedClient 内部有缓存)
                self.ext_bid, self.ext_ask = await self.extended_client.fetch_bbo_prices()
                
                # 记录数据到 CSV
                self.log_data()
                
                # 打印实时状态 (仅在数据有效时)
                if self.lighter_bid > 0 and self.ext_ask > 0:
                    spread_l = self.lighter_bid - self.ext_ask
                    spread_s = self.ext_bid - self.lighter_ask
                    
                    # 动态打印，不换行
                    print(f"\rExt: {self.ext_bid:.1f}/{self.ext_ask:.1f} | Lighter: {self.lighter_bid:.1f}/{self.lighter_ask:.1f} | Open: {spread_l:+.1f} | Close: {spread_s:+.1f}   ", end="")
                else:
                    print(f"\r⏳ 等待数据同步...   ", end="")
                
                # 采样频率：0.5秒一次
                await asyncio.sleep(0.5)
                
            except KeyboardInterrupt:
                self.stop_flag = True
                print("\n🛑 正在停止...")
                break
            except Exception as e:
                logger.error(f"主循环错误: {e}")
                await asyncio.sleep(1)

if __name__ == "__main__":
    collector = DataCollector()
    try:
        asyncio.run(collector.run())
    except KeyboardInterrupt:
        pass
