import os
import asyncio
import json
from dotenv import load_dotenv
from lighter.signer_client import SignerClient
from lighter import ApiClient, Configuration, OrderApi

# 加载环境变量
load_dotenv(override=True)

async def main():
    print("🚀 开始 Lighter 深度诊断 (修复Auth版)...")
    
    # 1. 配置与初始化
    correct_url = "https://mainnet.zklighter.elliot.ai"
    try:
        account_index = int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0"))
        api_key_index = int(os.getenv("LIGHTER_API_KEY_INDEX", "0"))
        api_priv = os.getenv("API_KEY_PRIVATE_KEY")
        if api_priv and api_priv.startswith("0x"):
            api_priv = api_priv[2:]
            
        print(f"👤 Account Index: {account_index}")
        
        # 初始化 SignerClient (用于生成 Auth Token)
        client = SignerClient(
            url=correct_url,
            account_index=account_index,
            api_private_keys={api_key_index: api_priv}
        )
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return

    # 2. 🔥 生成 Auth Token (这是之前缺失的关键步骤)
    print("🔑 正在生成 API Auth Token...")
    try:
        auth_token, err = client.create_auth_token_with_expiry(api_key_index=api_key_index)
        if err:
            print(f"❌ Auth Token 生成失败: {err}")
            return
        print("✅ Auth Token 生成成功")
    except Exception as e:
        print(f"❌ Auth Token 生成异常: {e}")
        return

    # 3. 初始化 API 客户端
    conf = Configuration(host=correct_url)
    api_client = ApiClient(configuration=conf)
    order_api = OrderApi(api_client)

    # 4. 🕵️ 检查“死亡”订单
    print("\n" + "="*40)
    print("💀 步骤: 查询最近的【失败/取消订单】")
    print("="*40)
    try:
        print(f"🔍 正在查询账户 {account_index} 的历史订单 (Top 10)...")
        
        # 使用带 auth 参数的请求
        inactive_orders = await order_api.account_inactive_orders(
            account_index=account_index,
            limit=10,
            auth=auth_token  # <--- 传入 Token
        )
        
        # 解析返回结果
        orders_list = getattr(inactive_orders, 'orders', [])
        
        if orders_list:
            print(f"✅ 成功找到 {len(orders_list)} 条历史记录！\n")
            for i, order in enumerate(orders_list):
                # 获取基本字段
                oid = getattr(order, 'order_id', getattr(order, 'id', '?'))
                status = getattr(order, 'status', 'UNKNOWN')
                side_str = "SELL" if getattr(order, 'is_ask', False) else "BUY"
                
                # 数量处理
                raw_size = getattr(order, 'base_amount', 0)
                readable_size = float(raw_size) / 100000  # BTC scale 1e5
                
                # 价格处理
                raw_price = getattr(order, 'price', 0)
                readable_price = float(raw_price) / 10  # BTC price scale usually 1e1
                
                print(f"[{i+1}] 订单ID: {oid}")
                print(f"    方向: {side_str} | 数量: {readable_size} ({raw_size}) | 价格: {readable_price}")
                print(f"    状态: {status} (3=Cancelled, 4=Rejected, 2=Filled)")
                
                # 尝试打印更详细的取消原因（如果 SDK 返回的话）
                # 注意：不同版本 SDK 字段不同，这里打印原始对象的一部分帮助排查
                # print(f"    原始数据: {order}") 
                print("-" * 30)
                
            print("\n💡 分析提示:")
            print("   - 如果全是 Status 3 (Cancelled): 极大可能是滑点太低，触发了价格保护 (Price Protection)。")
            print("   - 如果全是 Status 4 (Rejected): 可能是余额不足 (尽管你之前查有钱)。")
        else:
            print("❌ 查询成功，但没有返回任何历史订单。")
            print("   这说明订单根本没有到达撮合引擎 (可能在 API 网关层就被拦截，或 nonce 问题)。")

    except Exception as e:
        print(f"❌ 查询过程发生异常: {e}")

    # 关闭连接
    await api_client.close()

if __name__ == "__main__":
    asyncio.run(main())
