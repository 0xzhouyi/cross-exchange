import asyncio
import logging

class BaseExchange:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.positions = {}
        self.orders = {}
        self.balance = {}

    async def connect(self):
        """建立连接"""
        raise NotImplementedError("Subclasses must implement connect()")

    async def create_order(self, symbol: str, side: str, price: float, size: float, order_type="limit"):
        """下单"""
        raise NotImplementedError("Subclasses must implement create_order()")

    async def cancel_order(self, order_id: str, symbol: str = None):
        """撤单"""
        raise NotImplementedError("Subclasses must implement cancel_order()")

    async def get_balance(self):
        """查询余额"""
        pass

    async def get_orderbook(self, symbol: str):
        """获取订单薄"""
        pass

    async def close(self):
        """关闭连接"""
        pass
