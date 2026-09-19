"""
Switch 游戏收藏管家 —— 桌面版启动器（PyInstaller 打包入口）

行为
----
1. 初始化数据库（首次运行自动建库）。
2. 若 5000 端口已被本程序占用，则直接把浏览器指过去，不重复启动。
3. 端口被别的程序占用时，自动往后找一个空闲端口。
4. 后台预热 OCR 模型，并自动打开浏览器。

关闭这个黑色控制台窗口即可退出程序；数据全部保存在同级的 data 目录。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import app as appmod

HOST = "127.0.0.1"
PREFERRED_PORT = 5000
PORT_SCAN_LIMIT = 20
LAUNCH_DELAY = 1.5          # 等待服务起来再开浏览器
APP_TITLE = "Switch 游戏收藏管家"
NO_BROWSER = os.environ.get("SW_NO_BROWSER") == "1"   # 供自动化测试 / 无界面场景使用


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def set_console_title(title: str = APP_TITLE) -> None:
    if os.name == "nt":
        try:
            os.system(f"title {title}")
        except Exception:
            pass


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def looks_like_our_app(port: int) -> bool:
    """探测该端口上跑的是不是本程序（用于避免重复启动）。"""
    url = f"http://{HOST}:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            head = resp.read(4096).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return "csrf-token" in head and APP_TITLE in head


def choose_port(base_port: int) -> int:
    if port_is_free(base_port):
        return base_port
    for port in range(base_port + 1, base_port + 1 + PORT_SCAN_LIMIT):
        if port_is_free(port):
            return port
    raise SystemExit(f"找不到可用端口，请关闭占用 {base_port}-{base_port + PORT_SCAN_LIMIT} 的程序后重试。")


def preferred_port() -> int:
    """默认 5000；可用环境变量 SW_PORT 指定其它端口。"""
    raw = (os.environ.get("SW_PORT") or "").strip()
    if raw.isdigit() and 1 <= int(raw) <= 65535:
        return int(raw)
    return PREFERRED_PORT


def open_browser_later(url: str) -> None:
    def _open():
        time.sleep(LAUNCH_DELAY)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open, name="open-browser", daemon=True).start()


def banner(url: str, port: int) -> None:
    data_dir = appmod.DATA_DIR
    ocr_state = "已启用（离线识别）" if appmod.ocr_engine.available() else "未安装（纯手动录入）"
    line = "=" * 62
    print(line)
    print(f"  {APP_TITLE}  ·  桌面版")
    print(line)
    print(f"  访问地址 : {url}")
    print(f"  数据目录 : {data_dir}")
    print(f"  OCR 引擎 : {ocr_state}")
    print(f"  运行模式 : {'打包版 (exe)' if appmod._is_frozen() else '源码版 (python)'}")
    print(line)
    print("  浏览器会自动打开；若没有，请手动复制上面的访问地址。")
    print("  关闭本窗口即可退出程序（直接点右上角的 × 也行）。")
    print(line)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    set_console_title()
    appmod.init_db()
    appmod.ocr_engine.warmup_async()

    base_port = preferred_port()

    # 已经开着？直接把浏览器指过去
    if looks_like_our_app(base_port):
        url = f"http://{HOST}:{base_port}"
        print(f"{APP_TITLE} 已经在运行，正在打开浏览器…\n{url}")
        if not NO_BROWSER:
            webbrowser.open(url)
        time.sleep(1.5)
        return 0

    try:
        port = choose_port(base_port)
    except SystemExit as exc:
        print(exc)
        input("按回车键退出…")
        return 1

    if port != base_port:
        print(f"提示：端口 {base_port} 被占用，已改用 {port}。\n")

    url = f"http://{HOST}:{port}"
    banner(url, port)
    if NO_BROWSER:
        print("  （已设置 SW_NO_BROWSER=1，不自动打开浏览器）")
        print("=" * 62)
    else:
        open_browser_later(url)

    try:
        appmod.app.run(host=HOST, port=port, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"\n启动失败：{exc}")
        input("按回车键退出…")
        return 1

    print("\n已退出，数据已保存在:", appmod.DATA_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
