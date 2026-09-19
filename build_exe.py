"""
Switch 游戏收藏管家 —— 桌面版（EXE）打包脚本

用法:
    .venv\\Scripts\\python.exe build_exe.py              # 输出到项目内 SwitchGameManager/
    .venv\\Scripts\\python.exe build_exe.py --out D:\\build
    .venv\\Scripts\\python.exe build_exe.py --keep-work  # 保留 PyInstaller 中间产物

产物是一个免安装的绿色目录：
    SwitchGameManager/
        SwitchGameManager.exe     双击运行
        _internal/                依赖库与前端资源
        使用说明.txt / 备份数据.bat
首次运行时会在同目录自动生成 data\\（数据库）与 static\\（上传的图片、缩略图）。

为什么用 Python 而不是 PowerShell 驱动：PyInstaller 会把 INFO 日志写到 stderr，
在 PowerShell 里容易被当成错误记录而中断脚本；Python 的 subprocess 没有这个问题。
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "SwitchGameManager"
ENTRY = os.path.join(ROOT, "desktop.py")
ICON = os.path.join(ROOT, "icon.ico")
TEMPLATES_DIR = os.path.join(ROOT, "templates")
STATIC_DIR = os.path.join(ROOT, "static")

# 只打包前端资源，绝不要把用户上传的图片（static/uploads、static/thumbs）打进软件
STATIC_ASSETS = ("style.css", "app.js")

# 运行时用不到、却会被 PyInstaller 一起打进来的大文件（相对 _internal 的 glob 模式）
TRIM_PATTERNS = [
    "cv2/opencv_videoio_ffmpeg*.dll",   # 视频编解码，OCR 只处理静态图片（约 29MB）
    "PIL/_avif*.pyd",                   # AVIF 解码器，本程序只收 png/jpg/webp/gif/bmp（约 7MB）
]

EXCLUDES = [
    "tkinter", "matplotlib", "scipy", "pandas",
    "IPython", "pytest", "PyQt5", "PySide2", "PySide6",
]

README_TXT = """Switch 游戏收藏管家 · 桌面版
============================================================

一、怎么启动
------------------------------------------------------------
  双击 SwitchGameManager.exe 即可，程序会自动打开浏览器
  （默认地址 http://127.0.0.1:5000）。

  · 会弹出一个黑色控制台窗口，那是程序的运行窗口，不要关它。
  · 浏览器标签页关掉没关系，重新打开控制台里显示的地址就能继续用。
  · 关闭控制台窗口（或按 Ctrl+C）= 退出程序。


二、数据存在哪
------------------------------------------------------------
  全部数据都在本文件夹内，不写注册表、不写系统目录：

    data\\app.db          游戏库数据库（名称/类型/价格/时长/评分/笔记）
    data\\secret.key      本机会话密钥（自动生成）
    static\\uploads\\      你上传的封面与照片原图
    static\\thumbs\\       自动生成的缩略图缓存（删掉会自动重建）

  程序不联网、不上传任何信息，购买截图 OCR 也是本地离线识别。


三、备份与迁移
------------------------------------------------------------
  · 复制整个文件夹 = 完整备份，换电脑、换目录直接就能用。
  · 双击「备份数据.bat」，会在 备份\\ 目录里生成带日期的 zip 压缩包。
  · 网页右上角「导出」可以导出 CSV，用 Excel 直接打开不会乱码。


四、常见问题
------------------------------------------------------------
  Q: 提示端口被占用？
  A: 程序会自动改用 5001、5002…，请看控制台里打印的访问地址。

  Q: 想固定端口？
  A: 启动前设置环境变量 SW_PORT，例如：
         set SW_PORT=8888
         SwitchGameManager.exe

  Q: 不小心双击了两次图标？
  A: 不会启动第二个实例，会把浏览器直接指向已经运行的那个。

  Q: Windows 或杀毒软件报警？
  A: PyInstaller 打包的程序常被误报（因为要把自己解包运行），
     添加信任即可。若仍不放心，可用源码方式运行 python app.py。

  Q: 能在手机上用吗？
  A: 默认只监听 127.0.0.1（仅本机）。如需局域网访问，需要改源码里的
     host 参数，注意此时同网段的人都能访问你的收藏。


五、文件夹里都有什么
------------------------------------------------------------
  SwitchGameManager.exe    主程序（双击这个）
  _internal\\               程序依赖库与前端资源，请勿删除
  data\\                    你的数据（最重要，注意备份）
  static\\                  上传的图片与缩略图
  备份数据.bat             一键备份 data 与上传的图片
  使用说明.txt             本文件


六、技术信息
------------------------------------------------------------
  技术栈   : Flask + SQLite + Pillow + RapidOCR(ONNX，离线)
  打包方式 : PyInstaller onedir（免安装绿色版）
  运行要求 : Windows 10 / 11 64 位，无需安装 Python
  体积说明 : 内含 OCR 模型与推理引擎，因此文件夹较大属正常现象
"""

BACKUP_BAT = r"""@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist "备份" mkdir "备份"
if not exist "static\uploads" mkdir "static\uploads"

echo 正在备份数据，请稍候...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'; $dest = Join-Path '备份' ('Switch收藏备份_' + $stamp + '.zip'); Compress-Archive -Path 'data','static\uploads' -DestinationPath $dest -Force; $mb = [math]::Round((Get-Item $dest).Length / 1MB, 2); Write-Host ''; Write-Host ('备份完成: ' + $dest + '  (' + $mb + ' MB)')"

