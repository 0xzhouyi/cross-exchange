"""
Extended exchange client interface.
"""
import asyncio
import logging
import os
import time
import json
import traceback
import aiohttp
import websockets
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta

# === 关键依赖导入 ===
try:
    from x10.perpetual.trading_client import PerpetualTradingClient
    from x10.perpetual.configuration import STARKNET_MAINNET_CONFIG
    from x10.perpetual.accounts import StarkPerpetualAccount
    from x10.perpetual.orders import TimeInForce, OrderSide
except ImportError:
    print("❌ 严重错误: 缺少 'x10' 库。请运行: pip install x10-python-trading-starknet==0.0.10")
    raise

def utc_now():
    return datetime.now(tz=timezone.utc)

async def _stream_worker(url, handler, stop_event, extra_headers=None):
    while not stop_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, extra_headers=extra_headers) as ws:
                async for raw in ws:
                    if raw == "ping": await ws.send("pong"); continue
                    try:
                        msg = json.loads(raw)
                    except: continue
                    if msg.get("type") == "PING": await ws.send(json.dumps({"type": "PONG"})); continue
                    await handler(msg)
        except Exception:
            await asyncio.sleep(3)

class ExtendedClient:
    def __init__(self, config):
        self.config = config 
        self.ticker = config.ticker if hasattr(config, 'ticker') else config.get('ticker')
        self.contract_id = f"{self.ticker}-USD"
        self.quantity = config.quantity if hasattr(config, 'quantity') else config.get('quantity', Decimal('0'))
        
        self.logger = logging.getLogger(f"extended_client_{self.ticker}")

        # 1. 读取 .env 配置
        vault = os.getenv('EXTENDED_VAULT')
        private_key = os.getenv('EXTENDED_STARK_KEY_PRIVATE') or os.getenv('EXTENDED_PRIVATE_KEY')
        public_key = os.getenv('EXTENDED_STARK_KEY_PUBLIC') or os.getenv('EXTENDED_PUBLIC_KEY')
        api_key = os.getenv('EXTENDED_API_KEY')
        
        if not private_key: raise ValueError("❌ 缺少 EXTENDED_PRIVATE_KEY")
        
        self.api_key = api_key

        # 2. 初始化 X10 实盘账户
        try:
            self.stark_account = StarkPerpetualAccount(
                vault=int(vault) if vault else 0, 
                private_key=private_key, 
                public_key=public_key, 
                api_key=api_key
            )
            self.stark_config = STARKNET_MAINNET_CONFIG
            self.perpetual_trading_client = PerpetualTradingClient(self.stark_config, self.stark_account)
            self.logger.info("✅ Extended (X10) 实盘客户端初始化成功")
        except Exception as e:
            self.logger.error(f"❌ 客户端初始化失败: {e}")
            raise

        self.orderbook = None
        self._stop_event = asyncio.Event()
        self._tasks = []
        self.open_orders = {} 
        self.min_order_size = Decimal("0.001") 

    async def get_contract_attributes(self):
        """查询交易所的合约配置，自动更新 tick_size 和 min_order_size"""
        try:
            markets = await self.perpetual_trading_client.markets_info.get_markets(market_names=[self.contract_id])
            if markets and hasattr(markets, 'data') and len(markets.data) > 0:
                market_data = markets.data[0]
                trading_config = market_data.trading_config
                
                real_tick_size = Decimal(str(trading_config.min_price_change))
                real_min_size = Decimal(str(trading_config.min_order_size))
                
                self.config.tick_size = real_tick_size
                self.min_order_size = real_min_size
                
                self.logger.info(f"✅ 合约属性同步成功: TickSize={real_tick_size}, MinQty={real_min_size}")
                return self.contract_id, real_tick_size
            else:
                self.logger.warning("⚠️ 无法获取合约属性，将使用默认值")
                return self.contract_id, Decimal("0.1")
        except Exception as e:
            self.logger.error(f"获取合约属性失败: {e}")
            return self.contract_id, Decimal("0.1")

    async def connect(self):
        """连接数据流"""
        self._stop_event.clear()
        host = STARKNET_MAINNET_CONFIG.stream_url
        self._tasks = [
            asyncio.create_task(_stream_worker(
                host + "/account", self.handle_account, self._stop_event,
                extra_headers=[("X-API-Key", self.api_key)]
            )),
            asyncio.create_task(_stream_worker(
                host + "/orderbooks/" + self.ticker + "-USD?depth=1",
                self.handle_orderbook, self._stop_event
            )),
        ]
        await asyncio.sleep(1) 

    async def fetch_bbo_prices(self) -> tuple[Decimal, Decimal]:
        """获取最优买卖价"""
        if self.orderbook:
            try:
                bid = Decimal(str(self.orderbook["bid"][0]["p"])) if self.orderbook["bid"] else Decimal('0')
                ask = Decimal(str(self.orderbook["ask"][0]["p"])) if self.orderbook["ask"] else Decimal('0')
                return bid, ask
            except: pass
        
        try:
            ob = await self.perpetual_trading_client.markets_info.get_orderbook(market_name=self.contract_id)
            if ob and hasattr(ob, 'bids') and ob.bids:
                return Decimal(str(ob.bids[0].p)), Decimal(str(ob.asks[0].p))
        except: pass
        return Decimal('0'), Decimal('0')

    def round_to_tick(self, price: Decimal) -> str:
        tick = self.config.tick_size
        return str(price.quantize(tick, rounding=ROUND_HALF_UP))

    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str):
        """发送真实订单"""
        try:
            best_bid, best_ask = await self.fetch_bbo_prices()
            if best_bid == 0: return PlaceResult(False, None, "无价格数据")

            # Maker 策略
            tick_size = self.config.tick_size
            if direction == 'buy':
                price = best_ask - tick_size
                side = OrderSide.BUY
            else:
                price = best_bid + tick_size
                side = OrderSide.SELL
            
            final_price_str = self.round_to_tick(price)
            self.logger.info(f"🚀 [实盘] 发送 Extended 订单: {direction} {quantity} @ {final_price_str}")

            price_decimal = Decimal(final_price_str)
            qty_decimal = Decimal(str(quantity))

            res = await self.perpetual_trading_client.place_order(
                market_name=contract_id,
                amount_of_synthetic=qty_decimal,
                price=price_decimal,
                side=side,
                time_in_force=TimeInForce.GTT,
                post_only=True,
                expire_time=utc_now() + timedelta(hours=1)
            )
            
            if res and res.status == 'OK':
                order_id = res.data.id
                self.logger.info(f"✅ 下单成功! ID: {order_id}")
                return PlaceResult(True, order_id, None)
            else:
                 msg = "未知错误"
                 if hasattr(res, 'error'): msg = res.error
                 return PlaceResult(False, None, f"API拒绝: {msg}")

        except Exception as e:
            self.logger.error(f"❌ 下单异常: {e}")
            return PlaceResult(False, None, str(e))
            
        return PlaceResult(False, None, "未知错误")

    # === 🔥 关键修正：深入 orders 模块查找取消方法 ===
    async def cancel_order(self, order_id):
        try:
            # 1. 尝试从 client.orders 模块调用 cancel (可能性最高)
            if hasattr(self.perpetual_trading_client, 'orders'):
                orders_module = self.perpetual_trading_client.orders
                if hasattr(orders_module, 'cancel_order'):
                    await orders_module.cancel_order(order_id=int(order_id))
                    self.logger.info(f"🗑️ 订单 {order_id} 已发送取消请求 (via orders.cancel_order)")
                    return True
                elif hasattr(orders_module, 'cancel'):
                    await orders_module.cancel(order_id=int(order_id))
                    self.logger.info(f"🗑️ 订单 {order_id} 已发送取消请求 (via orders.cancel)")
                    return True
            
            # 2. 如果上面都失败，再次尝试直接调用（复数）
            if hasattr(self.perpetual_trading_client, 'cancel_orders'):
                 await self.perpetual_trading_client.cancel_orders(order_ids=[int(order_id)])
                 return True

            # 3. 终极调试：如果还不行，打印 orders 模块里的东西
            if hasattr(self.perpetual_trading_client, 'orders'):
                methods = [m for m in dir(self.perpetual_trading_client.orders) if not m.startswith('_')]
                self.logger.error(f"❌ 依然找不到取消方法。Orders模块可用属性: {methods}")
            else:
                self.logger.error("❌ client.orders 模块不存在")
                
            return False

        except Exception as e:
            self.logger.error(f"❌ 取消订单失败: {e}")
            return False

    # WS 回调处理
    async def handle_orderbook(self, msg):
        if isinstance(msg, str): msg = json.loads(msg)
        if msg.get("type") == "SNAPSHOT":
            d = msg.get('data', {})
            self.orderbook = {'bid': d.get('b', []), 'ask': d.get('a', [])}

    async def handle_account(self, msg):
        if isinstance(msg, str): msg = json.loads(msg)
        if msg.get("type") == "ORDER":
            orders = msg.get('data', {}).get('orders', [])
            for order in orders:
                if self._order_update_handler:
                    status = order.get('status')
                    if status == "NEW": status = "OPEN"
                    if status == "CANCELLED": status = "CANCELED"
                    self._order_update_handler({
                        'order_id': order.get('id'),
                        'status': status,
                        'side': order.get('side', '').lower(),
                        'filled_size': order.get('filledQty'),
                        'price': order.get('price')
                    })

    def setup_order_update_handler(self, callback):
        self._order_update_handler = callback

class PlaceResult:
    def __init__(self, success, order_id, error_message):
        self.success = success
        self.order_id = order_id
        self.error_message = error_message
