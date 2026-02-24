from dotenv import load_dotenv
import os
load_dotenv(override=True)

import asyncio
import logging
import sys
import time
import json
import aiohttp
import websockets
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
from collections import deque

# ==========================================
# ⚙️ USER CONFIG (V7.0 激进版)
# ==========================================
EXTENDED_MAKER_FEE = Decimal('0.0')     # 0 手续费
LIGHTER_TAKER_FEE  = Decimal('0.0')     # 0 手续费
GAS_COST_PER_TRADE = Decimal('0.5')     # Gas
MIN_PROFIT_MARGIN  = Decimal('20.0')    # 🔥 提高门槛：每单至少赚20U才肯动，防止无效磨损

STRICT_MODE = True  
# ==========================================

logging.getLogger("asyncio").setLevel(logging.WARNING)

if not os.getenv("API_KEY_PRIVATE_KEY"):
    print("❌ 严重错误: 未找到 API_KEY_PRIVATE_KEY")
    sys.exit(1)

try:
    import lighter
    from lighter.signer_client import SignerClient
    from lighter import ApiClient, Configuration, AccountApi, OrderApi
    from exchanges.extended import ExtendedClient
    from x10.perpetual.orders import OrderSide, TimeInForce
except ImportError as e:
    print(f"❌ 导入库失败: {e}")
    sys.exit(1)

try:
    from strategy.risk_engine import RiskEngine
except ImportError:
    RiskEngine = None

class BotConfig:
    def __init__(self, ticker, quantity):
        self.ticker = ticker
        self.contract_id = f"{ticker}-USD"
        self.quantity = quantity
        self.tick_size = Decimal("0.1") 
        self.take_profit = 0
        self.close_order_side = "sell"

class LocalOrderBook:
    def __init__(self):
        self.bids: Dict[Decimal, Decimal] = {}
        self.asks: Dict[Decimal, Decimal] = {}

    def update(self, side: str, updates: list, is_snapshot: bool = False):
        target = self.bids if side == 'bid' else self.asks
        if is_snapshot: target.clear()
        for item in updates:
            try:
                if isinstance(item, list): price, size = item[0], item[1]
                else: price, size = item.get('price'), (item.get('size') or item.get('amount'))
                p_dec, s_dec = Decimal(str(price)), Decimal(str(size))
                if s_dec == 0: 
                    if p_dec in target: del target[p_dec]
                else: target[p_dec] = s_dec
            except: continue

    def get_snapshot(self, side: str, limit: int = 20) -> List[List[Decimal]]:
        target = self.bids if side == 'bid' else self.asks
        reverse = (side == 'bid')
        return sorted(target.items(), key=lambda x: x[0], reverse=reverse)[:limit]
    
    # 🔥 新增：深度容量检查
    def get_depth_volume(self, side: str, price_limit: Decimal = None) -> Decimal:
        snapshot = self.get_snapshot(side, limit=5)
        total_vol = Decimal('0')
        for p, s in snapshot:
            # 如果指定了价格限制（例如不能吃太深），则只统计范围内的量
            if price_limit:
                if side == 'bid' and p < price_limit: break
                if side == 'ask' and p > price_limit: break
            total_vol += s
        return total_vol

