import re
import sys

def analyze_log(log_file="btc_arb.log"):
    stats = {
        "maker_orders_placed": 0,
        "maker_fills": 0,
        "hedges_success": 0,
        "closes": 0,
        "total_pnl": 0.0,
        "rollbacks": 0,
        "errors": 0,
        "spreads_open": [],
        "spreads_close": []
    }

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                # 统计挂单
                if "💎 触发做多" in line:
                    stats["maker_orders_placed"] += 1
                
                # 统计成交
                elif "⚡ Ext成交" in line:
                    stats["maker_fills"] += 1
                
                # 统计开仓对冲与价差
                elif "加仓完成" in line:
                    stats["hedges_success"] += 1
                    match = re.search(r"Spread=([-\d\.]+)", line)
                    if match:
                        stats["spreads_open"].append(float(match.group(1)))
                
                # 统计平仓与盈利
                elif "🟢 平仓详情" in line:
                    stats["closes"] += 1
                    pnl_match = re.search(r"PnL: \$([-\d\.]+)", line)
                    if pnl_match:
                        stats["total_pnl"] += float(pnl_match.group(1))
                    
                    spread_match = re.search(r"价差: ([-\d\.]+)", line)
                    if spread_match:
                        stats["spreads_close"].append(float(spread_match.group(1)))
                
                # 统计回滚与错误
                elif "💀 正在回滚" in line or "🚨 对冲失败" in line:
                    stats["rollbacks"] += 1
                elif "❌" in line:
                    stats["errors"] += 1

    except FileNotFoundError:
        print(f"找不到日志文件: {log_file}")
        return

    # 打印最终报告
    print("="*50)
    print("📊 24小时套利系统诊断报告")
    print("="*50)
    print(f"📈 尝试挂单次数 (Maker Placed): {stats['maker_orders_placed']}")
    print(f"⚡ 实际成交次数 (Maker Filled): {stats['maker_fills']}")
    
    fill_rate = (stats['maker_fills'] / stats['maker_orders_placed'] * 100) if stats['maker_orders_placed'] > 0 else 0
    print(f"🎯 挂单成交率 (Fill Rate): {fill_rate:.2f}%")
    
    print(f"🛡️ 对冲成功次数 (Hedged): {stats['hedges_success']}")
    print(f"🟢 完成平仓次数 (Closed): {stats['closes']}")
    print(f"💰 累计净利润 (Total PnL): ${stats['total_pnl']:.4f}")
    
    if stats["spreads_open"]:
        avg_open = sum(stats["spreads_open"]) / len(stats["spreads_open"])
        print(f"📊 平均真实开仓价差 (Avg Open Spread): ${avg_open:.2f}")
    if stats["spreads_close"]:
        avg_close = sum(stats["spreads_close"]) / len(stats["spreads_close"])
        print(f"📊 平均真实平仓价差 (Avg Close Spread): ${avg_close:.2f}")

    print("-"*50)
    print(f"💀 触发回滚次数 (Rollbacks): {stats['rollbacks']}")
    print(f"❌ 其他错误次数 (Errors): {stats['errors']}")
    print("="*50)

if __name__ == "__main__":
    log_name = sys.argv[1] if len(sys.argv) > 1 else "btc_arb.log"
    analyze_log(log_name)
