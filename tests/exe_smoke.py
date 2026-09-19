"""打包后 EXE 的验收测试：通过真实 HTTP 请求跑通全部核心功能，并检查数据落盘位置。

用法: python exe_smoke.py <base_url> <app_dir>
     base_url  例如 http://127.0.0.1:5000
     app_dir   exe 所在目录，用于检查 data/ 与 static/ 是否生成在正确位置
"""
import http.cookiejar
import io
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

from PIL import Image, ImageDraw, ImageFont

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
APP_DIR = sys.argv[2] if len(sys.argv) > 2 else None

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not ok else ""))


jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(path, data=None, content_type=None, method=None, timeout=30):
    url = BASE + path
    body = data
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def form_post(path, fields, files=None, timeout=30):
    """构造 urlencoded 或 multipart/form-data 请求。"""
    if not files:
        body = urllib.parse.urlencode(fields).encode()
        return request(path, body, "application/x-www-form-urlencoded", timeout=timeout)
    boundary = "----sw" + uuid.uuid4().hex
    buf = io.BytesIO()
    for key, value in fields.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        buf.write(str(value).encode("utf-8"))
        buf.write(b"\r\n")
    for key, (filename, content, mime) in files.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode())
        buf.write(f"Content-Type: {mime}\r\n\r\n".encode())
        buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return request(path, buf.getvalue(), f"multipart/form-data; boundary={boundary}", timeout=timeout)


def png_bytes(lines, size=(760, 360)):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 44)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 74
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


import urllib.parse  # noqa: E402  (供 form_post 使用)

print("=" * 62)
print("打包版 EXE 验收测试")
print("=" * 62)

print("\n[1] 首页与静态资源")
status, body, headers = request("/")
html = body.decode("utf-8", "replace")
check("首页返回 200", status == 200, status)
for marker, label in [
    ("我的 Switch 游戏库", "新版首页标题"),
    ("收藏库还是空的", "空状态引导"),
    ('class="hero-wave"', "Hero 贝塞尔波浪"),
    ("bg-curves", "背景曲线装饰层"),
    ("blob blob-a", "有机光斑"),
    ("chart-line", "贝塞尔折线图"),
    ('id="syncModal"', "时长同步弹窗"),
    ('data-open="syncModal"', "时长同步入口"),
    ("confirmModal", "确认弹窗"),
    ("lightbox", "图片灯箱"),
    ("themeToggle", "主题切换"),
]:
    check(f"页面含{label}", marker in html)

token_match = re.search(r'name="csrf-token" content="([^"]+)"', html)
TOKEN = token_match.group(1) if token_match else ""
check("CSRF token 已注入", bool(TOKEN))

for asset, needle in (("/static/style.css", "--accent-2"), ("/static/app.js", "confirmModal")):
    status, body, _ = request(asset)
    check(f"{asset} 可访问（{len(body)} 字节）", status == 200 and needle in body.decode("utf-8", "replace"))

print("\n[2] 新增游戏（含封面图上传）")
cover = png_bytes(["COVER"])
status, body, _ = form_post(
    "/add",
    {"_csrf": TOKEN, "title": "打包测试游戏", "game_type": "cartridge", "status": "playing",
     "rating": "5", "purchase_date": "2023-05-12", "purchase_price": "349", "playtime_hours": "42.5",
     "notes": "来自 EXE 验收测试的笔记"},
    {"cover": ("cover.png", cover, "image/png")},
)
detail = body.decode("utf-8", "replace")
check("新增后进入详情页", status == 200 and "打包测试游戏" in detail, status)
check("详情页显示价格", "¥349" in detail)
check("详情页显示快捷操作", "data-quick-playtime" in detail)

game_id = None
m = re.search(r'data-game-id="(\d+)"', detail)
if m:
    game_id = int(m.group(1))
check("拿到游戏 ID", game_id is not None)

print("\n[3] 缩略图生成")
thumbs_dir = os.path.join(APP_DIR, "static", "thumbs") if APP_DIR else None
status, body, headers = request(f"/thumb/uploads/placeholder.png")
check("不存在的缩略图返回 404", status == 404, status)
if APP_DIR:
    uploads = os.path.join(APP_DIR, "static", "uploads")
    files = os.listdir(uploads) if os.path.isdir(uploads) else []
    check("封面上传到 exe 同级 static/uploads", len(files) >= 1, files)
    if files:
        status, body, headers = request(f"/thumb/uploads/{files[0]}")
        check("缩略图接口返回 WebP", status == 200 and headers.get("Content-Type") == "image/webp",
              f"{status} {headers.get('Content-Type')}")
        check("缩略图体积小于原图", len(body) > 0 and len(body) < len(cover))
        check("缩略图已落盘缓存", os.path.isdir(thumbs_dir) and len(os.listdir(thumbs_dir)) >= 1)

