import asyncio
import os
import logging
import colorlog
from dotenv import load_dotenv

# 引入我们刚才写的两个交易所类
from exchanges.lighter import LighterExchange
from exchanges.variational_private import VariationalPrivateExchange

# 加载配置
load_dotenv()

# 配置日志颜色
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
    # 1. 读取配置
    lighter_index = os.getenv("LIGHTER_API_INDEX")
    lighter_pub = os.getenv("LIGHTER_PUBLIC_KEY")
    lighter_priv = os.getenv("LIGHTER_PRIVATE_KEY")
    
    var_token = os.getenv("VARIATIONAL_TOKEN")
    var_cookie = os.getenv("VARIATIONAL_COOKIE")
    
    trade_amount = float(os.getenv("TRADE_AMOUNT", 100))
    spread_threshold = float(os.getenv("SPREAD_THRESHOLD", 0.002))

    if not all([lighter_index, lighter_pub, lighter_priv, var_token]):
        logger.error("配置文件 .env 缺失必要参数，请检查！")
        return

    # 2. 初始化交易所
    lighter = LighterExchange(lighter_index, lighter_pub, lighter_priv)
    variational = VariationalPrivateExchange(var_token, var_cookie)

    await lighter.connect()
    await variational.connect()

    logger.info("🤖 机器人启动中... (Lighter <-> Variational)")

    # 3. 主循环 (每隔几秒检查一次)
    try:
        while True:
            # 这里你需要实现获取价格的逻辑
            # 由于 Variational 是私有接口，你可能需要轮询 get_balance 里的接口或者其他接口来获取估算价格
            # 假设我们获取到了两个交易所的 ETH 价格：
            
            # lighter_price = await lighter.get_mid_price("ETH-PERP")
            # var_price = ... (从 Variational 获取价格)
            
            # 模拟演示：
            logger.info("正在监控价差... (暂未连接真实行情)")
            
            # 如果价差 > 阈值:
            #     await lighter.create_order(...)
            #     await variational.create_order(...)
            
            await asyncio.sleep(5) # 休息5秒

    except KeyboardInterrupt:
        logger.info("机器人停止运行")
    finally:
        await variational.close()

if __name__ == "__main__":
    asyncio.run(main())
