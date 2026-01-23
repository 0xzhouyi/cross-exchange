import asyncio
import os
import logging
import colorlog
from dotenv import load_dotenv

# 引入我们写好的交易所接口
from exchanges.lighter import LighterExchange
from exchanges.variational_private import VariationalPrivateExchange

# 加载 .env 配置
load_dotenv()

# === 配置日志格式 (让输出好看点) ===
handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
    }
))
logger = logging.getLogger("ArbBot")
logger.addHandler(handler)
logger.setLevel(logging.INFO)

async def main():
    # 1. 检查 Lighter 配置
    l_index = os.getenv("LIGHTER_API_INDEX")
    l_pub = os.getenv("LIGHTER_PUBLIC_KEY")
    l_priv = os.getenv("LIGHTER_PRIVATE_KEY")
    
    # 2. 检查 Variational 配置
    v_token = os.getenv("VARIATIONAL_TOKEN")
    v_cookie = os.getenv("VARIATIONAL_COOKIE")
    
    # 3. 基础参数
    trade_size = float(os.getenv("TRADE_AMOUNT", "0.01")) # 默认交易数量
    spread_trigger = float(os.getenv("SPREAD_THRESHOLD", "0.002")) # 0.2% 价差触发

    if not all([l_index, l_pub, l_priv]):
        logger.error("Lighter 配置缺失！请检查 .env 文件")
        return
    
    if not all([v_token, v_cookie]):
        logger.error("Variational 配置缺失！请检查 .env 文件")
        return

    logger.info("正在连接交易所...")

    # 4. 初始化交易所
    # Lighter 需要把 index 转成 int
    lighter = LighterExchange(int(l_index), l_pub, l_priv)
    variational = VariationalPrivateExchange(v_token, v_cookie)

    try:
        # 5. 建立连接
        await lighter.connect()
        await variational.connect()
        
        logger.info("✅ 所有交易所连接成功！开始监控价差...")
        # === 新增测试代码 ===
        # ... 连接成功后 ...
        
        # 1. 测试余额
        logger.info("正在检查 Variational 余额...")
        await variational.get_balance()

        # 2. 测试询价流程 (不会真的买，只获取价格)
        # 假设我们要买 0.001 个 BTC (根据你的截图，最小单位很小)
        logger.info("正在测试询价流程 (BTC-PERP)...")
        quote = await variational.get_indicative_quote("BTC-PERP", 0.001)
        
        if quote:
            logger.info("流程打通！机器人已准备就绪。")
        else:
            logger.error("流程受阻，请检查网络或 Cookie。")
            
        # ... 进入主循环 ...
        # ==================
        
        # 6. 主循环 (每3秒检查一次)
        while True:
            try:
                # 这里为了演示，我们假设获取 ETH-USDC 的价格
                # 注意：实际代码需要完善 get_orderbook 逻辑
                
                # 模拟获取价格 (你需要完善 exchanges/ 中的 get_orderbook 函数来获取真实数据)
                # logger.info("正在询价...") 
                
                # 示例：获取 Variational 余额来证明连接活着
                # await variational.get_balance()
                
                # 示例：尝试在 Lighter 下一个极小的单子测试 (慎用)
                # await lighter.create_order("ETH-USDC", "buy", 1000.0, 0.01)

                await asyncio.sleep(5) 

            except Exception as e:
                logger.error(f"循环中发生错误: {e}")
                await asyncio.sleep(5)

    except KeyboardInterrupt:
        logger.info("机器人正在停止...")
    finally:
        await variational.close()
        logger.info("程序已退出")

if __name__ == "__main__":
    asyncio.run(main())
