#!/usr/bin/env python3
"""
一键启动脚本 - 同时运行后端和 Bot
"""
import subprocess
import sys
import os

# 切换到脚本所在目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 安装依赖
print("📦 安装依赖...")
subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"])

# 启动后端
print("🚀 启动后端服务...")
backend_process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"],
    cwd="backend"
)

# 启动 Bot
print("🤖 启动 Discord Bot...")
bot_process = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd="bot"
)

print("✅ 全部启动完成！")
print("   后端地址: http://0.0.0.0:8001")

# 等待进程
try:
    backend_process.wait()
    bot_process.wait()
except KeyboardInterrupt:
    print("\n🛑 正在停止服务...")
    backend_process.terminate()
    bot_process.terminate()
