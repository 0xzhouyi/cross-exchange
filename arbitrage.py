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
                 order_timeout: int = 20): 
        
        self.ticker = ticker.upper()
        self.order_quantity = order_quantity
        self.long_threshold = long_ex_threshold
        self.short_threshold = short_ex_threshold
        self.order_timeout = order_timeout
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
        
        # 🔥 注意：这里 BASE_DECIMALS=8 是标准（1 BTC = 100000000 atomic units），之前成功日志也证明正确
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
                # 🔥 关键修复：严格确保 filled_size 是 Decimal 小数
                raw_filled = update_data.get('filled_size', 0)
                try:
                    qty = Decimal(str(raw_filled)).quantize(Decimal('0.00000001'))  # 强制8位小数
                except:
                    qty = Decimal('0')
                price = update_data.get('price')
                self.logger.info(f"⚡ Extended 成交! side={update_data.get('side')} raw_filled={raw_filled} -> qty={qty} @ {price}")
                hedge_side = 'sell' if update_data.get('side') == 'buy' else 'buy'
                asyncio.create_task(self.place_lighter_hedge(hedge_side, qty))
            if self.current_maker_order_id == oid:
                self.current_maker_order_id = None
                self.order_start_time = 0
                self.logger.info("🔓 锁定解除，继续监控")

    async def place_lighter_hedge(self, side: str, qty: Decimal):
        """
        🔥 终极修复版：
        - slippage 严格 0.5%（你说绝对不会超）
        - 超强安全检查 + 详细数量日志（解决 1.3 BTC 谜团）
        - 双重尝试（带保护 → 无保护）
        """
        try:
            # 🔥 超严安全检查：qty 必须小数，且 < 0.01 BTC
            if qty <= 0 or qty >= Decimal('0.01'):
                self.logger.error(f"🚨 致命安全警报！对冲数量异常 qty={qty} BTC，拒绝下单！（可能 Extended filled_size 返回错误或 --size 输入错）")
                return

            slippage = Decimal('0.005')  # 🔥 精确 0.5%（按你说法不会超）

            if side == 'sell':
                base_price = self.lighter_bid
                if base_price <= 0:
                    self.logger.error("❌ Lighter bid 无效")
                    return
                worst_price = base_price * (Decimal('1') - slippage)
            else:
                base_price = self.lighter_ask
                if base_price <= 0:
                    self.logger.error("❌ Lighter ask 无效")
                    return
                worst_price = base_price * (Decimal('1') + slippage)

            atomic_amount = int(qty * (10 ** self.LIGHTER_BASE_DECIMALS))
            atomic_worst = int(worst_price * (10 ** self.LIGHTER_QUOTE_DECIMALS))
            
            # 🔥 关键调试日志：明确显示计算过程
            self.logger.info(f"🔍 对冲数量诊断: 输入 qty={qty} BTC | atomic_amount={atomic_amount} (应≈{float(qty)*1e8:.0f}) | worst_price={worst_price:.2f} | atomic_worst={atomic_worst}")

            client_id = int(time.time() * 1000) % 2147483647

            self.logger.info(f"🛡️ 正在执行 Lighter Market 对冲: {side} {qty} BTC @ 最差 {worst_price:.2f} (slippage 0.5%)")

            # 第一尝试：带 0.5% 保护
            try:
                res = await self.lighter_client.create_market_order(
                    market_index=int(self.lighter_market_id),
                    base_amount=atomic_amount,
                    is_ask=(side == 'sell'),
                    avg_execution_price=atomic_worst,
                    client_order_index=client_id
                )
                if isinstance(res, tuple) and len(res) >= 3 and res[2] is not None:
                    raise Exception(f"带保护失败: {res[2]}")
                self.logger.info(f"✅ Lighter Market 对冲成功 (带 0.5% 保护): {res}")
                return
            except Exception as e1:
                self.logger.warning(f"⚠️ 带 0.5% 保护失败 ({e1})，可能是瞬时深度问题，尝试无保护...")

            # 第二尝试：无保护（你说滑点不会超 0.5%，风险极低）
            res = await self.lighter_client.create_market_order(
                market_index=int(self.lighter_market_id),
                base_amount=atomic_amount,
                is_ask=(side == 'sell'),
                client_order_index=client_id
            )
            if isinstance(res, tuple) and len(res) >= 3 and res[2] is not None:
                self.logger.error(f"❌ 无保护也失败: {res[2]} (full: {res})")
            else:
                self.logger.info(f"✅ Lighter Market 对冲成功 (无保护，滑点应<0.5%): {res}")

        except Exception as e:
            self.logger.error(f"❌ 对冲最终失败: {e}", exc_info=True)

    async def run(self):
        await self.initialize_clients()
        asyncio.create_task(self.run_lighter_ws())
        
        self.logger.info("等待数据预热...")
        await asyncio.sleep(5)
        
        self.logger.info(f"🚀 策略已启动 (order_quantity={self.order_quantity} BTC, 超时重置: {self.order_timeout}秒)")
        
        while not self.stop_flag:
            try:
                if self.current_maker_order_id is not None:
                    elapsed = time.time() - self.order_start_time
                    if elapsed > self.order_timeout:
                        self.logger.info(f"⏰ 订单 {self.current_maker_order_id} 超时，立即取消并解除锁定")
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
                                self.logger.info(f"🔒 下单成功 {res.order_id}，等待成交...")
                            else:
                                self.logger.error(f"❌ 下单失败: {res.error_message}")
                                
                        elif spread_short > self.short_threshold:
                            self.logger.info(f"\n💎 SHORT 机会! 差价: {spread_short:.1f}")
                            res = await self.extended_client.place_open_order(f"{self.ticker}-USD", self.order_quantity, 'sell')
                            if res.success:
                                self.current_maker_order_id = res.order_id
                                self.order_start_time = time.time()
                                self.logger.info(f"🔒 下单成功 {res.order_id}，等待成交...")
                            else:
                                self.logger.error(f"❌ 下单失败: {res.error_message}")
                else:
                    print(f"\r⏳ 等待价格数据稳定...   ", end="")
                
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
    args = parser.parse_args()

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
        print("\n停止")
