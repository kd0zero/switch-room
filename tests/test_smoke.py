"""端到端冒烟测试：使用 Flask test_client 覆盖全部路由与主要分支。"""
import base64
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (_HERE, os.path.dirname(_HERE)):   # 兼容放在 tests\ 或项目根目录
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import app as appmod  # noqa: E402
import ocr_engine  # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail and not condition else ""))


def make_image(path, lines, size=(720, 360)):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 70
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return path


def png_bytes(lines):
    path = make_image(os.path.join(appmod.DATA_DIR, "_tmp_test.png"), lines)
    with open(path, "rb") as fh:
        data = fh.read()
    os.remove(path)
    return data


# --------------------------------------------------------------------------- #
appmod.init_db()
appmod.app.config.update(TESTING=True)
client = appmod.app.test_client()

print("\n=== 1. 基础页面 ===")
index = client.get("/")
html = index.get_data(as_text=True)
check("GET / 返回 200", index.status_code == 200, index.status_code)
check("首页渲染标题", "我的 Switch 游戏库" in html)
check("空状态提示出现", "收藏库还是空的" in html)
m = re.search(r'name="csrf-token" content="([^"]+)"', html)
TOKEN = m.group(1) if m else ""
check("CSRF token 注入页面", bool(TOKEN))

css = client.get("/static/style.css")
js = client.get("/static/app.js")
check("样式表可访问", css.status_code == 200 and len(css.get_data()) > 10000)
check("脚本可访问", js.status_code == 200 and len(js.get_data()) > 10000)

print("\n=== 2. CSRF 防护 ===")
bad = client.post("/add", data={"title": "无令牌注入"})
check("缺少 CSRF 令牌被拒绝 (400)", bad.status_code == 400, bad.status_code)
check("400 走友好错误页", "安全校验失败" in bad.get_data(as_text=True))

bad2 = client.post("/add", data={"title": "错误令牌", "_csrf": "forged-token"})
check("伪造 CSRF 令牌被拒绝 (400)", bad2.status_code == 400)

print("\n=== 3. 新增游戏 ===")
cover_png = png_bytes(["cover"])
resp = client.post(
    "/add",
    data={
        "_csrf": TOKEN,
        "title": "塞尔达传说 王国之泪",
        "game_type": "cartridge",
        "status": "playing",
        "rating": "5",
        "purchase_date": "2023-05-12",
        "purchase_price": "349.00",
        "playtime_hours": "120.5",
        "notes": "第一次下到地底时完全沉浸了。",
        "cover": (io.BytesIO(cover_png), "cover.png"),
    },
    content_type="multipart/form-data",
    follow_redirects=True,
)
detail = resp.get_data(as_text=True)
check("新增后跳转详情页", resp.status_code == 200 and "塞尔达传说 王国之泪" in detail)
check("新增成功提示", "已加入收藏" in detail)
check("价格格式化显示", "¥349" in detail)
check("状态徽章渲染", "在玩" in detail)

with appmod.db() as conn:
    row = conn.execute("SELECT * FROM games WHERE title=?", ("塞尔达传说 王国之泪",)).fetchone()
GID = row["id"]
check("数据库写入完整", row["purchase_price"] == 349.0 and row["playtime_hours"] == 120.5)
check("封面图已保存", bool(row["cover_path"]) and os.path.isfile(os.path.join(appmod.STATIC_DIR, row["cover_path"])))

print("\n=== 4. 缩略图 ===")
thumb = client.get(f"/thumb/{row['cover_path']}")
check("缩略图路由 200", thumb.status_code == 200, thumb.status_code)
check("缩略图返回 WebP", thumb.headers.get("Content-Type") == "image/webp", thumb.headers.get("Content-Type"))
check("缩略图落盘缓存", os.path.isfile(os.path.join(appmod.THUMB_DIR, appmod._thumb_name(row["cover_path"]))))
check("缩略图体积远小于原图", len(thumb.get_data()) < len(cover_png))
check("路径穿越被拒绝", client.get("/thumb/../app.py").status_code == 404)

