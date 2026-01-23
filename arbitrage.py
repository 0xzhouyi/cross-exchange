import asyncio
import sys
import argparse
from decimal import Decimal
import dotenv

# 原有的 EdgeX 策略
from strategy.edgex_arb import EdgexArb
# [新增 1] 导入新的 Extended 策略
# 确保你已经把上一条回复的代码保存为 strategy/extended_arb.py
from strategy.extended_arb import ExtendedArb 


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Cross-Exchange Arbitrage Bot Entry Point',
        formatter_class=argparse.RawDescriptionHelpFormatter
        )

    # 修改 default 或 help 描述，提示支持 extended
    parser.add_argument('--exchange', type=str, default='edgex',
                        help='Exchange to use (edgex, extended)') 
    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Ticker symbol (default: BTC)')
    parser.add_argument('--size', type=str, required=True,
                        help='Number of tokens to buy/sell per order')
    parser.add_argument('--fill-timeout', type=int, default=5,
                        help='Timeout in seconds for maker order fills (default: 5)')
    parser.add_argument('--max-position', type=Decimal, default=Decimal('0'),
                        help='Maximum position to hold (default: 0)')
    parser.add_argument('--long-threshold', type=Decimal, default=Decimal('10'),
                        help='Long threshold (Spread > threshold triggers Long Maker)')
    parser.add_argument('--short-threshold', type=Decimal, default=Decimal('10'),
                        help='Short threshold (Spread > threshold triggers Short Maker)')
    return parser.parse_args()


def validate_exchange(exchange):
    """Validate that the exchange is supported."""
    # [新增 2] 在列表里加入 'extended'
    supported_exchanges = ['edgex', 'extended']
    
    if exchange.lower() not in supported_exchanges:
        print(f"Error: Unsupported exchange '{exchange}'")
        print(f"Supported exchanges: {', '.join(supported_exchanges)}")
        sys.exit(1)


async def main():
    """Main entry point that creates and runs the cross-exchange arbitrage bot."""
    args = parse_arguments()

    dotenv.load_dotenv()

    # Validate exchange
    validate_exchange(args.exchange)

    try:
        bot = None
        
        # [新增 3] 根据参数选择实例化哪个策略类
        if args.exchange.lower() == 'edgex':
            print("🚀 Initializing EdgeX <-> Lighter Arbitrage...")
            bot = EdgexArb(
                ticker=args.ticker.upper(),
                order_quantity=Decimal(args.size),
                fill_timeout=args.fill_timeout,
                max_position=args.max_position,
                long_ex_threshold=Decimal(args.long_threshold),
                short_ex_threshold=Decimal(args.short_threshold)
            )
        
        elif args.exchange.lower() == 'extended':
            print("🚀 Initializing Extended <-> Lighter Arbitrage...")
            bot = ExtendedArb(
                ticker=args.ticker.upper(),
                order_quantity=Decimal(args.size),
                fill_timeout=args.fill_timeout,
                max_position=args.max_position,
                long_ex_threshold=Decimal(args.long_threshold),
                short_ex_threshold=Decimal(args.short_threshold)
            )

        # Run the bot
        if bot:
            await bot.run()

    except KeyboardInterrupt:
        print("\nCross-Exchange Arbitrage interrupted by user")
        return 1
    except Exception as e:
        print(f"Error running cross-exchange arbitrage: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
