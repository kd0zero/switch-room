"""主题色板静态审计：变量一致性 + WCAG 对比度 + 括号配平。

不依赖浏览器，直接解析 style.css，能客观发现「某个主题漏定义变量」
或「文字对比度不达标」这类肉眼容易漏掉的问题。
"""
import re
import sys
import os

CSS = sys.argv[1] if len(sys.argv) > 1 else "style.css"
with open(CSS, encoding="utf-8") as fh:
    text = fh.read()

FAIL = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def block(selector):
    """取出某个选择器的 { ... } 内容（处理一层嵌套）。"""
    idx = text.find(selector)
    if idx < 0:
        return ""
    start = text.find("{", idx)
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return ""


def vars_of(sel):
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block(sel)))


def rel_lum(hex_color):
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    l1, l2 = rel_lum(fg), rel_lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


print("=" * 64)
print("主题色板审计")
print("=" * 64)

print("\n[1] 结构健康度")
check("花括号配平", text.count("{") == text.count("}"),
      f"{text.count('{')} 个 {{ vs {text.count('}')} 个 }}")
check("包含明亮主题定义", 'html[data-theme="light"]' in text)
check("包含黑夜主题定义", 'html[data-theme="dark"]' in text)
check("声明了贝塞尔缓动变量", "--ease-spring" in text and "cubic-bezier" in text)
check("无遗留旧变量 --panel", "--panel:" not in text)
check("无遗留旧变量 --bg-soft", "--bg-soft:" not in text or "--bg-2:" in text)

light = vars_of('html[data-theme="light"]')
dark = vars_of('html[data-theme="dark"]')

print(f"\n[2] 变量一致性（明亮 {len(light)} 个 / 黑夜 {len(dark)} 个）")
only_light = sorted(set(light) - set(dark))
only_dark = sorted(set(dark) - set(light))
check("两个主题变量名完全对齐", not only_light and not only_dark,
      f"仅明亮有={only_light} 仅黑夜有={only_dark}")

print("\n[3] 关键变量齐备")
required = ["--bg", "--surface", "--surface-2", "--line", "--ink", "--text", "--text-2",
            "--muted", "--accent", "--accent-2", "--accent-ink", "--accent-text",
            "--accent-soft", "--accent-line", "--ok", "--warn", "--danger", "--gold",
            "--shadow", "--shadow-lift", "--ring", "--glass", "--curve-1", "--blob-a",
            # 任天堂经典配色
            "--n-red", "--n-red-ink", "--n-blue", "--n-blue-ink", "--n-yellow", "--n-yellow-ink",
            "--n-green", "--n-green-ink", "--n-pink", "--n-pink-ink", "--n-indigo", "--n-indigo-ink",
            # 磨砂玻璃
            "--glass-blur", "--glass-btn", "--glass-btn-hover", "--glass-border",
            "--glass-top", "--glass-shadow", "--glass-sheen"]
for theme_name, table in (("明亮", light), ("黑夜", dark)):
    missing = [v for v in required if v not in table]
    check(f"{theme_name}模式变量齐备", not missing, f"缺少 {missing}")

print("\n[4] WCAG 对比度（正文要求 ≥ 4.5:1，大字/图标 ≥ 3:1）")
pairs = [
    ("正文 text / surface", "text", "surface", 4.5),
    ("正文 text / bg", "text", "bg", 4.5),
    ("次要 muted / surface", "muted", "surface", 4.5),
    ("次要 muted / bg", "muted", "bg", 4.5),
    ("标题 ink / surface", "ink", "surface", 4.5),
    ("三级 text-2 / surface", "text-2", "surface", 4.5),
    ("强调 accent-text / surface", "accent-text", "surface", 4.5),
    ("强调 accent-text / bg", "accent-text", "bg", 4.5),
    ("主按钮字 btn-ink / btn-from", "btn-ink", "btn-from", 4.5),
    ("主按钮字 btn-ink / btn-to", "btn-ink", "btn-to", 4.5),
    ("危险色 danger / surface", "danger", "surface", 4.5),
    ("成功色 ok / surface", "ok", "surface", 4.5),
    ("警示色 warn / surface", "warn", "surface", 4.5),
    ("星级 gold / surface", "gold", "surface", 3.0),
    # 任天堂配色的文字版本
    ("任天堂红 n-red-ink / surface", "n-red-ink", "surface", 4.5),
    ("霓虹蓝 n-blue-ink / surface", "n-blue-ink", "surface", 4.5),
    ("霓虹黄 n-yellow-ink / surface", "n-yellow-ink", "surface", 4.5),
    ("霓虹绿 n-green-ink / surface", "n-green-ink", "surface", 4.5),
    ("霓虹粉 n-pink-ink / surface", "n-pink-ink", "surface", 4.5),
    ("靛蓝 n-indigo-ink / surface", "n-indigo-ink", "surface", 4.5),
]
for theme_name, table in (("明亮", light), ("黑夜", dark)):
    print(f"  -- {theme_name}模式 --")
    for label, fg_key, bg_key, need in pairs:
        fg, bg = table.get("--" + fg_key, ""), table.get("--" + bg_key, "")
        if not fg.startswith("#") or not bg.startswith("#"):
            check(f"{label}", False, f"值不是纯色：{fg!r} / {bg!r}")
            continue
        ratio = contrast(fg, bg)
        check(f"{label} = {ratio:.2f}:1", ratio >= need, f"需要 ≥ {need}")

