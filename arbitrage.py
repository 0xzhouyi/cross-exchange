# === 🔥 第一步：最优先加载环境变量 ===
from dotenv import load_dotenv
import os
load_dotenv(override=True)

import asyncio
import logging
import sys
import time
import json
import aiohttp
from decimal import Decimal
from typing import Optional, List

import websockets

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

        # Telegram
        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.tg_enabled = bool(self.tg_token and self.tg_chat_id)
        
        # === 💰 铁账本 ===
        self.positions: List[dict] = [] 
        self.pending_hedges = 0  

        self.extended_client: Optional[ExtendedClient] = None
        self.lighter_client: Optional[SignerClient] = None
        self.api_client: Optional[ApiClient] = None
        
        self.lighter_market_id = 1
        self.current_maker_order_id = None
        self.order_start_time = 0
        
        self.ext_bid = Decimal('0')
        self.ext_ask = Decimal('0')
        self.lighter_bid = Decimal('0')
        self.lighter_ask = Decimal('0')
        
        self.LIGHTER_BASE_DECIMALS = 5   
        self.LIGHTER_PRICE_DECIMALS = 1  
        
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

    async def send_tg_alert(self, message: str):
        if not self.tg_enabled: return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": message, "parse_mode": "HTML"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200: self.logger.error(f"TG Error: {await resp.text()}")
        except Exception as e:
            self.logger.error(f"TG Exception: {e}")

    async def initialize_clients(self):
        raw_key = os.getenv('API_KEY_PRIVATE_KEY')
        if raw_key.startswith("0x"): raw_key = raw_key[2:]
        try:
            self.lighter_client = SignerClient(
                url=self.lighter_base_url,
                account_index=self.account_index,
                api_private_keys={self.api_key_index: raw_key}
            )
            conf = Configuration(host=self.lighter_base_url)
            self.api_client = ApiClient(configuration=conf)
            self.logger.info("✅ Lighter 客户端初始化成功")
        except Exception as e:
            self.logger.error(f"❌ Lighter 初始化失败: {e}")
            raise

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
                self.positions.append({
                    'qty': abs(current_pos),
                    'ext_price': self.ext_bid if self.ext_bid > 0 else Decimal('88000'),
                    'lighter_price': self.lighter_ask if self.lighter_ask > 0 else Decimal('88100'),
                    'spread': Decimal('100'),
                    'hedged': True,
                    'status': 'RESTORED'
                })
            else:
                self.logger.info("✅ 初始状态: 空仓")
        except Exception as e:
            self.logger.error(f"检查初始持仓失败: {e}")

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

                        if msg.get("type") in ["subscribed/order_book", "update/order_book"]:
                            payload = msg.get('order_book') or msg.get('data') or msg.get('payload', {})
                            bids = payload.get('bids', [])
                            asks = payload.get('asks', [])
                            if bids: self.lighter_bid = Decimal(str(bids[0][0] if isinstance(bids[0], list) else bids[0].get('price')))
                            if asks: self.lighter_ask = Decimal(str(asks[0][0] if isinstance(asks[0], list) else asks[0].get('price')))
            except Exception as e:
                await asyncio.sleep(5)

    async def get_lighter_fill_price(self, client_order_index: int, fallback_price: Decimal) -> Decimal:
        try:
            await asyncio.sleep(1.5)
            order_api = OrderApi(self.api_client)
            target_order = None
            
            if hasattr(order_api, 'account_inactive_orders'):
                orders_resp = await order_api.account_inactive_orders(account_index=self.account_index, market_id=self.lighter_market_id, limit=10)
                target_order = self._find_order(orders_resp, client_order_index)

            if not target_order and hasattr(order_api, 'account_active_orders'):
                orders_resp = await order_api.account_active_orders(account_index=self.account_index, market_id=self.lighter_market_id)
                target_order = self._find_order(orders_resp, client_order_index)
            
            if target_order:
                raw_price = target_order.avg_execution_price
                if raw_price is None: return fallback_price
                raw_price_dec = Decimal(str(raw_price))
                if raw_price_dec > 1000000: return raw_price_dec / (10 ** self.LIGHTER_PRICE_DECIMALS)
                return raw_price_dec
            return fallback_price
        except:
            return fallback_price

    def _find_order(self, orders_obj, client_oid):
        orders_list = orders_obj if isinstance(orders_obj, list) else (getattr(orders_obj, 'orders', []) if hasattr(orders_obj, 'orders') else [])
        for o in orders_list:
            if getattr(o, 'client_order_id', -1) == client_oid: return o
        return None

    def handle_extended_order_update(self, update_data):
        status = update_data.get('status')
        if status == 'FILLED':
            side = update_data.get('side')
            try: qty = Decimal(str(update_data.get('filled_size', 0))).quantize(Decimal('0.00001')) 
            except: qty = Decimal('0')
            price = Decimal(str(update_data.get('price')))
            
            self.logger.info(f"⚡ Ext成交: {side} {qty} @ {price}")
            
            is_close_action = (side == 'sell')
            target_side = 'buy' if is_close_action else 'sell'
            
            if not is_close_action: # 开仓/加仓 - 立即记账
                position_entry = {
                    'qty': qty,
                    'ext_price': price,
                    'lighter_price': Decimal('0'),
                    'spread': Decimal('0'),
                    'hedged': False,
                    'status': 'HEDGING'
                }
                self.positions.append(position_entry)
                self.logger.info(f"✅ 账本更新: 已记录 Ext 持仓 {qty} (等待对冲...)")
                
                self.pending_hedges += 1
                asyncio.create_task(self.place_lighter_order(target_side, qty, is_close_action, price, position_entry))
            
            else: # 平仓
                self.pending_hedges += 1
                asyncio.create_task(self.place_lighter_order(target_side, qty, is_close_action, price, None))

            if self.current_maker_order_id == update_data.get('order_id'):
                self.current_maker_order_id = None

    async def place_lighter_order(self, side: str, qty: Decimal, is_closing: bool, ext_price: Decimal, position_record: Optional[dict]):
        try:
            qty_to_hedge = qty
            
            if is_closing:
                qty_to_hedge = Decimal('0')
                temp_qty_check = qty
                for p in self.positions:
                    if temp_qty_check <= 0: break
                    consumable = min(p['qty'], temp_qty_check)
                    if p.get('hedged', True): 
                        qty_to_hedge += consumable
                    temp_qty_check -= consumable
                
                if qty_to_hedge < qty:
                    self.logger.info(f"💡 智能平仓: 仅需对冲 {qty_to_hedge} (部分为裸头寸)")

            hedge_success = False
            real_lighter_price = Decimal('0')

            if qty_to_hedge > 0:
                slippage = self.hedge_slippage
                if side == 'sell': 
                    base_price = self.lighter_bid
                    worst_price = base_price * (Decimal('1') - slippage)
                    is_ask = True
                else:  
                    base_price = self.lighter_ask
                    worst_price = base_price * (Decimal('1') + slippage)
                    is_ask = False

                atomic_qty = int(qty_to_hedge * (10 ** self.LIGHTER_BASE_DECIMALS)) 
                atomic_price = int(worst_price * (10 ** self.LIGHTER_PRICE_DECIMALS))
                client_order_index = int(time.time() * 1000) % 2147483647
                
                res = await self.lighter_client.create_market_order(
                    market_index=self.lighter_market_id,
                    client_order_index=client_order_index,
                    base_amount=atomic_qty,
                    is_ask=is_ask,
                    avg_execution_price=atomic_price,
                    reduce_only=False 
                )

                if isinstance(res, tuple) and len(res) >= 3 and res[2] is not None:
                    err_msg = f"❌ Lighter 对冲失败: {res[2]}"
                    await self.send_tg_alert(err_msg)
                    hedge_success = False
                else:
                    hedge_success = True
                    real_lighter_price = await self.get_lighter_fill_price(client_order_index, base_price)

            if not is_closing: 
                # [开仓/加仓]
                if position_record:
                    position_record['lighter_price'] = real_lighter_price if hedge_success else Decimal('0')
                    position_record['spread'] = (real_lighter_price - ext_price) if hedge_success else Decimal('0')
                    position_record['hedged'] = hedge_success
                    position_record['status'] = 'OPEN'
                
                layer_count = len(self.positions)
                hedge_status_str = "" if hedge_success else " [⚠️裸头寸]"
                spread_val = (real_lighter_price - ext_price) if hedge_success else Decimal('0')
                msg = (f"🔵 <b>加仓完成{hedge_status_str} (第{layer_count}层)</b>\n"
                       f"Ext: {ext_price:.1f}\nLighter: {real_lighter_price:.1f}\n"
                       f"价差: {spread_val:.1f}\n" 
                       f"数量: {qty} BTC")
                self.logger.info(f"加仓更新完毕: Hedged={hedge_success} Spread={spread_val:.1f}")
                await self.send_tg_alert(msg)
                
            else: 
                # [平仓]
                total_pnl = Decimal('0')
                remaining_to_close = qty
                
                while remaining_to_close > 0 and self.positions:
                    current_pos = self.positions[0] 
                    match_qty = min(current_pos['qty'], remaining_to_close)
                    
                    p_ext = (ext_price - current_pos['ext_price']) * match_qty
                    p_lit = Decimal('0')
                    
                    if current_pos.get('hedged', True) and hedge_success:
                         p_lit = (current_pos['lighter_price'] - real_lighter_price) * match_qty

                    total_pnl += (p_ext + p_lit)
                    
                    current_pos['qty'] -= match_qty
                    remaining_to_close -= match_qty
                    
                    if current_pos['qty'] <= Decimal('0.00000001'):
                        self.positions.pop(0)
                
                msg = (f"🟢 <b>止盈平仓</b>\n"
                       f"Ext卖: {ext_price:.1f}\n"
                       f"数量: {qty} BTC\n"
                       f"💰 <b>本次盈利: ${total_pnl:.4f}</b>")
                self.logger.info(f"平仓完成: Qty={qty} PnL=${total_pnl:.4f}")
                await self.send_tg_alert(msg)

        except Exception as e:
            self.logger.error(f"对冲逻辑异常: {e}", exc_info=True)
        finally:
            self.pending_hedges -= 1

    async def run(self):
        await self.initialize_clients()
        await self.send_tg_alert(f"🚀 <b>策略启动 (固定网格阶梯)</b>\nOpen: {self.open_threshold} | Close: {self.close_threshold}\n滑点: {self.hedge_slippage*100}%")
        await self.check_initial_position()
        asyncio.create_task(self.run_lighter_ws())
        await asyncio.sleep(5)
        
        while not self.stop_flag:
            try:
                if self.current_maker_order_id and time.time() - self.order_start_time > self.order_timeout:
                    await self.extended_client.cancel_order(self.current_maker_order_id)
                    self.current_maker_order_id = None
                
                if self.pending_hedges > 0:
                    print(f"\r⏳ 对冲中 ({self.pending_hedges})...   ", end="")
                    await asyncio.sleep(0.1)
                    continue

                ext_bid, ext_ask = await self.extended_client.fetch_bbo_prices()
                
                if self.lighter_bid > 0 and ext_ask > 0:
                    spread_open = self.lighter_bid - ext_ask
                    spread_close = ext_bid - self.lighter_ask
                    
                    layers = len(self.positions)
                    status_icon = "🔴" if layers > 0 else "🟢"
                    total_holding = sum(p['qty'] for p in self.positions)
                    
                    # 🔥 核心修正：计算下一个绝对目标位 (Next Target)
                    next_target_spread = self.open_threshold + (Decimal(layers) * self.add_on_step)
                    # 如果已经满仓，就不显示下一目标
                    target_info = f"Next:>{next_target_spread:.0f}" if layers < self.max_layers else "Maxed"
                    
                    print(f"\r[{status_icon} {layers}层 {total_holding}BTC] Ext:{ext_bid:.0f}/{ext_ask:.0f} Lit:{self.lighter_bid:.0f}/{self.lighter_ask:.0f} | Op:{spread_open:+.0f} Cl:{spread_close:+.0f} ({target_info})  ", end="")

                    if not self.current_maker_order_id:
                        # 1. 平仓
                        if layers > 0 and spread_close > self.close_threshold:
                            self.logger.info(f"\n💰 触发止盈平仓! 总数量: {total_holding}")
                            res = await self.extended_client.place_open_order(f"{self.ticker}-USD", total_holding, 'sell')
                            if res.success: 
                                self.current_maker_order_id = res.order_id
                                self.order_start_time = time.time()
                        
                        # 2. 开仓/加仓
                        elif layers < self.max_layers:
                            current_order_qty = Decimal('0')
                            should_open = False
                            
                            # 🔥 使用绝对阈值判断
                            if spread_open > next_target_spread:
                                if layers == 0:
                                    self.logger.info(f"\n💎 首次开仓! 价差: {spread_open:.1f}")
                                    current_order_qty = self.order_quantity
                                else:
                                    self.logger.info(f"\n🚀 触发加仓! 价差: {spread_open:.1f} (目标: >{next_target_spread:.1f})")
                                    current_order_qty = self.add_on_quantity
                                should_open = True
                            
                            if should_open and current_order_qty > 0:
                                res = await self.extended_client.place_open_order(f"{self.ticker}-USD", current_order_qty, 'buy')
                                if res.success: 
                                    self.current_maker_order_id = res.order_id
                                    self.order_start_time = time.time()
                
                await asyncio.sleep(0.2)
            except KeyboardInterrupt:
                self.stop_flag = True
                break
            except Exception as e:
                self.logger.error(f"Error: {e}")
                await asyncio.sleep(1)

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
    parser.add_argument("--slippage", type=float, default=0.2, help="滑点保护(百分比)")
    args = parser.parse_args()

    addon = args.add_on_size if args.add_on_size is not None else args.size

    arb = ExtendedArb(
        ticker=args.ticker, 
        order_quantity=Decimal(str(args.size)), 
        add_on_quantity=Decimal(str(addon)),
        open_threshold=Decimal(str(args.open)), 
        close_threshold=Decimal(str(args.close)),
        add_on_step=Decimal(str(args.step)),
        max_layers=args.max_layers,
        hedge_slippage=args.slippage
    )
    asyncio.run(arb.run())
