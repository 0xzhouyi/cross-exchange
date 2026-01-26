import asyncio
import logging
import os
import sys
import time
import json
import traceback
from decimal import Decimal
from typing import Optional

import aiohttp
import websockets

# ==========================================
# 依赖库检查与导入
# ==========================================
print("🔄 正在加载依赖库...")

try:
    import x10.perpetual.trading_client
    from x10.perpetual.orders import OrderSide
    from x10.perpetual.configuration import MAINNET_CONFIG
    print("✅ X10 Trading Library 加载成功")
except ImportError as e:
    print(f"\n❌ 严重错误: 无法加载 X10 库 ({e})")

try:
    from lighter.signer_client import SignerClient
    print("✅ Lighter SDK 加载成功")
except ImportError:
    print("❌ 无法导入 lighter.signer_client，请检查 pip install lighter-sdk")
    sys.exit(1)

try:
    from exchanges.extended import ExtendedClient
    print("✅ Extended Client 加载成功")
except ImportError:
    print("❌ 无法导入 exchanges.extended，请检查文件是否存在")
    sys.exit(1)

print("--------------------------------------------------")

class Config:
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)

class ExtendedArb:
    def __init__(self, ticker: str, order_quantity: Decimal,
                 fill_timeout: int = 5, max_position: Decimal = Decimal('0'),
                 long_ex_threshold: Decimal = Decimal('10'),
                 short_ex_threshold: Decimal = Decimal('10')):
        
        self.ticker = ticker.upper()
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.max_position = max_position
        self.long_threshold = long_ex_threshold
        self.short_threshold = short_ex_threshold
        
        self.stop_flag = False
        self._setup_logger()

        # 状态变量
        self.extended_client: Optional[ExtendedClient] = None
        self.lighter_client: Optional[SignerClient] = None
        
        # 初始 ID 设为 None，等待雷达扫描结果
        self.lighter_market_id = None
        self.is_market_locked = False
        
        # 价格数据
        self.ext_bid = Decimal('0')
        self.ext_ask = Decimal('0')
        self.lighter_bid = Decimal('0')
        self.lighter_ask = Decimal('0')
        self.lighter_mark_price = Decimal('0')
        
        self.last_update_time = time.time()
        self.received_first_message = False

        # Lighter 连接配置
        self.lighter_ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        self.lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        
        try:
            raw_index = os.getenv('LIGHTER_ACCOUNT_INDEX', '0')
            self.account_index = int(raw_index)
            self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
        except ValueError:
            self.logger.error("❌ 环境变量 LIGHTER_ACCOUNT_INDEX 必须是整数")
            sys.exit(1)

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)
        self.logger = logging.getLogger(f"extended_arb_{self.ticker}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers = [] 
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)
        fh = logging.FileHandler(f"logs/extended_{self.ticker}.log")
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

    # ================= 客户端初始化 =================

    def initialize_lighter_client(self):
        raw_key = os.getenv('API_KEY_PRIVATE_KEY')
        if not raw_key: raise Exception("❌ .env 缺少 API_KEY_PRIVATE_KEY")
        if raw_key.startswith("0x"): raw_key = raw_key[2:]
        final_key = raw_key 

        self.logger.info(f"正在初始化 Lighter Client (Account Index: {self.account_index})...")
        try:
            self.lighter_client = SignerClient(
                url=self.lighter_base_url,
                account_index=self.account_index,
                api_private_keys={self.api_key_index: final_key}
            )
            self.logger.info("✅ Lighter client initialized successfully")
        except Exception as e:
            self.logger.error(f"❌ Lighter 初始化异常: {e}")
            raise

    def initialize_extended_client(self):
        self.logger.info("正在初始化 Extended Client...")
        config_dict = {'ticker': self.ticker, 'contract_id': f"{self.ticker}-USD", 'quantity': self.order_quantity}
        try:
            self.extended_client = ExtendedClient(Config(config_dict))
            asyncio.create_task(self.extended_client.connect())
            self.logger.info("✅ Extended client initialized")
        except Exception as e:
            self.logger.error(f"❌ Extended 初始化失败: {e}")
            raise

    # ================= WebSocket (雷达扫描核心) =================

    async def setup_websockets(self):
        self.extended_client.setup_order_update_handler(self.handle_extended_order_update)
        asyncio.create_task(self.run_lighter_ws())

    async def run_lighter_ws(self):
        """
        Lighter WS 监听循环
        🔥 策略：启动雷达，扫描 ID 0-4 的 market_stats，找到 BTC 后自动锁定
        """
        while not self.stop_flag:
            try:
                self.logger.info(f"Connecting to WS: {self.lighter_ws_url} ...")
                
                async with websockets.connect(
                    self.lighter_ws_url,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    self.logger.info("✅ Lighter WS connected (等待握手...)")
                    self.is_market_locked = False # 重置锁定状态

                    async for msg in ws:
                        data = json.loads(msg)
                        msg_type = data.get('type')

                        # 1. 握手成功 -> 启动雷达扫描
                        if msg_type == 'connected':
                            self.logger.info(f"🤝 握手成功! 启动 ID 扫描雷达 (0-4)...")
                            
                            # 一次性订阅前 5 个 ID 的 stats (流量很小，不用担心)
                            for i in range(5):
                                sub_stats = {
                                    "type": "subscribe",
                                    "channel": f"market_stats/{i}",
                                    "id": 200 + i
                                }
                                await ws.send(json.dumps(sub_stats))
                                await asyncio.sleep(0.05) # 微小延迟防止堵塞
                            
                            self.logger.info("📡 雷达已开启，正在寻找 BTC...")
                        
                        elif msg_type == 'subscribed':
                            # 忽略订阅成功的刷屏日志，保持清爽
                            pass

                        else:
                            await self._process_lighter_msg(ws, data)
                        
            except Exception as e:
                if "1008" in str(e):
                    self.logger.warning("⚠️ 连接超时重连中...")
                else:
                    self.logger.warning(f"Lighter WS Disconnected: {e}")
                await asyncio.sleep(5)

    async def _process_lighter_msg(self, ws, data):
        try:
            # 数据结构提取
            payload = data
            if 'data' in data: payload = data['data']
            if 'payload' in data: payload = data['payload']
            if data.get('type') == 'update' and 'data' in data: payload = data['data']

            # === 🔥 核心逻辑：智能识别 ID ===
            if not self.is_market_locked:
                # 检查 market_stats 里的 symbol
                stats = None
                if 'market_stats' in payload: stats = payload['market_stats']
                if 'symbol' in payload: stats = payload 

                if stats and 'symbol' in stats:
                    sym = stats.get('symbol', '').upper()
                    mid = stats.get('market_id')
                    price_str = stats.get('mark_price', '0')
                    price = Decimal(str(price_str))
                    
                    # 判断是否为目标币种 (BTC)
                    if 'BTC' in sym:
                        self.lighter_market_id = mid
                        self.is_market_locked = True
                        
                        # === 🔥 关键修正：锁定同时也立即更新价格，不要等待 ===
                        self.lighter_mark_price = price
                        # 用标记价格立即填充买卖价，确保主循环马上启动
                        self.lighter_bid = price - Decimal('0.5')
                        self.lighter_ask = price + Decimal('0.5')
                        
                        print("\n" + "="*60)
                        self.logger.info(f"🎉🎉🎉 找到目标! 锁定 Market ID: {mid} ({sym})")
                        self.logger.info(f"💰 当前标记价格: {price} (已同步至引擎)")
                        print("="*60 + "\n")
                        
                        # 立即订阅深度数据
                        sub_ob = {
                            "type": "subscribe",
                            "channel": f"order_book/{mid}",
                            "id": 999
                        }
                        self.logger.info(f"🚀 发送深度订阅请求: {sub_ob['channel']}")
                        await ws.send(json.dumps(sub_ob))
                        return 

            # === 常规数据处理 ===
            # 如果尚未锁定，不处理后续逻辑
            if not self.is_market_locked:
                return

            has_update = False

            # 1. 解析深度 (Order Book)
            if 'bids' in payload and payload['bids']:
                bid_entry = payload['bids'][0]
                price = bid_entry['price'] if isinstance(bid_entry, dict) else bid_entry[0]
                self.lighter_bid = Decimal(str(price))
                has_update = True
                
            if 'asks' in payload and payload['asks']:
                ask_entry = payload['asks'][0]
                price = ask_entry['price'] if isinstance(ask_entry, dict) else ask_entry[0]
                self.lighter_ask = Decimal(str(price))
                has_update = True

            # 2. 解析统计 (Market Stats) - 作为兜底
            # 兼容嵌套结构
            stats_data = None
            if 'mark_price' in payload: stats_data = payload
            if 'market_stats' in payload: stats_data = payload['market_stats']

            if stats_data and 'mark_price' in stats_data:
                # 确保是当前锁定 ID 的数据
                current_mid = stats_data.get('market_id')
                # 有些消息可能没有 market_id 字段，如果是 update 且我们已经订阅了，通常就是对的
                if current_mid is None or current_mid == self.lighter_market_id:
                    mp = stats_data['mark_price']
                    self.lighter_mark_price = Decimal(str(mp))
                    
                    # 如果深度数据还没来，用标记价格先顶着
                    if self.lighter_bid == 0: self.lighter_bid = self.lighter_mark_price - Decimal('0.5')
                    if self.lighter_ask == 0: self.lighter_ask = self.lighter_mark_price + Decimal('0.5')
                    has_update = True

            if has_update:
                self.last_update_time = time.time()

        except Exception as e:
            # print(f"解析错误: {e}")
            pass

    # ================= 交易逻辑 =================

    def handle_extended_order_update(self, order_data):
        status = order_data.get('status')
        side = order_data.get('side', '').lower()
        
        # 如果成交，或者取消，都要解锁
        if status in ['FILLED', 'CANCELED', 'EXPIRED']:
            if status == 'FILLED':
                filled_qty = Decimal(str(order_data.get('filled_size', self.order_quantity)))
                price = order_data.get('price')
                print("\n")
                self.logger.info(f"⚡ Extended FILLED! Side: {side}, Qty: {filled_qty} @ {price}")
                
                # 对冲
                hedge_side = 'sell' if side == 'buy' else 'buy'
                asyncio.create_task(self.place_lighter_hedge_order(hedge_side, filled_qty))
            
            # === 🔥 解锁，允许下新的单子 ===
            self.current_maker_order_id = None
            self.logger.info("🔓 订单结束，解除锁定，继续监控...")

    async def place_extended_maker_order(self, side: str, price: Decimal):
        print("\n")
        self.logger.info(f"Creating Extended Maker {side} @ {price}...")
        result = await self.extended_client.place_open_order(
            contract_id=f"{self.ticker}-USD", quantity=self.order_quantity, direction=side
        )
        if result.success:
            self.logger.info(f"✅ Extended Order Placed: {result.order_id}")
            return result.order_id
        return None

    async def place_lighter_hedge_order(self, side: str, quantity: Decimal):
        print("\n")
        self.logger.info(f"🛡️ Executing Lighter Hedge: {side} {quantity}...")
        try:
            if side == 'buy':
                price = float(self.lighter_ask) * 1.05 if self.lighter_ask > 0 else 200000
                is_ask = False
            else:
                price = float(self.lighter_bid) * 0.95 if self.lighter_bid > 0 else 1
                is_ask = True
            await self.lighter_client.create_order(
                market_id=self.lighter_market_id, price=price, size=float(quantity),
                is_ask=is_ask, order_type="Limit"
            )
            self.logger.info("✅ Lighter Hedge Order Sent")
        except Exception as e:
            self.logger.error(f"❌ Lighter Hedge Failed: {e}")

    # ================= 主循环 =================

    async def run(self):
        print(f"🚀 Initializing Extended <-> Lighter Arbitrage for {self.ticker}...")
        
        try:
            self.initialize_lighter_client()
            self.initialize_extended_client()
        except Exception as e:
            self.logger.error(f"❌ 初始化失败: {e}")
            return

        await self.setup_websockets()
        
        self.logger.info("Waiting 3s for market data...")
        await asyncio.sleep(3)
        self.logger.info("🔄 Loop started. Monitoring spreads...")
        print("-" * 80)
        
        last_print_time = 0
        last_debug_time = time.time()

        while not self.stop_flag:
            try:
                # 获取 Extended 价格
                ext_bid, ext_ask = await self.extended_client.fetch_bbo_prices()
                
                # 只有两边都有数据才显示
                if self.lighter_bid > 0 and ext_bid > 0:
                    spread_long = self.lighter_bid - ext_ask
                    spread_short = ext_bid - self.lighter_ask
                    
                    # === 🔥 关键修复：检查是否有正在进行的订单 ===
                    # 只有当 current_maker_order_id 为 None 时才下单
                    if self.current_maker_order_id is None:
                        
                        if spread_long > self.long_threshold:
                            print("\n")
                            self.logger.info(f"💎 LONG 机会! 价差: {spread_long:.2f} (买Ext:{ext_ask} -> 卖Light:{self.lighter_bid})")
                            # 下单并记录 Order ID
                            order_id = await self.place_extended_maker_order('buy', ext_bid)
                            if order_id:
                                self.current_maker_order_id = order_id
                                self.logger.info("🔒 锁定状态：等待 Extended 订单成交或取消...")
                            
                        elif spread_short > self.short_threshold:
                            print("\n")
                            self.logger.info(f"💎 SHORT 机会! 价差: {spread_short:.2f} (卖Ext:{ext_bid} -> 买Light:{self.lighter_ask})")
                            order_id = await self.place_extended_maker_order('sell', ext_ask)
                            if order_id:
                                self.current_maker_order_id = order_id
                                self.logger.info("🔒 锁定状态：等待 Extended 订单成交或取消...")

                    # 实时看板
                    current_time = time.time()
                    if current_time - last_print_time > 1.0:
                        # 状态指示器
                        lock_status = "🔓空闲" if self.current_maker_order_id is None else "🔒持单中"
                        
                        status = (
                            f"\r📡 [{lock_status}] "
                            f"Light: {self.lighter_bid:.1f}/{self.lighter_ask:.1f} | "
                            f"Ext: {ext_bid:.1f}/{ext_ask:.1f} | "
                            f"价差: {float(spread_long):.1f}/{float(spread_short):.1f}"
                        )
                        sys.stdout.write(status)
                        sys.stdout.flush()
                        last_print_time = current_time
                else:
                    if time.time() - last_debug_time > 5:
                        msg = []
                        if not self.is_market_locked:
                            msg.append("📡 雷达扫描中...")
                        elif self.lighter_bid == 0:
                            msg.append(f"等待 Lighter (ID {self.lighter_market_id}) 深度...")
                        if ext_bid == 0: msg.append("Waiting Extended")
                        print(f"\r⏳ {' | '.join(msg)}", end="")
                        last_debug_time = time.time()

                await asyncio.sleep(0.1)

            except KeyboardInterrupt:
                print("\n🛑 用户停止")
                self.stop_flag = True
            except Exception as e:
                self.logger.error(f"\nLoop Error: {e}")
                await asyncio.sleep(1)
