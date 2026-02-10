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
from typing import Optional, List, Dict

# 屏蔽 asyncio 的调试日志
logging.getLogger("asyncio").setLevel(logging.WARNING)

if not os.getenv("API_KEY_PRIVATE_KEY"):
    print("❌ 严重错误: 未找到 API_KEY_PRIVATE_KEY")
    sys.exit(1)

try:
    import lighter
    from lighter.signer_client import SignerClient
    from lighter import ApiClient, Configuration, AccountApi, OrderApi
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

# === 🛡️ 本地订单薄维护 ===
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
        self.order_timeout = order_timeout
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
        
        self.current_maker_order_id = None
        self.current_maker_price = Decimal('0')
        self.current_order_side = None
        self.order_start_time = 0
        
        self.orderbook = LocalOrderBook()
        self.ext_bid = Decimal('0')
        self.ext_ask = Decimal('0')
        
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

    async def check_initial_position(self):
        try:
            account_api = AccountApi(self.api_client)
            account_data = await account_api.account(by="index", value=str(self.account_index))
            positions = []
            if hasattr(account_data, 'accounts') and len(account_data.accounts) > 0:
                positions = account_data.accounts[0].positions
            current_pos = Decimal('0')
            for pos in positions:
                if str(getattr(pos, 'market_id', None)) == str(self.lighter_market_id):
                    current_pos = Decimal(str(getattr(pos, 'position', 0)))
                    break
            if abs(current_pos) > 0:
                self.logger.warning(f"⚠️ 恢复已有持仓: {current_pos} BTC")
                self.positions.append({'qty': abs(current_pos), 'ext_price': self.ext_bid if self.ext_bid>0 else Decimal('80000'), 'lighter_price': Decimal('0'), 'spread': Decimal('0'), 'hedged': True, 'status': 'RESTORED'})
            else: self.logger.info("✅ 初始状态: 空仓")
        except: pass

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
            
            # 打印时先换行，防止被 status bar 覆盖
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

            hedge_success = False
            real_lighter_price = Decimal('0')
            est_fill_price = self.calculate_vwap(qty_to_hedge, is_buy=(side=='buy'))
            if est_fill_price == 0: est_fill_price = Decimal('0')

            if qty_to_hedge > 0:
                slippage = self.hedge_slippage
                if side == 'sell': 
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
                    await self.send_tg_alert(f"❌ Lighter 对冲失败: {res[2]}")
                else:
                    hedge_success = True
                    real_lighter_price = await self.get_lighter_fill_price(client_oid, base)

            # === 打印输出逻辑 ===
            print() # 换行
            if not is_closing: 
                if position_record:
                    position_record['lighter_price'] = real_lighter_price if hedge_success else Decimal('0')
                    position_record['spread'] = (real_lighter_price - ext_price) if hedge_success else Decimal('0')
                    position_record['hedged'] = hedge_success
                    position_record['status'] = 'OPEN'
                
                spread_val = (real_lighter_price - ext_price) if hedge_success else Decimal('0')
                msg = (f"🔵 <b>加仓完成</b>\nExt: {ext_price:.1f}\nLighter: {real_lighter_price:.1f}\n价差: {spread_val:.1f}")
                self.logger.info(f"加仓完成: Hedged={hedge_success} Spread={spread_val:.1f}")
                await self.send_tg_alert(msg)
            else: 
                # === 🔴 平仓明细 ===
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
                
                # 计算本次平仓的实际价差 (Ext卖 - Lit买)
                closing_spread = Decimal('0')
                if hedge_success and real_lighter_price > 0:
                    closing_spread = ext_price - real_lighter_price
                
                msg = (f"🟢 <b>止盈平仓完成</b>\n"
                       f"Ext卖出: {ext_price:.1f}\n"
                       f"Lit买入: {real_lighter_price:.1f}\n"
                       f"实际价差: {closing_spread:.1f}\n"
                       f"数量: {qty} BTC\n"
                       f"💰 <b>本次盈利: ${total_pnl:.4f}</b>")
                
                self.logger.info(f"🟢 平仓详情 | Ext: {ext_price:.1f} | Lit: {real_lighter_price:.1f} | 价差: {closing_spread:.1f} | PnL: ${total_pnl:.4f}")
                await self.send_tg_alert(msg)

        except Exception as e:
            self.logger.error(f"对冲逻辑异常: {e}", exc_info=True)
        finally: self.pending_hedges -= 1

    async def cleanup(self):
        self.stop_flag = True
        if self.current_maker_order_id:
            try: await self.extended_client.cancel_order(self.current_maker_order_id)
            except: pass

    async def run(self):
        try:
            await self.initialize_clients()
            await self.send_tg_alert(f"🚀 策略启动 V4.1\nOpen: {self.open_threshold}")
            await self.check_initial_position()
            asyncio.create_task(self.run_lighter_ws())
            await asyncio.sleep(5)
            
            SPREAD_BUFFER = Decimal('20') 

            while not self.stop_flag:
                try:
                    ext_bid, ext_ask = await self.extended_client.fetch_bbo_prices()
                    current_qty = self.order_quantity
                    if len(self.positions) > 0 and len(self.positions) < self.max_layers: current_qty = self.add_on_quantity
                    elif len(self.positions) >= self.max_layers: current_qty = sum(p['qty'] for p in self.positions)

                    vwap_sell = self.calculate_vwap(current_qty, False)
                    vwap_buy = self.calculate_vwap(current_qty, True)

                    # 动态护航逻辑
                    if self.current_maker_order_id and self.current_maker_price > 0:
                        if time.time() - self.order_start_time > self.order_timeout:
                            self.logger.info(f"⏰ 超时撤单...")
                            await self.extended_client.cancel_order(self.current_maker_order_id)
                            self.current_maker_order_id = None
                        else:
                            live_spread = Decimal('0')
                            target_min = Decimal('0')
                            
                            if self.current_order_side == 'buy': 
                                if vwap_sell == 0: live_spread = Decimal('-9999')
                                else: live_spread = vwap_sell - self.current_maker_price
                                layer_idx = len(self.positions)
                                target_min = (self.open_threshold + (Decimal(layer_idx) * self.add_on_step)) - SPREAD_BUFFER
                            elif self.current_order_side == 'sell':
                                if vwap_buy == 0: live_spread = Decimal('-9999')
                                else: live_spread = self.current_maker_price - vwap_buy
                                target_min = self.close_threshold - SPREAD_BUFFER 

                            if self.current_order_side == 'buy' and live_spread < target_min:
                                print()
                                self.logger.warning(f"📉 利润恶化: {live_spread:.1f} < {target_min:.1f} -> 撤单")
                                await self.extended_client.cancel_order(self.current_maker_order_id)
                                self.current_maker_order_id = None
                                await asyncio.sleep(1)

                        print(f"\r⏳ 挂单中... VWAP价差: {live_spread:.1f} (Buffer {target_min:.1f})   ", end="")
                        await asyncio.sleep(0.1); continue
                    
                    if self.pending_hedges > 0:
                        print(f"\r⏳ 对冲中 ({self.pending_hedges})...   ", end="")
                        await asyncio.sleep(0.1); continue
                    
                    if vwap_sell > 0 and ext_ask > 0:
                        sp_open = vwap_sell - ext_ask
                        sp_close = ext_bid - vwap_buy if vwap_buy > 0 else Decimal('-9999')
                        
                        print(f"\r[Qty:{current_qty}] Ext:{ext_bid:.0f}/{ext_ask:.0f} Lit:{vwap_sell:.0f}/{vwap_buy:.0f} | Op:{sp_open:+.0f} Cl:{sp_close:+.0f}   ", end="")

                        if not self.current_maker_order_id:
                            # 平仓
                            if len(self.positions) > 0 and sp_close > self.close_threshold:
                                print()
                                self.logger.info(f"💰 触发平仓! 价差: {sp_close:.1f}")
                                total_holding = sum(p['qty'] for p in self.positions)
                                res = await self.extended_client.place_open_order(f"{self.ticker}-USD", total_holding, 'sell')
                                if res.success: 
                                    self.current_maker_order_id = res.order_id
                                    self.current_maker_price = ext_bid + self.extended_client.config.tick_size
                                    self.current_order_side = 'sell'
                                    self.order_start_time = time.time()
                            # 开仓
                            elif len(self.positions) < self.max_layers:
                                target = self.open_threshold + (Decimal(len(self.positions)) * self.add_on_step)
                                if sp_open > target:
                                    print()
                                    self.logger.info(f"💎 触发开仓! 价差: {sp_open:.1f}")
                                    res = await self.extended_client.place_open_order(f"{self.ticker}-USD", current_qty, 'buy')
                                    if res.success: 
                                        self.current_maker_order_id = res.order_id
                                        self.current_maker_price = ext_ask - self.extended_client.config.tick_size
                                        self.current_order_side = 'buy'
                                        self.order_start_time = time.time()
                    
                    await asyncio.sleep(0.2)
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
