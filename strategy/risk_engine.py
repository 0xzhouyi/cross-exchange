# strategy/risk_engine.py

import asyncio
import logging
from decimal import Decimal

class RiskEngine:
    def __init__(self, extended_client, lighter_client, logger, max_drawdown=0.15):
        self.extended = extended_client
        self.lighter = lighter_client
        self.logger = logger
        self.max_drawdown = max_drawdown  # 允许最大回撤 15%
        self.initial_equity = None
        self.is_halted = False

    async def check_health(self):
        """
        生命体征检测：如果保证金率危险，返回 False 并触发报警
        """
        if self.is_halted:
            return False, "SYSTEM_HALTED"

        try:
            # 1. 获取 Extended 权益 (需要你在 ExtendedClient 实现 get_account_summary)
            # 假设返回结构: {'equity': 1000, 'margin_ratio': 10.5}
            ext_info = await self.extended.get_account_summary()
            
            # 2. 获取 Lighter 权益 (利用 LighterClient 现有方法)
            # Lighter SDK 通常返回 available_balance 或通过 positions 估算
            lighter_pos = await self.lighter.get_account_positions()
            # 这里简化处理，你需要根据 Lighter SDK 补充获取权益逻辑
            
            # === 核心风控逻辑 ===
            
            # A. 保证金率检查 (假设阈值为 3.0 即 300%)
            if ext_info.get('margin_ratio', 100) < 3.0:
                self.is_halted = True
                return False, f"🚨 Extended 保证金率过低: {ext_info['margin_ratio']}"

            # B. 强制止损 (总权益回撤检查)
            current_total_equity = ext_info.get('equity', 0) # + lighter_equity
            if self.initial_equity is None:
                self.initial_equity = current_total_equity
            
            if current_total_equity < self.initial_equity * (1 - self.max_drawdown):
                self.is_halted = True
                return False, "🚨 触发总账户最大回撤熔断！"

            return True, "OK"

        except Exception as e:
            self.logger.error(f"风控检查异常: {e}")
            # 保守起见，风控报错也视为不安全
            return False, "RISK_CHECK_ERROR"

    async def emergency_shutdown(self):
        """
        核按钮：取消所有订单，并尝试平仓
        """
        self.logger.critical("☢️ 正在执行紧急关停程序...")
        # 1. Cancel All
        await self.extended.cancel_all_orders()
        # await self.lighter.cancel_all_orders()
        
        # 2. Close Positions (市价全平)
        # 实现市价平仓逻辑...
        self.logger.critical("☢️ 关停程序完成，进程退出。")
        import sys
        sys.exit(1)
