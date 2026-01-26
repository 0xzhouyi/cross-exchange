import os
from dotenv import load_dotenv

# 1. 检查当前在哪里运行
print(f"📂 当前工作目录: {os.getcwd()}")

# 2. 检查 .env 文件是否真的存在
env_path = os.path.join(os.getcwd(), '.env')
file_exists = os.path.exists(env_path)
print(f"📄 .env 文件是否存在: {'✅ 存在' if file_exists else '❌ 不存在'}")

if file_exists:
    # 3. 尝试直接打印文件内容（只打前几行，确保 key 在里面）
    print("\n--- .env 文件原始内容预览 ---")
    with open(env_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        # 简单脱敏打印，看看 key 是否在
        for line in content.splitlines():
            if "API_KEY_PRIVATE_KEY" in line:
                print(f"👉 发现行: {line[:25]}...")
            elif "EXTENDED_API_KEY" in line:
                print(f"👉 发现行: {line[:25]}...")
    print("----------------------------\n")

# 4. 尝试加载并读取
print("🔄 正在执行 load_dotenv()...")
load_dotenv(override=True) # 强制重新加载

val = os.getenv('API_KEY_PRIVATE_KEY')
if val:
    print(f"✅ 成功读取到 API_KEY_PRIVATE_KEY: {val[:5]}******")
else:
    print(f"❌ 读取失败! os.getenv 返回 None")
