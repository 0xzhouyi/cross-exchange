import asyncio
import time
import os
import sys
import logging
from decimal import Decimal
from dotenv import load_dotenv
import websockets
import json

# 加载环境变量
load_dotenv(override=True)

# 导入客户端
try:
    from lighter.signer_client import SignerClient
    from exchanges.extended import ExtendedClient
    # 🔥 修复 1: 导入 AccountApi
    from lighter import ApiClient, Configuration, OrderApi, AccountApi
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    sys.exit(1)

# 配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("LatencyTest")

# 是否测试真实下单 (注意：可能会有极小成本)
TEST_REAL_ORDER = True 
TICKER = "BTC"
SIZE = 0.0001  # 测试数量

class LatencyTester:
    def __init__(self):
        self.lighter_client = None
        self.extended_client = None
        self.lighter_ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        
        # 记录时间戳
        self.order_sent_time = {}
        self.ws_received_time = {}

    async def init_clients(self):
        # 1. Lighter
        api_key = os.getenv('API_KEY_PRIVATE_KEY')
        if api_key and api_key.startswith("0x"): api_key = api_key[2:]
        
        # 获取配置
        acc_idx = int(os.getenv('LIGHTER_ACCOUNT_INDEX', 0))
        key_idx = int(os.getenv('LIGHTER_API_KEY_INDEX', 0))
        
        self.lighter_client = SignerClient(
            url="https://mainnet.zklighter.elliot.ai",
            account_index=acc_idx,
            api_private_keys={key_idx: api_key}
        )
        
        # 2. Extended
        class Config:
            def __init__(self, ticker):
                self.ticker = ticker
                self.quantity = Decimal('0')
                self.tick_size = Decimal('0.1')
        self.extended_client = ExtendedClient(Config(TICKER))
        await self.extended_client.get_contract_attributes()
        # Extended WS 连接通常在 connect() 里自动处理，这里我们需要手动挂载监听
        self.extended_client.setup_order_update_handler(self.on_extended_ws_event)
        asyncio.create_task(self.extended_client.connect())

    async def on_extended_ws_event(self, data):
        """Extended WS 回调"""
        if data.get('status') in ['OPEN', 'NEW', 'FILLED']:
            oid = data.get('order_id')
            if oid in self.order_sent_time:
                recv_time = time.time() * 1000
                send_time = self.order_sent_time[oid]
                latency = recv_time - send_time
                logger.info(f"⚡ [Extended] WS 推送延迟: {latency:.2f} ms (Order {oid})")

    async def measure_http_rtt(self):
        print("\n=== 📡 阶段 1: HTTP API 往返延迟 (RTT) ===")
        
        # --- Lighter RTT ---
        # 🔥 修复 2: 使用 AccountApi 进行标准的 API 请求
        try:
            # 利用 SignerClient 内部已初始化的 api_client
            account_api = AccountApi(self.lighter_client.api_client)
            
            start = time.time()
            # 查询账户详情作为“Ping”
            await account_api.account(by="index", value=str(self.lighter_client.account_index))
            end = time.time()
            print(f"✅ Lighter API RTT:  {(end-start)*1000:.2f} ms")
        except Exception as e:
            print(f"❌ Lighter RTT 失败: {e}")

        # --- Extended RTT ---
        try:
            start = time.time()
            await self.extended_client.fetch_bbo_prices()
            end = time.time()
            print(f"✅ Extended API RTT: {(end-start)*1000:.2f} ms")
        except Exception as e:
            print(f"❌ Extended RTT 失败: {e}")

    async def measure_order_latency(self):
        if not TEST_REAL_ORDER:
            print("\n=== ⚠️ 跳过真实下单测试 (TEST_REAL_ORDER=False) ===")
            return

        print("\n=== 🚀 阶段 2: 真实下单 & 撤单延迟 ===")
        
        # 获取最新价格以便挂远一点的单 (Maker)
        ext_bid, ext_ask = await self.extended_client.fetch_bbo_prices()
        
        # --- Extended 下单测试 ---
        price = ext_bid * Decimal('0.5') # 半价挂单，确保不成交
        print(f"正在 Extended 挂单 Buy {SIZE} @ {price:.2f} (Maker)...")
        
        start_req = time.time() * 1000
        res = await self.extended_client.place_open_order(f"{TICKER}-USD", Decimal(str(SIZE)), 'buy')
        end_req = time.time() * 1000
        
        if res.success:
            http_lat = end_req - start_req
            self.order_sent_time[res.order_id] = start_req # 记录发送时间用于计算 WS 延迟
            print(f"✅ Extended 下单成功! HTTP 耗时: {http_lat:.2f} ms")
            
            # 立即撤单
            await asyncio.sleep(0.5) 
            start_cancel = time.time()
            await self.extended_client.cancel_order(res.order_id)
            print(f"Extended 撤单指令已发送")
        else:
            print(f"❌ Extended 下单失败: {res.error_message}")

        print("注: Lighter 下单测试略过，以免市价单误成交。建议通过日志观察 arbitrage_v2.py 的 '对冲已提交' 到 '确认成交' 的时间差。")

    async def run(self):
        await self.init_clients()
        print("正在初始化连接，请稍候...")
        await asyncio.sleep(2) # 等待 WS 连接
        
        await self.measure_http_rtt()
        await self.measure_order_latency()
        
        print("\n测试结束，3秒后自动退出...")
        await asyncio.sleep(3)

if __name__ == "__main__":
    tester = LatencyTester()
    try:
        asyncio.run(tester.run())
    except KeyboardInterrupt:
        pass
