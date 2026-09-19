"""最终线上验证：确认 http://127.0.0.1:5000 提供的是优化后的版本。"""
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:5000"
ok_all = True


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)


def report(label, good, extra=""):
    global ok_all
    ok_all = ok_all and good
    print(f"   {'OK ' if good else 'BAD'}  {label}{('  ' + extra) if extra else ''}")


print("=== 首页 ===")
status, html, _ = get("/")
print(f"GET /  ->  HTTP {status}, {len(html)} bytes")
report("新版首页标题", "我的 Switch 游戏库" in html)
report("空状态引导语", "收藏库还是空的" in html)
report("新看板（累计花费）", "累计花费" in html)
report("Hero 光效容器", "hero-glow" in html)
report("主题切换按钮", "themeToggle" in html)
report("自定义确认弹窗", "confirmModal" in html)
report("图片灯箱", "lightbox" in html)
report("CSRF meta 注入", 'name="csrf-token"' in html)
report("旧版模板标记已消失", "stat-card" not in html and "addModal" in html)

print("=== 导出 CSV ===")
status, csv_text, headers = get("/export")
ctype = headers.get("Content-Type", "")
report("导出返回 200", status == 200, f"HTTP {status}")
report("Content-Type 无重复 charset", ctype.count("charset") == 1, ctype)
report("UTF-8 BOM（Excel 友好）", csv_text.startswith("\ufeff"))
report("表头含新增列", "状态" in csv_text and "评分" in csv_text)
report("空库时不挂起", len(csv_text) < 500)

print("=== 静态资源 ===")
for path, needle in (("/static/style.css", '[data-theme="light"]'), ("/static/app.js", "confirmModal")):
    status, body, _ = get(path)
    report(f"{path} 可访问且为新版", status == 200 and needle in body, f"HTTP {status}, {len(body)} bytes")

print("=== 错误页 ===")
for path in ("/game/999", "/nope"):
    try:
        get(path)
        report(f"{path} 应返回错误", False)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        friendly = "没有找到这个页面" in body or "不在收藏库里" in body
        report(f"{path} 友好错误页", exc.code == 404 and friendly, f"HTTP {exc.code}")

print("=== 缩略图防护 ===")
try:
    get("/thumb/../app.py")
    report("路径穿越被拒绝", False)
except urllib.error.HTTPError as exc:
    report("路径穿越被拒绝", exc.code == 404, f"HTTP {exc.code}")

print()
print("总体结果:", "全部通过 OK" if ok_all else "存在失败项 FAIL")
raise SystemExit(0 if ok_all else 1)