print("\n=== 5. 表单校验 ===")
r_bad_date = client.post(
    "/add",
    data={"_csrf": TOKEN, "title": "错误日期", "purchase_date": "2023-13-45"},
    follow_redirects=True,
)
check("非法日期被拦截并提示", "日期不存在" in r_bad_date.get_data(as_text=True))

r_neg = client.post(
    "/add",
    data={"_csrf": TOKEN, "title": "负数价格", "purchase_price": "-20"},
    follow_redirects=True,
)
check("负数价格被拦截并提示", "不能为负数" in r_neg.get_data(as_text=True))

r_no_title = client.post("/add", data={"_csrf": TOKEN, "title": "   "}, follow_redirects=True)
check("空标题被拒绝", "请先填写游戏名称" in r_no_title.get_data(as_text=True))

r_dup = client.post("/add", data={"_csrf": TOKEN, "title": "塞尔达传说 王国之泪"}, follow_redirects=True)
check("重复名称给出提醒", "已有同名游戏" in r_dup.get_data(as_text=True))

r_enum = client.post(
    "/add",
    data={"_csrf": TOKEN, "title": "枚举注入", "game_type": "<script>", "status": "'; DROP TABLE games;--"},
    follow_redirects=True,
)
with appmod.db() as conn:
    inj = conn.execute("SELECT * FROM games WHERE title=?", ("枚举注入",)).fetchone()
check("非法类型回退为默认值", inj["game_type"] == "cartridge", inj["game_type"])
check("非法状态回退为默认值", inj["status"] == "backlog", inj["status"])
check("注入未破坏数据表", inj is not None)

print("\n=== 6. 编辑与清空字段（旧版 bug 修复点）===")
upd = client.post(
    f"/game/{GID}/update",
    data={
        "_csrf": TOKEN,
        "title": "塞尔达传说 王国之泪",
        "game_type": "cartridge",
        "purchase_date": "",
        "purchase_price": "",
        "playtime_hours": "",
    },
    follow_redirects=True,
)
with appmod.db() as conn:
    after = conn.execute("SELECT * FROM games WHERE id=?", (GID,)).fetchone()
check("提交空值可清空价格（旧版会保留旧值）", not after["purchase_price"], after["purchase_price"])
check("提交空值可清空时长（旧版会保留旧值）", not after["playtime_hours"], after["playtime_hours"])
check("提交空值可清空日期", not after["purchase_date"], after["purchase_date"])
check("清空后仍有名称", after["title"] == "塞尔达传说 王国之泪")

client.post(
    f"/game/{GID}/update",
    data={
        "_csrf": TOKEN,
        "title": "塞尔达传说 王国之泪",
        "game_type": "cartridge",
        "purchase_date": "2023-05-12",
        "purchase_price": "349",
        "playtime_hours": "120.5",
    },
)
check("恢复数值成功", True)

print("\n=== 7. 快捷操作 JSON 接口 ===")
q1 = client.post(f"/game/{GID}/quick", data={"action": "playtime_inc", "amount": "1", "_csrf": TOKEN})
d1 = q1.get_json()
check("+1 小时接口", q1.status_code == 200 and d1["ok"] and round(d1["playtime_hours"], 1) == 121.5, d1)

q2 = client.post(f"/game/{GID}/quick", data={"action": "status", "value": "finished", "_csrf": TOKEN})
check("切换状态接口", q2.get_json()["status_label"] == "已通关")

q3 = client.post(f"/game/{GID}/quick", data={"action": "rating", "value": "4", "_csrf": TOKEN})
check("修改评分接口", q3.get_json()["rating"] == 4)

q4 = client.post(f"/game/{GID}/quick", data={"action": "hack", "_csrf": TOKEN})
check("未知操作被拒绝", q4.status_code == 400)

q5 = client.post(f"/game/{GID}/quick", data={"action": "status", "value": "nope", "_csrf": TOKEN})
check("非法状态被拒绝", q5.status_code == 400)

q6 = client.post("/game/999999/quick", data={"action": "rating", "value": "3", "_csrf": TOKEN})
check("不存在的游戏返回 404", q6.status_code == 404)