print("\n[4] 照片上传与图库")
photo = png_bytes(["PHOTO"])
status, body, _ = form_post(f"/game/{game_id}/photo",
                            {"_csrf": TOKEN, "caption": "实机照片"},
                            {"photo": ("p.jpg", photo, "image/jpeg")})
check("照片上传成功", "已上传 1 张照片" in body.decode("utf-8", "replace"))
check("图库渲染出照片", body.decode("utf-8", "replace").count("photo-button") >= 1)

print("\n[5] 快捷记录与笔记")
status, body, _ = form_post(f"/game/{game_id}/quick", {"_csrf": TOKEN, "action": "playtime_inc", "amount": "5"})
check("快捷 +5 小时", status == 200 and b'"ok": true' in body.replace(b'"ok":true', b'"ok": true'), body[:120])
status, body, _ = form_post(f"/game/{game_id}/quick", {"_csrf": TOKEN, "action": "status", "value": "finished"})
check("快捷改状态", status == 200 and "已通关".encode() in body)
status, body, _ = form_post(f"/game/{game_id}/notes", {"_csrf": TOKEN, "notes": "笔记写入测试 ✔"})
check("笔记自动保存接口", status == 200 and b'"ok": true' in body.replace(b'"ok":true', b'"ok": true'))

print("\n[6] OCR 离线识别（验证模型已随包）")
shot = png_bytes(["Purchase Date 2023-05-12", "Total JPY 299.00"])
status, body, _ = form_post("/ocr", {"_csrf": TOKEN},
                            {"screenshot": ("shot.png", shot, "image/png")}, timeout=180)
try:
    data = __import__("json").loads(body.decode("utf-8"))
except Exception:
    data = {}
check("OCR 接口返回 200", status == 200, f"{status} {body[:200]}")
check("OCR 未报未安装错误", "未安装" not in str(data.get("error", "")), data.get("error"))
check("OCR 读到原文", bool(data.get("raw")), data.get("raw", "")[:80])
check("OCR 识别出日期", data.get("date") == "2023-05-12", data.get("date"))
check("OCR 识别出金额", data.get("price") == 299.0, data.get("price"))
if data.get("lines"):
    print("      识别原文:", " | ".join(data["lines"][:5]))

print("\n[7] 导出 CSV")
status, body, headers = request("/export")
csv_text = body.decode("utf-8-sig", "replace")
check("导出返回 200", status == 200, status)
check("导出含新增游戏", "打包测试游戏" in csv_text)
check("导出表头含状态/评分", "状态" in csv_text and "评分" in csv_text)

print("\n[8] 错误页与安全")
status, body, _ = request("/game/999999")
check("404 友好错误页", status == 404 and "没有找到这个页面" in body.decode("utf-8", "replace"))
status, body, _ = form_post("/add", {"title": "无令牌"})
check("缺少 CSRF 令牌被拒绝", status == 400, status)
status, body, _ = request("/thumb/../app.py")
check("路径穿越被拒绝", status == 404, status)

print("\n[9] 数据落盘位置（应在 exe 同级目录）")
if APP_DIR:
    expected = {
        "data 目录": os.path.join(APP_DIR, "data"),
        "数据库 app.db": os.path.join(APP_DIR, "data", "app.db"),
        "会话密钥 secret.key": os.path.join(APP_DIR, "data", "secret.key"),
        "static 目录": os.path.join(APP_DIR, "static"),
        "上传目录 uploads": os.path.join(APP_DIR, "static", "uploads"),
        "缩略图目录 thumbs": os.path.join(APP_DIR, "static", "thumbs"),
        "前端样式 style.css": os.path.join(APP_DIR, "static", "style.css"),
    }
    for label, path in expected.items():
        check(f"{label} 已生成", os.path.exists(path), path)
    db_size = os.path.getsize(os.path.join(APP_DIR, "data", "app.db")) if os.path.exists(os.path.join(APP_DIR, "data", "app.db")) else 0
    check("数据库已写入数据", db_size > 10000, f"{db_size} bytes")
else:
    print("  (未提供 app_dir，跳过)")

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for name in FAIL:
    print("  FAILED:", name)
print("=" * 62)
sys.exit(1 if FAIL else 0)