class ExtendedArb:
    def __init__(self, ticker: str, 
                 order_quantity: Decimal,          
                 add_on_quantity: Decimal,         
                 open_threshold: Decimal = Decimal('105'),
                 close_threshold: Decimal = Decimal('-90'),
                 add_on_step: Decimal = Decimal('10'),
                 max_layers: int = 5,
                 order_timeout: int = 20,
                 hedge_slippage: float = 0.5):
        
        self.ticker = ticker.upper()
        self.order_quantity = order_quantity
        self.add_on_quantity = add_on_quantity
        self.open_threshold = open_threshold
        self.close_threshold = close_threshold
        self.add_on_step = add_on_step
        self.max_layers = max_layers
        self.order_timeout = order_timeout # 这里虽然传入了，但我们会用更短的内部周期
        self.hedge_slippage = Decimal(str(hedge_slippage)) / 100
        
        self.stop_flag = False
        self._setup_logger()

        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.tg_enabled = bool(self.tg_token and self.tg_chat_id)
        
        self.positions: List[dict] = [] 
        self.pending_hedges = 0  
        self.extended_client = None
        self.lighter_client = None
        self.api_client = None
        self.lighter_market_id = None
        self.risk_engine = None
        
        self.current_maker_order_id = None
        self.current_maker_price = Decimal('0')
        self.current_order_side = None
        self.order_start_time = 0
        
        self.orderbook = LocalOrderBook()
        self.trade_results = deque(maxlen=10)
        
        self.LIGHTER_BASE_DECIMALS = 5   
        self.LIGHTER_PRICE_DECIMALS = 1  
        self.lighter_ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        self.lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        self.lighter_api_url = "https://mainnet.zklighter.elliot.ai"
        
        try:
            self.account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
            self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
        except ValueError: sys.exit(1)

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

    async def send_tg_alert(self, message: str):
        if not self.tg_enabled: return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": message, "parse_mode": "HTML"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    pass
        except: pass

    def calculate_vwap(self, quantity: Decimal, is_buy: bool) -> Decimal:
        side = 'ask' if is_buy else 'bid'
        snapshot = self.orderbook.get_snapshot(side)
        if not snapshot: return Decimal('0')
        remaining, total_val = quantity, Decimal('0')
        for price, size in snapshot:
            fill = min(remaining, size)
            total_val += fill * price
            remaining -= fill
            if remaining <= 0: break
        if remaining > 0: return Decimal('0') 
        return total_val / quantity
    
    def calculate_break_even_spread(self, price: Decimal) -> Decimal:
        if price == 0: return self.open_threshold
        fees = price * (EXTENDED_MAKER_FEE + LIGHTER_TAKER_FEE)
        slippage_buffer = price * Decimal('0.0005')
        min_spread = fees + slippage_buffer + GAS_COST_PER_TRADE + MIN_PROFIT_MARGIN
        return min_spread

    async def check_circuit_breaker(self):
        if len(self.trade_results) >= 5:
            failures = list(self.trade_results).count(False)
            fail_rate = failures / len(self.trade_results)
            if fail_rate > 0.3:
                msg = f"🛑 <b>系统熔断触发</b>\n最近 {len(self.trade_results)} 次交易中失败 {failures} 次。\n机器人已停止以保护资金。"
                self.logger.critical(msg)
                await self.send_tg_alert(msg)
                self.stop_flag = True
                sys.exit(1)

    async def fetch_lighter_market_id(self):
        self.logger.info(f"🔍 正在查询 Lighter [{self.ticker}] 的 Market ID...")
        try:
            conf = Configuration(host=self.lighter_api_url)
            api_client = ApiClient(configuration=conf)
            order_api = OrderApi(api_client)
            order_books = await order_api.order_books()
            found_market = None
            for market in order_books.order_books:
                if market.symbol == self.ticker: found_market = market; break
                if market.symbol == f"{self.ticker}-USD": found_market = market; break
                if self.ticker in market.symbol.split('/')[0]: found_market = market
            if found_market:
                self.lighter_market_id = found_market.market_id
                self.logger.info(f"✅ 成功找到 Market ID: {self.lighter_market_id}")
            else:
                self.logger.error(f"❌ 未找到 {self.ticker} 对应的市场！")
                sys.exit(1)
            await api_client.close()
        except Exception as e:
            self.logger.error(f"❌ 查询 Market ID 失败: {e}")
            sys.exit(1)

    async def initialize_clients(self):
        await self.fetch_lighter_market_id()
        raw_key = os.getenv('API_KEY_PRIVATE_KEY')
        if raw_key.startswith("0x"): raw_key = raw_key[2:]
        try:
            self.lighter_client = SignerClient(url=self.lighter_base_url, account_index=self.account_index, api_private_keys={self.api_key_index: raw_key})
            conf = Configuration(host=self.lighter_base_url)
            self.api_client = ApiClient(configuration=conf)
            self.logger.info("✅ Lighter 客户端初始化成功")
        except Exception as e: raise

        try:
            config = BotConfig(self.ticker, self.order_quantity) 
            self.extended_client = ExtendedClient(config)
            await self.extended_client.get_contract_attributes()
            asyncio.create_task(self.extended_client.connect())
            self.extended_client.setup_order_update_handler(self.handle_extended_order_update)
            self.logger.info("✅ Extended 客户端连接成功")
        except Exception as e: raise
        
        if RiskEngine:
            self.risk_engine = RiskEngine(self.extended_client, self.lighter_client, self.logger)
            self.logger.info("✅ RiskEngine 风控模块已挂载")

    async def _get_extended_position_safe(self) -> Decimal:
        try:
            if hasattr(self.extended_client, 'perpetual_trading_client'):
                client = self.extended_client.perpetual_trading_client
                positions = await client.account.get_positions()
                target_market = f"{self.ticker}-USD"
                for pos in positions:
                    p_market = getattr(pos, 'market', '') or getattr(pos, 'symbol', '')
                    if p_market == target_market:
                        size = Decimal(str(getattr(pos, 'size', 0)))
                        side = getattr(pos, 'side', None)
                        is_short = False
                        if str(side).upper() == 'SELL' or str(side) == 'PositionSide.SHORT':
                            is_short = True
                        if is_short: return -size
                        return size
                return Decimal('0')
            else: return Decimal('0')
        except Exception as e:
            self.logger.error(f"❌ 获取 Extended 持仓失败: {e}")
            return None

    async def sync_initial_positions(self):
        self.logger.info("🔄 正在执行双边持仓审计 (Strict Sync)...")
        try:
            lighter_pos = Decimal('0')
            account_api = AccountApi(self.api_client)
            account_data = await account_api.account(by="index", value=str(self.account_index))
            if hasattr(account_data, 'accounts') and len(account_data.accounts) > 0:
                for pos in account_data.accounts[0].positions:
                    if str(getattr(pos, 'market_id', None)) == str(self.lighter_market_id):
                        lighter_pos = Decimal(str(getattr(pos, 'position', 0)))
                        break
            
            extended_pos = await self._get_extended_position_safe()
            if extended_pos is None:
                msg = "❌ 无法获取 Extended 真实持仓！拒绝启动！"
                self.logger.critical(msg)
                await self.send_tg_alert(msg)
                sys.exit(1)

            self.logger.info(f"📊 审计结果: Lighter={lighter_pos}, Extended={extended_pos}")
            net_exposure = lighter_pos + extended_pos
            
            if abs(net_exposure) > Decimal('0.0001'):
                msg = (f"🚨 <b>持仓严重不匹配！</b>\nL: {lighter_pos}\nE: {extended_pos}\nNet: {net_exposure}")
                self.logger.critical(msg)
                await self.send_tg_alert(msg)
                if STRICT_MODE: sys.exit(1)
            
            if abs(lighter_pos) > 0:
                qty = abs(lighter_pos)
                chunks = int(qty / self.add_on_quantity)
                remainder = qty % self.add_on_quantity
                for _ in range(chunks):
                    self.positions.append({'qty': self.add_on_quantity, 'ext_price': Decimal('0'), 'lighter_price': Decimal('0'), 'hedged': True, 'status': 'RESTORED'})
                if remainder > Decimal('0.00001'):
                     self.positions.append({'qty': remainder, 'ext_price': Decimal('0'), 'lighter_price': Decimal('0'), 'hedged': True, 'status': 'RESTORED'})
                self.logger.info(f"✅ 持仓校验通过。已恢复 {len(self.positions)} 层持仓。")
            else:
                self.logger.info("✅ 双边空仓，状态完美。")

        except Exception as e:
            self.logger.error(f"❌ 审计失败: {e}")
            sys.exit(1)

    async def run_lighter_ws(self):
        while not self.stop_flag:
            try:
                async with websockets.connect(self.lighter_ws_url, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "channel": f"order_book/{self.lighter_market_id}"}))
                    async for raw_msg in ws:
                        if self.stop_flag: break
                        if raw_msg == "ping": await ws.send("pong"); continue
                        try: msg = json.loads(raw_msg)
                        except: continue
                        if isinstance(msg, dict) and msg.get("type") == "ping": await ws.send(json.dumps({"type": "pong"})); continue
                        msg_type = msg.get("type")
                        if msg_type in ["subscribed/order_book", "update/order_book"]:
                            payload = msg.get('order_book') or msg.get('data') or msg.get('payload', {})
                            bids, asks = payload.get('bids', []), payload.get('asks', [])
                            is_snapshot = (msg_type == "subscribed/order_book")
                            if bids: self.orderbook.update('bid', bids, is_snapshot)
                            if asks: self.orderbook.update('ask', asks, is_snapshot)
            except asyncio.CancelledError: break
            except: 
                if not self.stop_flag: await asyncio.sleep(5)

    async def get_lighter_fill_price(self, client_order_index: int, fallback_price: Decimal) -> Decimal:
        try:
            await asyncio.sleep(1.5)
            order_api = OrderApi(self.api_client)
            target_order = None
            if hasattr(order_api, 'account_inactive_orders'):
                orders = await order_api.account_inactive_orders(account_index=self.account_index, market_id=self.lighter_market_id, limit=10)
                target_order = self._find_order(orders, client_order_index)
            if not target_order and hasattr(order_api, 'account_active_orders'):
                orders = await order_api.account_active_orders(account_index=self.account_index, market_id=self.lighter_market_id)
                target_order = self._find_order(orders, client_order_index)
            if target_order:
                raw = target_order.avg_execution_price
                if raw is None: return fallback_price
                raw_dec = Decimal(str(raw))
                if raw_dec > 1000000: return raw_dec / (10 ** self.LIGHTER_PRICE_DECIMALS)
                return raw_dec
            return fallback_price
        except: return fallback_price

    def _find_order(self, orders_obj, client_oid):
        orders_list = orders_obj if isinstance(orders_obj, list) else (getattr(orders_obj, 'orders', []) if hasattr(orders_obj, 'orders') else [])
        for o in orders_list:
            if getattr(o, 'client_order_id', -1) == client_oid: return o
        return None

    def handle_extended_order_update(self, update_data):
        if update_data.get('status') == 'FILLED':
            side = update_data.get('side')
            try: qty = Decimal(str(update_data.get('filled_size', 0))).quantize(Decimal('0.00001')) 
            except: qty = Decimal('0')
            price = Decimal(str(update_data.get('price')))
            
            print() 
            self.logger.info(f"⚡ Ext成交: {side} {qty} @ {price}")
            
            if self.current_maker_order_id == update_data.get('order_id'):
                self.current_maker_order_id = None
                self.current_maker_price = Decimal('0')
                self.current_order_side = None

            is_close_action = (side == 'sell')
            target_side = 'buy' if is_close_action else 'sell'
            
            if not is_close_action: # 开仓
                entry = {'qty': qty, 'ext_price': price, 'lighter_price': Decimal('0'), 'spread': Decimal('0'), 'hedged': False, 'status': 'HEDGING'}
                self.positions.append(entry)
                self.logger.info(f"✅ 账本更新: 已记录 Ext 持仓 {qty} (等待对冲...)")
                self.pending_hedges += 1
                asyncio.create_task(self.place_lighter_order(target_side, qty, is_close_action, price, entry))
            else: # 平仓
                self.pending_hedges += 1
                asyncio.create_task(self.place_lighter_order(target_side, qty, is_close_action, price, None))

    async def place_lighter_order(self, side: str, qty: Decimal, is_closing: bool, ext_price: Decimal, position_record: Optional[dict]):
        try:
            if not is_closing and side != 'sell':
                self.logger.critical(f"❌ 逻辑错误: Long Only 模式下开仓对冲必须是 SELL")
                return

            qty_to_hedge = qty
            if is_closing:
                qty_to_hedge = Decimal('0')
                temp = qty
                for p in self.positions:
                    if temp <= 0: break
                    consumable = min(p['qty'], temp)
                    if p.get('hedged', True): qty_to_hedge += consumable
                    temp -= consumable
                if qty_to_hedge < qty: self.logger.info(f"💡 智能平仓: 仅需对冲 {qty_to_hedge}")

            if qty_to_hedge <= 0:
                self.pending_hedges -= 1
                return

            hedge_success = False
            real_lighter_price = Decimal('0')
            est_fill_price = self.calculate_vwap(qty_to_hedge, is_buy=(side=='buy'))
            if est_fill_price == 0: est_fill_price = Decimal('0')

            slippage = self.hedge_slippage
            if side == 'sell': 
                if not is_closing: slippage = slippage * Decimal('2.0')
                base = est_fill_price if est_fill_price > 0 else Decimal('999999')
                worst = base * (Decimal('1') - slippage)
                is_ask = True
            else:  
                base = est_fill_price if est_fill_price > 0 else Decimal('1')
                worst = base * (Decimal('1') + slippage)
                is_ask = False

            atomic_qty = int(qty_to_hedge * (10 ** self.LIGHTER_BASE_DECIMALS)) 
            atomic_price = int(worst * (10 ** self.LIGHTER_PRICE_DECIMALS))
            client_oid = int(time.time() * 1000) % 2147483647
            
            res = await self.lighter_client.create_market_order(
                market_index=self.lighter_market_id, client_order_index=client_oid,
                base_amount=atomic_qty, is_ask=is_ask, avg_execution_price=atomic_price, reduce_only=False 
            )
            
            if isinstance(res, tuple) and len(res) >= 3 and res[2] is not None:
                err_msg = str(res[2])
                self.logger.error(f"❌ Lighter 对冲失败: {err_msg}")
                await self.send_tg_alert(f"❌ Lighter 对冲失败: {err_msg}")
                hedge_success = False
            else:
                hedge_success = True
                real_lighter_price = await self.get_lighter_fill_price(client_oid, base)

            if not is_closing:
                if not hedge_success:
                    self.trade_results.append(False)
                    self.logger.critical("🚨 对冲失败，触发回滚逻辑！")
                    await self._rollback_extended(qty_to_hedge)
                    return
                else:
                    self.trade_results.append(True)
                    
                if position_record:
                    position_record['lighter_price'] = real_lighter_price 
                    position_record['spread'] = (real_lighter_price - ext_price)
                    position_record['hedged'] = True
                    position_record['status'] = 'OPEN'
                
                spread_val = (real_lighter_price - ext_price)
                msg = (f"🔵 <b>加仓完成</b>\nExt: {ext_price:.1f}\nLighter: {real_lighter_price:.1f}\n价差: {spread_val:.1f}")
                self.logger.info(f"加仓完成: Hedged=True Spread={spread_val:.1f}")
                await self.send_tg_alert(msg)
            else: 
                total_pnl = Decimal('0')
                remaining = qty
                while remaining > 0 and self.positions:
                    curr = self.positions[0] 
                    match = min(curr['qty'], remaining)
                    p_ext = (ext_price - curr['ext_price']) * match
                    p_lit = Decimal('0')
                    if curr.get('hedged', True) and hedge_success:
                         p_lit = (curr['lighter_price'] - real_lighter_price) * match
                    total_pnl += (p_ext + p_lit)
                    curr['qty'] -= match
                    remaining -= match
                    if curr['qty'] <= Decimal('0.00000001'): self.positions.pop(0)
                
                closing_spread = Decimal('0')
                if hedge_success and real_lighter_price > 0:
                    closing_spread = ext_price - real_lighter_price
                
                msg = (f"🟢 <b>止盈平仓完成</b>\nExt卖出: {ext_price:.1f}\nLit买入: {real_lighter_price:.1f}\n价差: {closing_spread:.1f}\nQty: {qty}\n💰 <b>PnL: ${total_pnl:.4f}</b>")
                self.logger.info(f"🟢 平仓详情 | PnL: ${total_pnl:.4f}")
                await self.send_tg_alert(msg)

        except Exception as e:
            self.logger.error(f"对冲逻辑异常: {e}", exc_info=True)
            if not is_closing:
                self.trade_results.append(False)
        finally: self.pending_hedges -= 1

    async def _rollback_extended(self, qty: Decimal):
        try:
            self.logger.warning(f"🔄 正在回滚 Extended: 紧急砸盘 SELL {qty}...")
            ext_bid, _ = await self.extended_client.fetch_bbo_prices()
            target_price = ext_bid * Decimal('0.95')
            target_price_str = self.extended_client.round_to_tick(target_price)
            
            if hasattr(self.extended_client, 'perpetual_trading_client'):
                client = self.extended_client.perpetual_trading_client
                tif = TimeInForce.IOC if 'TimeInForce' in globals() else "IOC"
                res = await client.place_order(
                    market_name=f"{self.ticker}-USD",
                    amount_of_synthetic=qty,
                    price=Decimal(target_price_str),
                    side=OrderSide.SELL,
                    time_in_force=tif,
                    post_only=False
                )
                if res and getattr(res, 'status', '') == 'OK':
                    self.logger.info("✅ Extended 紧急回滚订单已发送并成交！")
                    if self.positions and self.positions[-1]['status'] == 'HEDGING':
                        self.positions.pop()
                else:
                    error_msg = getattr(res, 'error', 'Unknown Error')
                    self.logger.critical(f"💀 逃生单被拒: {error_msg}")
                    await self.send_tg_alert(f"💀 回滚砸盘失败! Error: {error_msg}")
            else:
                self.logger.critical("💀 无法调用底层 X10 客户端执行回滚！")
        except Exception as e:
            self.logger.critical(f"💀 回滚异常: {e}")

    async def cleanup(self):
        self.stop_flag = True
        if self.current_maker_order_id:
            try: await self.extended_client.cancel_order(self.current_maker_order_id)
            except: pass

    async def run(self):
        try:
            await self.initialize_clients()
            await self.send_tg_alert(f"🚀 策略启动 V7.0 (High Frequency)\nOpen: {self.open_threshold}")
            await self.sync_initial_positions()
            asyncio.create_task(self.run_lighter_ws())
            await asyncio.sleep(5)
            
            SPREAD_BUFFER = Decimal('20') 

            while not self.stop_flag:
                try:
                    await self.check_circuit_breaker()
                    ext_bid, ext_ask = await self.extended_client.fetch_bbo_prices()
                    
                    if ext_bid == 0 or ext_ask == 0:
                         print("\r⏳ 等待 Extended 数据...   ", end="")
                         await asyncio.sleep(0.5)
                         continue

                    current_qty = self.order_quantity
                    if len(self.positions) > 0 and len(self.positions) < self.max_layers: current_qty = self.add_on_quantity
                    elif len(self.positions) >= self.max_layers: current_qty = sum(p['qty'] for p in self.positions)

                    vwap_sell = self.calculate_vwap(current_qty, False) 
                    vwap_buy = self.calculate_vwap(current_qty, True)   

                    # 🔥 追单逻辑 (Order Chasing)
                    if self.current_maker_order_id and self.current_maker_price > 0:
                        # 检查盘口是否偏离
                        should_cancel = False
                        
                        # 1. 超时撤单
                        if time.time() - self.order_start_time > 5: # 缩短到5秒
                            should_cancel = True
                        
                        # 2. 价格偏离撤单
                        if self.current_order_side == 'buy':
                            # 如果新的买一价比我的挂单价高，说明我被埋了
                            if ext_bid > self.current_maker_price:
                                should_cancel = True
                        
                        if should_cancel:
                            self.logger.info(f"🔄 追单/超时: 撤销旧订单...")
                            await self.extended_client.cancel_order(self.current_maker_order_id)
                            self.current_maker_order_id = None
                            await asyncio.sleep(0.5)
                            continue

                        print(f"\r⏳ 挂单中...   ", end="")
                        await asyncio.sleep(0.1); continue
                    
                    if self.pending_hedges > 0:
                        print(f"\r⏳ 对冲中...   ", end="")
                        await asyncio.sleep(0.1); continue
                    
                    if vwap_sell > 0 and ext_ask > 0:
                        # 🔥 优化：使用 Ext Bid + Tick 作为开仓基准 (抢一档)
                        my_maker_bid = ext_bid + self.extended_client.config.tick_size
                        sp_open = vwap_sell - my_maker_bid
                        
                        sp_close = ext_bid - vwap_buy if vwap_buy > 0 else Decimal('-9999')
                        
                        min_required_spread = self.calculate_break_even_spread(ext_ask)
                        current_open_threshold = max(self.open_threshold, min_required_spread)
                        
                        print(f"\r[Qty:{current_qty}] Op:{sp_open:.0f} (Req:{current_open_threshold:.0f}) Cl:{sp_close:.0f}   ", end="")

                        if not self.current_maker_order_id:
                            # 1. 平仓
                            if len(self.positions) > 0 and sp_close > self.close_threshold:
                                self.logger.info(f"💰 触发平仓! 价差: {sp_close:.1f}")
                                total_holding = sum(p['qty'] for p in self.positions)
                                res = await self.extended_client.place_open_order(f"{self.ticker}-USD", total_holding, 'sell')
                                if res.success: 
                                    self.current_maker_order_id = res.order_id
                                    self.current_maker_price = ext_bid + self.extended_client.config.tick_size
                                    self.current_order_side = 'sell'
                                    self.order_start_time = time.time()
                            
                            # 2. 开仓 (Maker Buy)
                            elif len(self.positions) < self.max_layers:
                                target = current_open_threshold + (Decimal(len(self.positions)) * self.add_on_step)
                                if sp_open > target:
                                    # 🔥 深度检查
                                    depth_ok = True
                                    lighter_depth = self.orderbook.get_depth_volume('ask', price_limit=vwap_sell*Decimal('1.001'))
                                    if lighter_depth < current_qty:
                                        # 深度不够，跳过
                                        depth_ok = False
                                        # print("深度不足", end="")

                                    if depth_ok:
                                        print()
                                        self.logger.info(f"💎 触发做多! 预期价差: {sp_open:.1f} (Ext:{my_maker_bid})")
                                        # 挂在买一价 + tick
                                        res = await self.extended_client.place_open_order(f"{self.ticker}-USD", current_qty, 'buy')
                                        if res.success: 
                                            self.current_maker_order_id = res.order_id
                                            self.current_maker_price = my_maker_bid # 记录我们想挂的价格
                                            self.current_order_side = 'buy'
                                            self.order_start_time = time.time()
                    await asyncio.sleep(0.1)
                except KeyboardInterrupt: raise
                except Exception as e:
                    self.logger.error(f"Loop Error: {e}"); await asyncio.sleep(1)

        except: pass
        finally: await self.cleanup()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default="BTC")
    parser.add_argument("--size", type=float, default=0.001)
    parser.add_argument("--add-on-size", type=float, default=None)
    parser.add_argument("--open", type=float, default=105)
    parser.add_argument("--close", type=float, default=-90)
    parser.add_argument("--step", type=float, default=10)
    parser.add_argument("--max-layers", type=int, default=5)
    parser.add_argument("--slippage", type=float, default=0.2)
    args = parser.parse_args()

    addon = args.add_on_size if args.add_on_size is not None else args.size
    arb = ExtendedArb(args.ticker, Decimal(str(args.size)), Decimal(str(addon)), Decimal(str(args.open)), Decimal(str(args.close)), Decimal(str(args.step)), args.max_layers, args.order_timeout if 'order_timeout' in args else 20, args.slippage)
    
    try: asyncio.run(arb.run())
    except KeyboardInterrupt: print("\n👋 策略已停止")
