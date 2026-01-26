# === 🔥 第一步：最优先加载环境变量 (放在所有 import 之前) ===
from dotenv import load_dotenv
import os
# 强制从当前目录加载 .env，覆盖系统变量
load_dotenv(override=True)

import asyncio
import logging
import sys
import time
import json
import random
from decimal import Decimal
from typing import Optional

# === 检查配置是否加载成功 ===
if not os.getenv("API_KEY_PRIVATE_KEY"):
    print("❌ 严重错误: 代码已加载 .env 但仍未找到 API_KEY_PRIVATE_KEY")
    sys.exit(1)

# === 导入业务库 ===
try:
    from lighter.signer_client import SignerClient
    from exchanges.extended import ExtendedClient
except ImportError as e:
    print(f"❌ 导入库失败: {e}")
    sys.exit(1)

# 配置类
class BotConfig:
    def __init__(self, ticker, quantity):
        self.ticker = ticker
        self.contract_id = f"{ticker}-USD"
        self.quantity = quantity
        self.tick_size = Decimal("0.1") 
        self.take_profit = 0
        self.close_order_side = "sell"

class ExtendedArb:
    def __init__(self, ticker: str, order_quantity: Decimal,
                 long_ex_threshold: Decimal = Decimal('10'),
                 short_ex_threshold: Decimal = Decimal('10'),
                 order_timeout: int = 10): 
        
        self.ticker = ticker.upper()
        self.order_quantity = order_quantity
        self.long_threshold = long_ex_threshold
        self.short_threshold = short_ex_threshold
        self.order_timeout = order_timeout
        self.stop_flag = False
        self._setup_logger()

        self.extended_client: Optional[ExtendedClient] = None
        self.lighter_client: Optional[SignerClient] = None
        
        self.lighter_market_id = 1 
        self.current_maker_order_id = None
        self.order_start_time = 0
        
        self.ext_bid = Decimal('0')
        self.ext_ask = Decimal('0')
        self.lighter_bid = Decimal('0')
        self.lighter_ask = Decimal('0')
        
        # 精度配置 (BTC=8, USDC=6)
        self.LIGHTER_BASE_DECIMALS = 8  
        self.LIGHTER_QUOTE_DECIMALS = 6 
        
        self.lighter_ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        self.lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        
        try:
            raw_index = os.getenv('LIGHTER_ACCOUNT_INDEX', '0')
            self.account_index = int(raw_index)
            self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
        except ValueError:
            sys.exit(1)

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)
        self.logger = logging.getLogger(f"arb_{self.ticker}")
        self.logger.setLevel(logging.INFO)
        if self.logger.hasHandlers(): self.logger.handlers.clear()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)

    # === 异步初始化 ===
    async def initialize_clients(self):
        raw_key = os.getenv('API_KEY_PRIVATE_KEY')
        if raw_key.startswith("0x"): raw_key = raw_key[2:]
        try:
            self.lighter_client = SignerClient(
                url=self.lighter_base_url,
                account_index=self.account_index,
                api_private_keys={self.api_key_index: raw_key}
            )
            self.logger.info("✅ Lighter 客户端初始化成功")
        except Exception as e:
            self.logger.error(f"❌ Lighter 初始化失败: {e}")
            raise

        self.logger.info("正在初始化 Extended Client...")
        try:
            config = BotConfig(self.ticker, self.order_quantity)
            self.extended_client = ExtendedClient(config)
            await self.extended_client.get_contract_attributes()
            asyncio.create_task(self.extended_client.connect())
            self.extended_client.setup_order_update_handler(self.handle_extended_order_update)
            self.logger.info("✅ Extended 客户端连接成功")
        except Exception as e:
            self.logger.error(f"❌ Extended 初始化失败: {e}")
            raise

    # Lighter WS
    import websockets
    async def run_lighter_ws(self):
        while not self.stop_flag:
            try:
                self.logger.info(f"正在连接 Lighter WS: {self.lighter_ws_url}")
                async with self.websockets.connect(self.lighter_ws_url, ping_interval=20, ping_timeout=20) as ws:
                    self.logger.info("✅ Lighter WS 已连接")
                    sub_msg = json.dumps({"type": "subscribe", "channel": f"order_book/{self.lighter_market_id}"})
                    await ws.send(sub_msg)
                    async for raw_msg in ws:
                        if raw_msg == "ping": await ws.send("pong"); continue
                        try: msg = json.loads(raw_msg)
                        except: continue
                        if msg.get("type") == "ping": await ws.send(json.dumps({"type": "pong"})); continue
                        if msg.get("type") in ["subscribed/order_book", "update/order_book"]: await self._process_lighter_msg(msg)
            except Exception as e:
                self.logger.error(f"Lighter WS 错误: {e}")
                await asyncio.sleep(5)
    
    async def _process_lighter_msg(self, data):
        try:
            payload = data.get('order_book', {})
            if not payload: payload = data.get('data', data.get('payload', {}))
            if 'bids' in payload and payload['bids']:
                self.lighter_bid = Decimal(str(payload['bids'][0]['price']))
            if 'asks' in payload and payload['asks']:
                self.lighter_ask = Decimal(str(payload['asks'][0]['price']))
        except Exception: pass

    def handle_extended_order_update(self, update_data):
        status = update_data.get('status')
        oid = update_data.get('order_id')
        if status in ['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED']:
            self.logger.info(f"📝 订单 {oid} 状态更新: {status}")
            if status == 'FILLED':
                side = update_data.get('side')
                qty = Decimal(str(update_data.get('filled_size', 0)))
                price = update_data.get('price')
                self.logger.info(f"⚡ Extended 成交! {side} {qty} @ {price}")
                hedge_side = 'sell' if side == 'buy' else 'buy'
                asyncio.create_task(self.place_lighter_hedge(hedge_side, qty))
            if self.current_maker_order_id == oid:
                self.current_maker_order_id = None
                self.order_start_time = 0
                self.logger.info("🔓 锁定解除，继续监控")

    async def place_lighter_hedge(self, side, qty):
        try:
            # 动态获取常量
            TYPE_LIMIT = getattr(self.lighter_client, 'ORDER_TYPE_LIMIT', 1) 
            TIF_GTC = getattr(self.lighter_client, 'ORDER_TIME_IN_FORCE_GOOD_TILL_TIME', 0)

            price_multiplier = Decimal("1.01") if side == 'buy' else Decimal("0.99")
            base_price = self.lighter_ask if side == 'buy' else self.lighter_bid
            hedge_price = base_price * price_multiplier
            is_ask = (side == 'sell')
            
            atomic_amount = int(qty * (10 ** self.LIGHTER_BASE_DECIMALS))
            atomic_price = int(hedge_price * (10 ** self.LIGHTER_QUOTE_DECIMALS))
            
            # 使用安全的 31位 nonce
            client_id = int(time.time() * 1000) % 2147483647
            
            # === 🔥 关键修正：使用 Seconds + 1小时短时效 ===
            # 1. 之前Seconds能过签名，说明SDK需要Seconds
            # 2. 之前长时效被拒，这次缩短到 1 小时
            expiry_timestamp = int(time.time()) + 3600

            self.logger.info(f"🛡️ 正在执行 Lighter 对冲: {side} (Price: {atomic_price}, Expiry: {expiry_timestamp})")

            res = await self.lighter_client.create_order(
                market_index=int(self.lighter_market_id),
                price=atomic_price,
                base_amount=atomic_amount,
                is_ask=is_ask,
                order_type=TYPE_LIMIT, 
                client_order_index=client_id,
                time_in_force=TIF_GTC, 
                order_expiry=expiry_timestamp # 传入秒级
            )
            
            if res and isinstance(res, tuple) and res[2] is not None:
                self.logger.error(f"❌ Lighter 拒绝: {res[2]}")
            else:
                self.logger.info(f"✅ Lighter 对冲订单已发送 (Result: {res})")
                
        except Exception as e:
            self.logger.error(f"❌ 对冲失败: {e}")

    async def run(self):
        await self.initialize_clients()
        import websockets
        self.websockets = websockets
        asyncio.create_task(self.run_lighter_ws())
        self.logger.info("等待数据预热...")
        await asyncio.sleep(3)
        self.logger.info(f"🚀 策略已启动 (超时重置: {self.order_timeout}秒)")
        
        while not self.stop_flag:
            try:
                if self.current_maker_order_id is not None:
                    elapsed = time.time() - self.order_start_time
                    if elapsed > self.order_timeout:
                        self.logger.info(f"⏰ 订单 {self.current_maker_order_id} 超时 ({elapsed:.1f}s > {self.order_timeout}s)，正在取消...")
                        await self.extended_client.cancel_order(self.current_maker_order_id)
                
                ext_bid, ext_ask = await self.extended_client.fetch_bbo_prices()
                
                if self.lighter_bid > 0 and ext_bid > 0:
                    spread_long = self.lighter_bid - ext_ask
                    spread_short = ext_bid - self.lighter_ask
                    
                    print(f"\rExt: {ext_bid:.1f}/{ext_ask:.1f} | Lighter: {self.lighter_bid:.1f}/{self.lighter_ask:.1f} | Diff: {spread_long:.1f}/{spread_short:.1f}   ", end="")

                    if self.current_maker_order_id is None:
                        if spread_long > self.long_threshold:
                            self.logger.info(f"\n💎 LONG 机会! 差价: {spread_long}")
                            res = await self.extended_client.place_open_order(f"{self.ticker}-USD", self.order_quantity, 'buy')
                            if res.success:
                                self.current_maker_order_id = res.order_id
                                self.order_start_time = time.time()
                                self.logger.info(f"🔒 下单成功 {res.order_id}，等待成交...")
                            else:
                                self.logger.error(f"❌ 下单失败: {res.error_message}")
                                
                        elif spread_short > self.short_threshold:
                            self.logger.info(f"\n💎 SHORT 机会! 差价: {spread_short}")
                            res = await self.extended_client.place_open_order(f"{self.ticker}-USD", self.order_quantity, 'sell')
                            if res.success:
                                self.current_maker_order_id = res.order_id
                                self.order_start_time = time.time()
                                self.logger.info(f"🔒 下单成功 {res.order_id}，等待成交...")
                            else:
                                self.logger.error(f"❌ 下单失败: {res.error_message}")
                else:
                    if time.time() % 5 == 0: print(f"\r⏳ 状态监控...", end="")
                await asyncio.sleep(0.1)
            except KeyboardInterrupt:
                self.stop_flag = True
            except Exception as e:
                self.logger.error(f"Loop error: {e}")
                await asyncio.sleep(1)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default="BTC")
    parser.add_argument("--size", type=float, default=0.003)
    parser.add_argument("--long-threshold", type=float, default=10)
    parser.add_argument("--short-threshold", type=float, default=10)
    parser.add_argument("--timeout", type=int, default=10, help="挂单超时时间(秒)")
    args, unknown = parser.parse_known_args()

    arb = ExtendedArb(
        ticker=args.ticker, 
        order_quantity=Decimal(str(args.size)), 
        long_ex_threshold=Decimal(str(args.long_threshold)), 
        short_ex_threshold=Decimal(str(args.short_threshold)),
        order_timeout=args.timeout 
    )
    try:
        asyncio.run(arb.run())
    except KeyboardInterrupt:
        print("停止")
