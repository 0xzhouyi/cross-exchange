# === 🔥 第一步：最优先加载环境变量 (放在所有 import 之前) ===
from dotenv import load_dotenv
import os
load_dotenv(override=True)

import asyncio
import logging
import sys
import time
import json
from decimal import Decimal
from typing import Optional

import websockets

if not os.getenv("API_KEY_PRIVATE_KEY"):
    print("❌ 严重错误: 未找到 API_KEY_PRIVATE_KEY")
    sys.exit(1)

try:
    from lighter.signer_client import SignerClient
    from exchanges.extended import ExtendedClient
except ImportError as e:
    print(f"❌ 导入库失败: {e}")
    sys.exit(1)

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
                 order_timeout: int = 20,
                 hedge_slippage: float = 0.5):  # 🔥 默认 0.5%，安全且足够保护
        
        self.ticker = ticker.upper()
        self.order_quantity = order_quantity
        self.long_threshold = long_ex_threshold
        self.short_threshold = short_ex_threshold
        self.order_timeout = order_timeout
        self.hedge_slippage = Decimal(str(hedge_slippage)) / 100
        self.stop_flag = False
        self._setup_logger()

        self.extended_client: Optional[ExtendedClient] = None
        self.lighter_client: Optional[SignerClient] = None
        
        self.lighter_market_id = 1  # BTC 市场 ID
        self.current_maker_order_id = None
        self.order_start_time = 0
        
        self.ext_bid = Decimal('0')
        self.ext_ask = Decimal('0')
        self.lighter_bid = Decimal('0')
        self.lighter_ask = Decimal('0')
        
        # 精度配置: BTC base=5 decimals, quote=6
        self.LIGHTER_BASE_DECIMALS = 5   
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
        fh = logging.FileHandler(f"logs/{self.ticker}_arb.log")
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

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

    async def run_lighter_ws(self):
        while not self.stop_flag:
            try:
                async with websockets.connect(self.lighter_ws_url, ping_interval=20, ping_timeout=20, max_size=None) as ws:
                    self.logger.info("✅ Lighter WS 已连接")
                    sub_msg = json.dumps({"type": "subscribe", "channel": f"order_book/{self.lighter_market_id}"})
                    await ws.send(sub_msg)
                    async for raw_msg in ws:
                        if self.stop_flag: break
                        if raw_msg == "ping":
                            await ws.send("pong")
                            continue
                        try:
                            msg = json.loads(raw_msg)
                        except:
                            continue
                        if msg.get("type") == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
                            continue
                        if msg.get("type") in ["subscribed/order_book", "update/order_book"]:
                            await self._process_lighter_msg(msg)
            except Exception as e:
                self.logger.error(f"❌ Lighter WS 错误: {e} (5秒后重连)")
                await asyncio.sleep(5)
    
    async def _process_lighter_msg(self, data):
        try:
            payload = data.get('order_book') or data.get('data') or data.get('payload', {})
            bids = payload.get('bids', [])
            asks = payload.get('asks', [])
            if bids:
                self.lighter_bid = Decimal(str(bids[0][0] if isinstance(bids[0], list) else bids[0].get('price', 0)))
            if asks:
                self.lighter_ask = Decimal(str(asks[0][0] if isinstance(asks[0], list) else asks[0].get('price', 0)))
        except Exception as e:
            self.logger.error(f"处理 Lighter 消息异常: {e}")

    def handle_extended_order_update(self, update_data):
        status = update_data.get('status')
        oid = update_data.get('order_id')
        if status in ['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED']:
            self.logger.info(f"📝 订单 {oid} 状态更新: {status}")
            if status == 'FILLED':
                raw_filled = update_data.get('filled_size', 0)
                try:
                    qty = Decimal(str(raw_filled)).quantize(Decimal('0.00001')) 
                except:
                    qty = Decimal('0')
                price = update_data.get('price')
                self.logger.info(f"⚡ Extended 成交! side={update_data.get('side')} qty={qty} @ {price}")
                hedge_side = 'sell' if update_data.get('side') == 'buy' else 'buy'
                asyncio.create_task(self.place_lighter_hedge(hedge_side, qty))
            if self.current_maker_order_id == oid:
                self.current_maker_order_id = None
                self.order_start_time = 0
                self.logger.info("🔓 锁定解除，继续监控")

    async def place_lighter_hedge(self, side: str, qty: Decimal):
        try:
            if qty <= 0:
                return
            
            slippage = self.hedge_slippage

            if side == 'sell':
                base_price = self.lighter_bid
                worst_price = base_price * (Decimal('1') - slippage)  # 卖单最低接受平均价
                is_ask = True
            else:  # buy
                base_price = self.lighter_ask
                worst_price = base_price * (Decimal('1') + slippage)  # 买单最高接受平均价
                is_ask = False

            if base_price <= 0:
                self.logger.warning("Lighter 价格无效，跳过对冲")
                return

            atomic_quantity = int(qty * (10 ** self.LIGHTER_BASE_DECIMALS))
            atomic_worst_price = int(worst_price * (10 ** self.LIGHTER_QUOTE_DECIMALS))
            client_order_index = int(time.time() * 1000) % 2147483647

            self.logger.info(f"🛡️ 正在执行 Lighter Market 对冲（带价格保护）: {side.upper()} {qty} 保护价 {'≤' if side=='sell' else '≥'}{worst_price:.2f} (滑点 {slippage*100:.2f}%) | client_order_index={client_order_index}")

            # 🔥 切换到 Market Order + avg_execution_price 保护（官方推荐方式，避免 limit 的各种风控检查）
            res = await self.lighter_client.create_market_order(
                market_index=self.lighter_market_id,
                client_order_index=client_order_index,
                base_amount=atomic_quantity,
                is_ask=is_ask,
                avg_execution_price=atomic_worst_price,  # 价格保护：超价直接拒绝
                reduce_only=True
            )

            if isinstance(res, tuple) and len(res) >= 3 and res[2] is not None:
                err_msg = str(res[2])
                self.logger.error(f"❌ Lighter 对冲下单被拒: {err_msg}")
                if "price" in err_msg.lower():
                    self.logger.critical("🚨 拒绝原因：成交价预计超出保护价（坏价保护触发）")
                self.logger.critical("🚨 对冲失败，请立即手动检查/平仓！")
            else:
                self.logger.info(f"✅ Lighter Market 对冲已提交")
                asyncio.create_task(self.monitor_hedge_order(client_order_index, qty, side))

        except Exception as e:
            if "no attribute 'create_market_order'" in str(e):
                self.logger.critical("❌ SDK 无 create_market_order 方法，请检查 lighter-python 版本或使用 create_order + market 参数")
            self.logger.error(f"❌ 对冲执行异常: {e}", exc_info=True)
            self.logger.critical("🚨 对冲失败，请立即手动干预！")

    async def monitor_hedge_order(self, client_order_index: int, qty: Decimal, side: str):
        await asyncio.sleep(15)  # market 稍长一点时间

        try:
            status_res = await self.lighter_client.get_order_status(client_order_index)
            status = status_res.get("status", "UNKNOWN") if isinstance(status_res, dict) else "UNKNOWN"
            filled = status_res.get("filled_amount", 0) if isinstance(status_res, dict) else 0

            expected = int(qty * (10 ** self.LIGHTER_BASE_DECIMALS) * 0.99)
            if filled >= expected:
                self.logger.info(f"✅ 对冲订单确认成交: client_order_index={client_order_index} filled={filled}")
            else:
                self.logger.critical(f"🚨 对冲订单未完全成交/取消！client_order_index={client_order_index} 状态={status} 已成交={filled}")
                self.logger.critical("请立即手动平仓！")
        except Exception as e:
            self.logger.error(f"查询对冲订单状态失败: {e}")
            self.logger.critical("🚨 查询失败，请手动去 Lighter 网页检查订单！")

    async def run(self):
        await self.initialize_clients()
        asyncio.create_task(self.run_lighter_ws())
        
        self.logger.info("等待数据预热...")
        await asyncio.sleep(5)
        
        self.logger.info(f"🚀 策略已启动 (数量={self.order_quantity} BTC, 超时={self.order_timeout}s, 对冲滑点={self.hedge_slippage*100:.2f}%)")
        
        while not self.stop_flag:
            try:
                if self.current_maker_order_id is not None:
                    elapsed = time.time() - self.order_start_time
                    if elapsed > self.order_timeout:
                        self.logger.info(f"⏰ 订单 {self.current_maker_order_id} 超时，取消")
                        await self.extended_client.cancel_order(self.current_maker_order_id)
                        self.current_maker_order_id = None
                        self.order_start_time = 0
                
                ext_bid, ext_ask = await self.extended_client.fetch_bbo_prices()
                
                if self.lighter_bid > 0 and self.lighter_ask > 0 and ext_bid > 0 and ext_ask > 0:
                    spread_long = self.lighter_bid - ext_ask
                    spread_short = ext_bid - self.lighter_ask
                    
                    print(f"\rExt: {ext_bid:.1f}/{ext_ask:.1f} | Lighter: {self.lighter_bid:.1f}/{self.lighter_ask:.1f} | Diff: {spread_long:+.1f}/{spread_short:+.1f}   ", end="")

                    if self.current_maker_order_id is None:
                        if spread_long > self.long_threshold:
                            self.logger.info(f"\n💎 LONG 机会! 差价: {spread_long:.1f}")
                            res = await self.extended_client.place_open_order(f"{self.ticker}-USD", self.order_quantity, 'buy')
                            if res.success:
                                self.current_maker_order_id = res.order_id
                                self.order_start_time = time.time()
                                self.logger.info(f"🔒 下单成功 {res.order_id}")
                            else:
                                self.logger.error(f"❌ 下单失败: {res.error_message}")
                                
                        elif spread_short > self.short_threshold:
                            self.logger.info(f"\n💎 SHORT 机会! 差价: {spread_short:.1f}")
                            res = await self.extended_client.place_open_order(f"{self.ticker}-USD", self.order_quantity, 'sell')
                            if res.success:
                                self.current_maker_order_id = res.order_id
                                self.order_start_time = time.time()
                                self.logger.info(f"🔒 下单成功 {res.order_id}")
                            else:
                                self.logger.error(f"❌ 下单失败: {res.error_message}")
                else:
                    print(f"\r⏳ 等待价格数据...   ", end="")
                
                await asyncio.sleep(0.2)
            except KeyboardInterrupt:
                self.stop_flag = True
                self.logger.info("正在停止...")
                break
            except Exception as e:
                self.logger.error(f"主循环异常: {e}")
                await asyncio.sleep(1)

        self.logger.info("🤖 机器人已停止")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default="BTC")
    parser.add_argument("--size", type=float, default=0.0013)
    parser.add_argument("--long-threshold", type=float, default=80)
    parser.add_argument("--short-threshold", type=float, default=80)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--hedge-slippage", type=float, default=0.5, help="对冲最大滑点百分比 (默认 0.5%，推荐 0.3~1.0)")
    args = parser.parse_args()

    arb = ExtendedArb(
        ticker=args.ticker, 
        order_quantity=Decimal(str(args.size)), 
        long_ex_threshold=Decimal(str(args.long_threshold)), 
        short_ex_threshold=Decimal(str(args.short_threshold)),
        order_timeout=args.timeout,
        hedge_slippage=args.hedge_slippage
    )
    try:
        asyncio.run(arb.run())
    except KeyboardInterrupt:
        print("\n停止")
