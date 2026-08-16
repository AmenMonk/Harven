"""
Harven AI — 桌面启动入口（pywebview版）
替代PySide6，消除LGPL风险，BSD-3协议零法律风险

Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import sys
import os
import time
import subprocess
import socket

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(PROJECT_ROOT, 'static', 'icons', 'duck.ico')


def is_port_open(port):
    """检查端口是否已开放"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()
    return result == 0


def wait_for_port(port, timeout=30):
    """等待端口可用"""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_open(port):
            return True
        time.sleep(0.5)
    return False


def main():
    flask_process = None

    # 检查端口是否已被占用（哈文已在运行）
    if is_port_open(7000):
        print("Harven AI 已在运行，直接打开窗口...")
    else:
        # 启动Flask服务
        print("🚀 正在启动 Harven AI 哈文...")
        venv_python = os.path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
        if not os.path.exists(venv_python):
            venv_python = sys.executable

        flask_process = subprocess.Popen(
            [venv_python, 'web_app.py'],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # 等待服务就绪
        print("⏳ 等待服务启动...")
        if not wait_for_port(7000, timeout=30):
            print("❌ 服务启动超时，请检查配置")
            input("按回车退出...")
            sys.exit(1)

        print("✅ 服务已启动！正在打开窗口...")

    # 设置Windows应用图标
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('HarvenAI.HarvenAI.1')
    except Exception as _e:
        print(f"[Startup] 设置应用图标失败（不影响运行）: {_e}")

    # 打开pywebview窗口
    import webview

    webview.create_window(
        title='Harven AI 哈文',
        url='http://127.0.0.1:7000',  # ★ 2026-07-29: 5000→7000
        width=1280,
        height=800,
        min_size=(800, 600),
        text_select=True
    )

    icon_arg = ICON_PATH if os.path.exists(ICON_PATH) else None
    webview.start(icon=icon_arg)

    # 窗口关闭后终止Flask服务
    if flask_process and flask_process.poll() is None:
        flask_process.terminate()
        try:
            flask_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            flask_process.kill()
        print("🔇 Harven AI 服务已停止")


if __name__ == '__main__':
    main()
