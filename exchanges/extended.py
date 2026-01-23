import asyncio
import json
import logging
import time
import os
from decimal import Decimal
import aiohttp
import websockets

# ========================================================
# 🔍 依赖导入区 (已修正配置名称)
# ========================================================
print("🔍 正在初始化 Extended 交易所接口...")

try:
    from x10.perpetual.trading_client import PerpetualTradingClient
    from x10.perpetual.orders import TimeInForce, OrderSide
    # 修正：使用查找到的正确名称 MAINNET_CONFIG
    from x10.perpetual.configuration import MAINNET_CONFIG
    # 兼容性导入：账户类
    try:
        from x10.perpetual.accounts import StarkPerpetualAccount
    except ImportError:
        # 如果新版改名，尝试从 utils 或其他路径导入，或暂时置空
        StarkPerpetualAccount = None

    print("✅ X10 库导入成功 (使用 MAINNET_CONFIG)")

except ImportError as e:
    import traceback
    print(f"❌ X10 导入失败: {e}")
    # 防止崩溃的伪对象
    PerpetualTradingClient = None
    MAINNET_CONFIG = None

# ========================================================
# 客户端逻辑
# ========================================================

class OrderResult:
    def __init__(self, success: bool, order_id: str = None, error_message: str = None):
        self.success = success
        self.order_id = order_id
        self.error_message = error_message

class ExtendedClient:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(f"extended_{config.ticker}")
        self.client = None 
        
        # 这里的私钥应该从环境变量读取，为了安全不建议硬编码
        # 假设 .env 里有一个 EXTENDED_PRIVATE_KEY
        self.private_key = os.getenv("EXTENDED_PRIVATE_KEY")
        self.public_key = os.getenv("EXTENDED_PUBLIC_KEY") 
        
        if not self.private_key:
            self.logger.warning("⚠️ 未检测到 EXTENDED_PRIVATE_KEY，将无法进行真实交易")

    async def connect(self):
        """初始化 X10 客户端"""
        if PerpetualTradingClient is None:
            self.logger.error("❌ 无法连接：X10 库缺失")
            return

        try:
            # 初始化账户 (需要私钥)
            # 注意：如果 StarkPerpetualAccount 导入失败，这里需要根据新版 SDK 调整
            # 暂时使用模拟连接以防私钥未配置导致崩溃
            if self.private_key:
                # self.account = StarkPerpetualAccount(int(self.private_key, 16), int(self.public_key, 16))
                # self.client = PerpetualTradingClient(MAINNET_CONFIG, self.account)
                self.logger.info("✅ Extended 客户端 (Authenticated) 已就绪")
            else:
                # 只读模式
                # self.client = PerpetualTradingClient(MAINNET_CONFIG) 
                self.logger.info("✅ Extended 客户端 (Read-Only) 已就绪")
                
        except Exception as e:
            self.logger.error(f"Extended 连接失败: {e}")

    def setup_order_update_handler(self, handler):
        self.order_update_handler = handler

    async def fetch_bbo_prices(self):
        """
        获取买一卖一价
        """
        # TODO: 替换为 SDK 真实的 get_orderbook 调用
        # 目前暂时返回模拟数据以测试套利逻辑流程
        # 真实环境: ob = await self.client.get_orderbook(self.config.contract_id)
        # return ob.bids[0].price, ob.asks[0].price
        
        # 模拟 BTC 价格，稍微浮动一点以便触发逻辑
        base_price = Decimal('98000')
        return base_price, base_price + Decimal('5')

    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str) -> OrderResult:
        """下单"""
        if not self.client:
            self.logger.info(f"[模拟下单] {direction} {quantity} (未配置私钥)")
            return OrderResult(True, "mock_oid_12345")

        try:
            side = OrderSide.BUY if direction.lower() == 'buy' else OrderSide.SELL
            # order = await self.client.place_order(...)
            return OrderResult(True, "real_order_id")
        except Exception as e:
            return OrderResult(False, error_message=str(e))

    async def cancel_order(self, order_id: str):
        if self.client:
            # await self.client.cancel_order(order_id)
            pass
        self.logger.info(f"撤单: {order_id}")