print("\n[5] 贝塞尔曲线使用情况")
# 缓动统一走令牌，所以统计「令牌定义数」与「令牌引用数」两件事
ease_defs = re.findall(r"--ease-[\w-]+\s*:\s*cubic-bezier\([^)]+\)", text)
ease_uses = re.findall(r"var\(--ease-[\w-]+\)", text)
check("定义贝塞尔缓动令牌 ≥5 个", len(ease_defs) >= 5, f"找到 {len(ease_defs)} 个")
check("缓动令牌被广泛引用 ≥30 处", len(ease_uses) >= 30, f"找到 {len(ease_uses)} 处")
check("全部缓动都用 cubic-bezier",
      "transition-timing-function: ease" not in text,
      "存在未使用贝塞尔的缓动")

# 有机圆角 = 含百分比的 border-radius（椭圆半径），区别于普通等圆角
organic = re.findall(r"border-radius:[^;{}]*%[^;{}]*", text)
check("有机（椭圆）圆角 ≥5 处", len(organic) >= 5, f"找到 {len(organic)} 处")
app_js = open(os.path.join(os.path.dirname(CSS), "app.js"), encoding="utf-8").read()
check("折线图用三次贝塞尔样条", "bezierPath" in app_js and "' C '" in app_js)

print("\n[5b] 磨砂玻璃按钮")
btn_block = block(".btn {")
check(".btn 使用 backdrop-filter 磨砂", "backdrop-filter" in btn_block and "var(--glass-blur)" in btn_block)
check(".btn 有内高光", "inset 0 1px 0 var(--glass-top)" in btn_block)
check(".btn 使用玻璃底色变量", "var(--glass-btn)" in btn_block)
check("悬停有玻璃高亮过渡", "var(--glass-btn-hover)" in text)
primary_block = block(".btn-primary {")
check("主按钮同样为玻璃质感", "backdrop-filter" in btn_block or "glass" in primary_block)
check("磨砂降级方案存在", "@supports not" in text)
check("丝滑：主题切换交叉淡入", "theme-switching" in text and "theme-switching" in app_js)
check("丝滑：卡片入场错峰", "riseIn" in text and "initReveal" in app_js)
check("丝滑：数字滚动", "initCounters" in app_js and "data-count" in app_js)

print("\n[6] 模板中的曲线装饰")
tpl_dir = os.path.join(os.path.dirname(os.path.dirname(CSS)), "templates")
base_html = open(os.path.join(tpl_dir, "base.html"), encoding="utf-8").read()
index_html = open(os.path.join(tpl_dir, "index.html"), encoding="utf-8").read()


def svg_paths(html):
    """统计含三次贝塞尔命令 C 的 SVG path 数量。"""
    return [d for d in re.findall(r'd="([^"]+)"', html) if re.search(r"[Cc][\s\d.-]", d)]


check("base 含背景贝塞尔曲线层",
      'class="bg-curves"' in base_html and len(svg_paths(base_html)) >= 3,
      f"曲线路径 {len(svg_paths(base_html))} 条")
check("base 含有机光斑 blob",
      'class="blob blob-a"' in base_html and 'class="blob blob-b"' in base_html)
check("首页含 Hero 波浪曲线",
      'class="hero-wave"' in index_html and len(svg_paths(index_html)) >= 2,
      f"曲线路径 {len(svg_paths(index_html))} 条")
check("首页图表为 SVG 样条",
      'class="chart-line"' in index_html and "chartGradient" in index_html)
check("旧的柱状图结构已移除", "chart-col" not in index_html and "chart-fill" not in index_html)

print("\n" + "=" * 64)
print("审计通过" if not FAIL else f"发现 {len(FAIL)} 项问题")
for name in FAIL:
    print("  -", name)
print("=" * 64)
sys.exit(1 if FAIL else 0)