echo.
pause
"""


def log(message: str) -> None:
    print(message, flush=True)


def stage_assets(work_dir: str) -> tuple[str, str]:
    """把模板与前端资源复制到干净的暂存目录，避免把用户上传的图片打进软件包。"""
    stage = os.path.join(work_dir, "_stage")
    tpl_dst = os.path.join(stage, "templates")
    sta_dst = os.path.join(stage, "static")
    os.makedirs(tpl_dst, exist_ok=True)
    os.makedirs(sta_dst, exist_ok=True)

    count = 0
    for name in sorted(os.listdir(TEMPLATES_DIR)):
        if name.endswith(".html"):
            shutil.copy2(os.path.join(TEMPLATES_DIR, name), os.path.join(tpl_dst, name))
            count += 1
    for name in STATIC_ASSETS:
        src = os.path.join(STATIC_DIR, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(sta_dst, name))
            count += 1
    log(f"=== 暂存前端资源 ({count} 个文件) ===")
    return tpl_dst, sta_dst


def clean(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise SystemExit(
            f"无法删除 {path}：{exc}\n"
            f"如果 {APP_NAME}.exe 正在运行，请先关闭它再重新打包。"
        )


def pyinstaller_args(out_dir: str, work_dir: str, tpl_dir: str, sta_dir: str) -> list[str]:
    # PyInstaller 6.x 的 --add-data 用 SOURCE:DEST（Windows 上不再用分号）
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--console",
        "--name", APP_NAME,
        "--distpath", out_dir,
        "--workpath", work_dir,
        "--specpath", work_dir,
        "--add-data=" + tpl_dir + ":templates",
        "--add-data=" + sta_dir + ":static",
        # OCR 模型与推理运行时必须整体收集，否则识别会在运行时静默失败
        "--collect-all", "rapidocr_onnxruntime",
        "--collect-all", "onnxruntime",
    ]
    if os.path.isfile(ICON):
        args += ["--icon", ICON]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append(ENTRY)
    return args


def trim(app_dir: str) -> int:
    """删除明确用不到的大文件，返回释放的字节数。"""
    log("=== 裁剪无用文件 ===")
    freed = 0
    internal = os.path.join(app_dir, "_internal")
    for pattern in TRIM_PATTERNS:
        hits = glob.glob(os.path.join(internal, pattern))
        if not hits:
            continue
        for path in hits:
            try:
                size = os.path.getsize(path)
                os.remove(path)
                freed += size
                log(f"  已删除 {os.path.relpath(path, app_dir)}  ({size / 1024 / 1024:.1f} MB)")
            except OSError as exc:
                log(f"  跳过 {path}: {exc}")
    if not freed:
        log("  没有需要裁剪的文件")
    return freed


def write_extras(app_dir: str) -> None:
    log("=== 写入附加文件 ===")
    with open(os.path.join(app_dir, "使用说明.txt"), "w", encoding="utf-8-sig") as fh:
        fh.write(README_TXT)
    with open(os.path.join(app_dir, "备份数据.bat"), "w", encoding="utf-8") as fh:
        fh.write(BACKUP_BAT)
    log("  + 使用说明.txt\n  + 备份数据.bat")


def report(app_dir: str, freed: int) -> None:
    total = count = 0
    for dirpath, _dirnames, filenames in os.walk(app_dir):
        for name in filenames:
            total += os.path.getsize(os.path.join(dirpath, name))
            count += 1
    log("")
    log("=" * 62)
    log("  打包完成")
    log("=" * 62)
    log(f"  输出目录 : {app_dir}")
    log(f"  主程序   : {os.path.join(app_dir, APP_NAME + '.exe')}")
    log(f"  文件数量 : {count}")
    log(f"  总体积   : {total / 1024 / 1024:.1f} MB"
        + (f"  (裁剪释放 {freed / 1024 / 1024:.1f} MB)" if freed else ""))
    log("=" * 62)


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 Switch 游戏收藏管家桌面版")
    parser.add_argument("--out", default=ROOT, help="输出目录（默认项目根目录）")
    parser.add_argument("--keep-work", action="store_true", help="保留 PyInstaller 中间产物")
    opts = parser.parse_args()

    out_dir = os.path.abspath(opts.out)
    work_dir = os.path.join(out_dir, "build")
    app_dir = os.path.join(out_dir, APP_NAME)
    os.makedirs(out_dir, exist_ok=True)

    # 缓存目录固定在工作区内，避免写入用户 AppData
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", os.path.join(work_dir, "picache"))

    if not os.path.isfile(ENTRY):
        raise SystemExit(f"找不到打包入口：{ENTRY}")

    log("=== 清理旧产物 ===")
    clean(app_dir)
    clean(work_dir)

    tpl_dir, sta_dir = stage_assets(work_dir)

    log(f"=== 开始打包 ({APP_NAME}) ===")
    # 不捕获输出，让 PyInstaller 的日志直接流到当前终端
    result = subprocess.run(pyinstaller_args(out_dir, work_dir, tpl_dir, sta_dir))
    if result.returncode != 0:
        log(f"PyInstaller 打包失败，退出码 {result.returncode}")
        return result.returncode

    exe = os.path.join(app_dir, APP_NAME + ".exe")
    if not os.path.isfile(exe):
        log(f"未找到产物：{exe}")
        return 1

    freed = trim(app_dir)
    write_extras(app_dir)
    report(app_dir, freed)

    if not opts.keep_work:
        clean(work_dir)
        log("  （已清理中间产物，如需保留请加 --keep-work）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