neg = client.post(f"/game/{GID}/quick", data={"action": "playtime_inc", "amount": "-100", "_csrf": TOKEN})
check("负时长被钳制", neg.get_json()["playtime_hours"] > 121.5, neg.get_json())

print("\n=== 8. 笔记自动保存 ===")
n1 = client.post(f"/game/{GID}/notes", data={"_csrf": TOKEN, "notes": "心流状态：完全沉浸 3 小时。"})
check("笔记保存返回 JSON", n1.status_code == 200 and n1.get_json()["ok"])
check("笔记保存时间戳返回", bool(n1.get_json().get("saved_at")))
n2 = client.post("/game/999999/notes", data={"_csrf": TOKEN, "notes": "x"})
check("不存在的游戏笔记返回 404", n2.status_code == 404)

print("\n=== 9. 照片上传与删除 ===")
photo = png_bytes(["photo"])
for i in range(2):
    client.post(
        f"/game/{GID}/photo",
        data={"_csrf": TOKEN, "caption": f"实机照 {i}", "photo": (io.BytesIO(photo), f"p{i}.jpg")},
        content_type="multipart/form-data",
    )
with appmod.db() as conn:
    photos = conn.execute("SELECT * FROM photos WHERE game_id=?", (GID,)).fetchall()
check("多张照片写入成功", len(photos) == 2, len(photos))

