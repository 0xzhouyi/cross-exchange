import asyncio
import logging
import json
from curl_cffi.requests import AsyncSession
from exchanges.base import BaseExchange

class VariationalPrivateExchange(BaseExchange):
    def __init__(self, token: str, cookie: str, proxy: str = None):
        super().__init__()
        self.base_url = "https://omni.variational.io/api"
        self.logger = logging.getLogger("Variational")
        self.proxy = proxy
        self.token = token
        self.cookie = cookie
        self.session = None
        
        # 伪装配置 (curl_cffi 会自动处理指纹，这里只填鉴权信息)
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": self.token,
            "Cookie": self.cookie,
            "Origin": "https://trade.variational.io",
            "Referer": "https://trade.variational.io/"
        }

    async def connect(self):
        # 模拟 Chrome 浏览器指纹
        self.session = AsyncSession(
            impersonate="chrome120", 
            headers=self.headers,
            proxies={"http": self.proxy, "https": self.proxy} if self.proxy else None,
            timeout=15
        )
        self.logger.info("Variational Session Initialized")

    def _get_instrument_payload(self, symbol: str):
        """
        根据 symbol (如 ETH-PERP) 构建 Variational 需要的复杂 instrument 对象
        """
        # 简单的解析逻辑：假设 symbol 格式为 "BTC-..." 或 "ETH-..."
        underlying = "BTC" if "BTC" in symbol.upper() else "ETH"
        
        return {
            "underlying": underlying,
            "funding_interval_s": 3600,
            "settlement_asset": "USDC",
            "instrument_type": "perpetual_future"
        }

    async def get_indicative_quote(self, symbol: str, size: float):
        """
        第一步：询价 (获取 quote_id)
        """
        url = f"{self.base_url}/quotes/indicative"
        
        payload = {
            "instrument": self._get_instrument_payload(symbol),
            "qty": str(size) # API 要求数量是字符串格式
        }

        try:
            self.logger.info(f"正在询价 (Quote): {symbol} {size}...")
            resp = await self.session.post(url, json=payload)
            
            if resp.status_code == 200:
                data = resp.json()
                quote_id = data.get("quote_id")
                price = data.get("mark_price")
                self.logger.info(f"询价成功! Quote ID: {quote_id} | 参考价: {price}")
                return data
            else:
                self.logger.error(f"询价失败 [{resp.status_code}]: {resp.text}")
                return None
        except Exception as e:
            self.logger.error(f"询价异常: {e}")
            return None

    async def create_order(self, symbol: str, side: str, price: float, size: float, order_type="limit"):
        """
        第二步：正式下单
        """
        # 1. 先去询价拿到 Quote ID
        quote_data = await self.get_indicative_quote(symbol, size)
        if not quote_data or "quote_id" not in quote_data:
            self.logger.error("无法获取报价，停止下单")
            return None
            
        quote_id = quote_data["quote_id"]
        
        # 2. 发送下单请求
        url = f"{self.base_url}/orders/new/market"
        
        payload = {
            "quote_id": quote_id,
            "side": side.lower(),  # "buy" or "sell"
            "max_slippage": 0.0008 # 滑点保护，默认 0.08%
        }
        
        try:
            self.logger.info(f"正在下单 (Order): {side} {size} (Quote: {quote_id})")
            resp = await self.session.post(url, json=payload)
            
            if resp.status_code in [200, 201]:
                data = resp.json()
                self.logger.info(f"✅ 下单成功: {data}")
                return data
            else:
                self.logger.error(f"❌ 下单失败 [{resp.status_code}]: {resp.text}")
                return None
        except Exception as e:
            self.logger.error(f"下单异常: {e}")
            return None

    async def get_balance(self):
        """获取余额"""
        url = f"{self.base_url}/settlement_pools/details"
        try:
            resp = await self.session.get(url)
            if resp.status_code == 200:
                data = resp.json()
                self.logger.info(f"余额获取成功: {str(data)[:50]}...") # 只打印前50字符防止刷屏
                return data
            else:
                self.logger.error(f"余额获取失败 [{resp.status_code}]")
                return None
        except Exception as e:
            self.logger.error(f"余额请求异常: {e}")
            return None

    async def close(self):
        if self.session:
            await self.session.close()