bad_photo = client.post(
    f"/game/{GID}/photo",
    data={"_csrf": TOKEN, "photo": (io.BytesIO(b"not an image"), "evil.png")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
check("假图片被拒绝", "不是有效的图片" in bad_photo.get_data(as_text=True))

bad_ext = client.post(
    f"/game/{GID}/photo",
    data={"_csrf": TOKEN, "photo": (io.BytesIO(b"x"), "shell.php")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
check("非法扩展名被拒绝", "仅支持 PNG" in bad_ext.get_data(as_text=True))

photo_path = photos[0]["path"]
check("照片文件存在", os.path.isfile(os.path.join(appmod.STATIC_DIR, photo_path)))
client.post(f"/photo/{photos[0]['id']}/delete", data={"_csrf": TOKEN})
check("删除照片后文件被清理", not os.path.isfile(os.path.join(appmod.STATIC_DIR, photo_path)))
with appmod.db() as conn:
    left = conn.execute("SELECT COUNT(*) c FROM photos WHERE game_id=?", (GID,)).fetchone()["c"]
check("删除照片后记录被清理", left == 1, left)
check("删除不存在的照片 404", client.post("/photo/999999/delete", data={"_csrf": TOKEN}).status_code == 404)

print("\n=== 10. OCR ===")
ocr_text = "订单详情\n购买日期 2023-05-12\n商品数量 2\n实付款 ¥299.00\n订单号 202305128888"
d, p = ocr_engine.extract_fields(ocr_text)
check("文本提取日期正确", d == "2023-05-12", d)
check("文本提取金额正确（不误取数量/订单号）", p == 299.0, p)

d2, p2 = ocr_engine.extract_fields("2023年5月12日 购买 合计 1,299.50 元")
check("中文日期与千分位金额", d2 == "2023-05-12" and p2 == 1299.5, (d2, p2))

d3, p3 = ocr_engine.extract_fields("20230512")
check("紧凑日期解析", d3 == "2023-05-12", d3)
check("两位数字日不被截断 (12-25)", ocr_engine.extract_fields("2023-12-25")[0] == "2023-12-25",
      ocr_engine.extract_fields("2023-12-25")[0])
check("个位月日解析 (2023-1-5)", ocr_engine.extract_fields("2023-1-5")[0] == "2023-01-05",
      ocr_engine.extract_fields("2023-1-5")[0])
check("两位年份解析 (23年12月31日)", ocr_engine.extract_fields("23年12月31日")[0] == "2023-12-31",
      ocr_engine.extract_fields("23年12月31日")[0])
check("点分隔日期解析", ocr_engine.extract_fields("2023.05.12")[0] == "2023-05-12")
check("斜杠日期解析", ocr_engine.extract_fields("2023/05/12")[0] == "2023-05-12")
check("非法月份不解析", ocr_engine.extract_fields("2023-13-45")[0] is None)
check("不存在的日期不解析 (2月30日)", ocr_engine.extract_fields("2023-02-30")[0] is None)
check("闰年 2月29日 有效", ocr_engine.extract_fields("2024-02-29")[0] == "2024-02-29")
check("平年 2月29日 无效", ocr_engine.extract_fields("2023-02-29")[0] is None)
check("空文本安全", ocr_engine.extract_fields("") == (None, None))
check("候选按可信度排序", ocr_engine.extract_price_candidates("数量 3 元 实付 ¥88.50")[0]["value"] == 88.5)

shot = png_bytes(["Purchase Date 2023-05-12", "Total JPY 299.00"])
r_ocr = client.post(
    "/ocr",
    data={"_csrf": TOKEN, "screenshot": (io.BytesIO(shot), "shot.png")},
    content_type="multipart/form-data",
)
od = r_ocr.get_json()
check("OCR 接口返回 200", r_ocr.status_code == 200, od)
check("OCR 接口带 ok 标记", od.get("ok") is True or "error" in od, od)
check("OCR 返回原文与候选结构", "raw" in od and "price_candidates" in od)
if od.get("raw"):
    check("真实图片 OCR 提取到日期", od.get("date") == "2023-05-12", od.get("date"))
    check("真实图片 OCR 提取到金额", od.get("price") == 299.0, od.get("price"))
    print("      OCR 原文：", " | ".join((od.get("lines") or [])[:6]))

r_ocr_bad = client.post(
    "/ocr",
    data={"_csrf": TOKEN, "screenshot": (io.BytesIO(b"xx"), "a.txt")},
    content_type="multipart/form-data",
)
check("非图片 OCR 被拒绝", r_ocr_bad.status_code == 400)

print("\n=== 11. 列表：搜索 / 筛选 / 排序 / 分页 ===")
for i in range(30):
    client.post(
        "/add",
        data={
            "_csrf": TOKEN,
            "title": f"批量游戏 {i:02d}",
            "game_type": "digital" if i % 2 else "cartridge",
            "status": ["backlog", "playing", "finished", "shelved"][i % 4],
            "purchase_price": str(100 + i),
            "playtime_hours": str(i),
            "purchase_date": f"2024-{(i % 12) + 1:02d}-01",
        },
    )

page1 = client.get("/").get_data(as_text=True)
check("分页每页 24 条", page1.count('class="card"') == 24, page1.count('class="card"'))
check("分页器出现", "第 1 /" in page1)
page2 = client.get("/?page=2").get_data(as_text=True)
check("第二页可访问", page2.count('class="card"') > 0)
check("越界页码被钳制", client.get("/?page=999").status_code == 200)

search = client.get("/?q=塞尔达").get_data(as_text=True)
check("搜索命中标题", "塞尔达传说" in search and "批量游戏" not in search)

search_notes = client.get("/?q=沉浸").get_data(as_text=True)
check("搜索命中笔记内容", "塞尔达传说" in search_notes)

filt = client.get("/?type=digital").get_data(as_text=True)
check("按类型筛选有效", "数字" in filt and "批量游戏 01" in filt)

fstatus = client.get("/?status=finished").get_data(as_text=True)
check("按状态筛选有效", "已通关" in fstatus)

for sort_key in ["title", "price_desc", "price_asc", "playtime_desc", "date_desc", "rating_desc", "created_asc"]:
    r = client.get(f"/?sort={sort_key}")
    check(f"排序 {sort_key} 正常", r.status_code == 200)

check("非法排序参数回退", client.get("/?sort=; DROP TABLE").status_code == 200)
check("非法类型参数回退", client.get("/?type=<script>").status_code == 200)

print("\n=== 12. 统计看板 ===")
stats = appmod.compute_stats()
# 库里此时应有 35 条：塞尔达 + 错误日期 + 负数价格 + 重复塞尔达 + 枚举注入 + 30 条批量
check("总数统计正确", stats["total"] == 35, stats["total"])
check("卡带数统计", stats["cartridges"] == 20, stats["cartridges"])
check("数字版统计", stats["digital"] == 15, stats["digital"])
check("卡带+数字=总数", stats["cartridges"] + stats["digital"] == stats["total"])
check("花费汇总为正", stats["spent"] > 0, stats["spent"])
check("平均单价合理", stats["avg_price"] > 0)
check("近六月序列长度 6", len(stats["monthly"]) == 6)
check("通关率 0-100", 0 <= stats["completion"] <= 100)

dash = client.get("/").get_data(as_text=True)
check("看板渲染累计花费", "累计花费" in dash)
check("看板渲染贝塞尔折线图",
      "data-chart" in dash and 'class="chart-line"' in dash and "chartGradient" in dash)
check("背景贝塞尔曲线层存在", 'class="bg-curves"' in dash and "curve curve-a" in dash)
check("Hero 波浪曲线存在", 'class="hero-wave"' in dash)

print("\n=== 13. 导出 / 导入 ===")
exp = client.get("/export")
csv_text = exp.get_data(as_text=True)
check("导出返回 CSV", exp.status_code == 200 and "text/csv" in exp.headers["Content-Type"])
check("导出带 BOM（Excel 友好）", exp.get_data().startswith(b"\xef\xbb\xbf"))
check("导出表头完整", "标题" in csv_text and "游玩时长(小时)" in csv_text and "状态" in csv_text)
rows = list(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))))
check("导出行数与库一致", len(rows) == 35, len(rows))

imp_csv = ("标题,类型,状态,评分,购买日期,价格,游玩时长(小时),备注\n"
           "导入测试游戏,数字版,在玩,5,2024-01-15,199.5,12.5,来自 CSV\n"
           "塞尔达传说 王国之泪,实体卡带,已通关,5,2023-05-12,349,120,重复应跳过\n")
r_imp = client.post(
    "/import",
    data={"_csrf": TOKEN, "csv": (io.BytesIO(imp_csv.encode("utf-8-sig")), "backup.csv")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
imp_html = r_imp.get_data(as_text=True)
check("导入新增 1 条", "新增 1 条" in imp_html, [l for l in imp_html.split("\n") if "导入完成" in l][:1])
check("重名被跳过", "跳过 1 条" in imp_html)
with appmod.db() as conn:
    imported = conn.execute("SELECT * FROM games WHERE title=?", ("导入测试游戏",)).fetchone()
check("导入字段解析正确", imported["purchase_price"] == 199.5 and imported["status"] == "playing"
      and imported["rating"] == 5 and imported["game_type"] == "digital", dict(imported) if imported else None)

gbk_csv = "标题,类型,价格\nGBK 编码游戏,实体卡带,88\n"
r_gbk = client.post(
    "/import",
    data={"_csrf": TOKEN, "csv": (io.BytesIO(gbk_csv.encode("gbk")), "gbk.csv")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
check("兼容 GBK 编码 CSV", "新增 1 条" in r_gbk.get_data(as_text=True))

r_nocsv = client.post("/import", data={"_csrf": TOKEN}, follow_redirects=True)
check("未选择文件时提示", "请选择要导入的 CSV" in r_nocsv.get_data(as_text=True))

print("\n=== 14. 删除级联 ===")
with appmod.db() as conn:
    target = conn.execute("SELECT * FROM games WHERE title=?", ("批量游戏 00",)).fetchone()
client.post(
    f"/game/{target['id']}/photo",
    data={"_csrf": TOKEN, "photo": (io.BytesIO(photo), "x.png")},
    content_type="multipart/form-data",
)
with appmod.db() as conn:
    with_photo = conn.execute("SELECT path FROM photos WHERE game_id=?", (target["id"],)).fetchall()
paths = [r["path"] for r in with_photo]
if target["cover_path"]:
    paths.append(target["cover_path"])
client.post(f"/game/{target['id']}/delete", data={"_csrf": TOKEN}, follow_redirects=True)
with appmod.db() as conn:
    gone = conn.execute("SELECT * FROM games WHERE id=?", (target["id"],)).fetchone()
    orphan = conn.execute("SELECT COUNT(*) c FROM photos WHERE game_id=?", (target["id"],)).fetchone()["c"]
check("游戏记录已删除", gone is None)
check("关联照片记录级联删除", orphan == 0)
check("磁盘文件已清理", all(not os.path.isfile(os.path.join(appmod.STATIC_DIR, p)) for p in paths))
check("删除不存在的游戏返回 404", client.post("/game/999999/delete", data={"_csrf": TOKEN}).status_code == 404)

print("\n=== 15. 错误页与边界 ===")
nf = client.get("/game/999999")
check("404 友好页面", nf.status_code == 404 and "没有找到这个页面" in nf.get_data(as_text=True))
nf2 = client.get("/no-such-route")
check("未知路由 404", nf2.status_code == 404)

old_limit = appmod.app.config["MAX_CONTENT_LENGTH"]
appmod.app.config["MAX_CONTENT_LENGTH"] = 512
big = client.post(
    "/add",
    data={"_csrf": TOKEN, "title": "超大文件", "cover": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 4096), "big.png")},
    content_type="multipart/form-data",
)
appmod.app.config["MAX_CONTENT_LENGTH"] = old_limit
check("超出请求体积返回 413", big.status_code == 413, big.status_code)
check("413 使用友好页面", "文件太大了" in big.get_data(as_text=True))

check("详情页 404 文案", "不在收藏库里" in client.get("/game/999999").get_data(as_text=True))

print("\n=== 16. 数据库迁移 ===")
legacy_path = os.path.join(appmod.DATA_DIR, "legacy_test.db")
if os.path.isfile(legacy_path):
    os.remove(legacy_path)
old = sqlite3.connect(legacy_path)
old.execute("""CREATE TABLE games (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
    game_type TEXT NOT NULL DEFAULT 'cartridge', purchase_date TEXT,
    purchase_price REAL, playtime_hours REAL DEFAULT 0, cover_path TEXT,
    notes TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime')))""")
old.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL, "
            "path TEXT NOT NULL, caption TEXT DEFAULT '', created_at TEXT)")
old.execute("INSERT INTO games (title, purchase_price) VALUES ('老库游戏', 199.0)")
old.commit()
old.close()

orig_db = appmod.DB_PATH
appmod.DB_PATH = legacy_path
appmod.init_db()
with appmod.db() as conn:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(games)")}
    migrated = conn.execute("SELECT * FROM games WHERE title='老库游戏'").fetchone()
check("旧库自动补 status 列", "status" in cols)
check("旧库自动补 rating 列", "rating" in cols)
check("旧库自动补 updated_at 列", "updated_at" in cols)
check("旧库数据保留", migrated["purchase_price"] == 199.0 and migrated["status"] == "backlog")
appmod.DB_PATH = orig_db
os.remove(legacy_path)

print("\n=== 17. 静态资源与模板完整性 ===")
for name in ["base.html", "index.html", "game_detail.html", "error.html"]:
    check(f"模板存在 {name}", os.path.isfile(os.path.join(appmod.BASE_DIR, "templates", name)))
for asset in ["style.css", "app.js"]:
    check(f"静态资源存在 {asset}", os.path.isfile(os.path.join(appmod.STATIC_DIR, asset)))

detail_html = client.get(f"/game/{GID}").get_data(as_text=True)
for marker in ["data-game-id", "data-quick-rating", "data-quick-status", "data-quick-playtime",
               "id=\"notes\"", "id=\"lightbox\"", "data-confirm", "记录时长", "每小时成本"]:
    check(f"详情页包含 {marker}", marker.replace('\\"', '"') in detail_html)

index_html = client.get("/").get_data(as_text=True)
for marker in ["data-open=\"addModal\"", "id=\"addModal\"", "id=\"importModal\"", "themeToggle",
               "viewToggle", "searchInput", "chip-status", "chart-line",
               "bg-curves", "hero-wave", "blob blob-a", "confirmModal"]:
    check(f"首页包含 {marker}", marker.replace('\\"', '"') in index_html)

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：")
    for name in FAIL:
        print("  -", name)
print("=" * 62)
sys.exit(1 if FAIL else 0)
