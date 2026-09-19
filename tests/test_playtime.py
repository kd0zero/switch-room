"""游玩时长同步测试：OCR 解析/匹配 + 任天堂 API 客户端 + 相关路由。

任天堂接口无法在没有真实令牌的情况下联网验证，因此这里：
  · 用固定的响应样本验证「递归解析 + 时长聚合 + 标题映射」的逻辑
  · 用打桩（monkeypatch）验证鉴权流程的请求构造与错误处理
"""
import io
import json
import os
import sys
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (_HERE, os.path.dirname(_HERE)):   # 兼容放在 tests\ 或项目根目录
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

import app as appmod  # noqa: E402
import playtime  # noqa: E402
import nintendo_parental as np  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not ok else ""))


# --------------------------------------------------------------------------- #
print("\n=== 1. 时长解析（各语言/格式）===")
cases = [
    ("塞尔达传说 123 小时以上", 123.0),
    ("123小时", 123.0),
    ("123 小時", 123.0),
    ("プレイ時間 123時間以上", 123.0),
    ("123 hours or more", 123.0),
    ("123 hrs", 123.0),
    ("123h", 123.0),
    ("12 小时 30 分钟", 12.5),
    ("1,234 小时", 1234.0),
    ("45 分钟", 0.75),
    ("45 minutes", 0.75),
    ("未满 1 小时", 1.0),
]
for text, expected in cases:
    got = playtime.parse_duration(text)
    check(f"{text!r} → {expected}h", got is not None and abs(got[0] - expected) < 0.01,
          got[0] if got else None)

check("纯文字不误判", playtime.parse_duration("塞尔达传说") is None)
check("价格数字不误判为时长", playtime.parse_duration("¥349.00") is None)

print("\n=== 2. 标题归一化与匹配 ===")
check("全角转半角 + 去空白", playtime.normalize_title("萨尔达 传说") == "萨尔达传说")
check("去修饰词", playtime.normalize_title("游玩时间 塞尔达") == "塞尔达")
check("完全相同得 1.0", playtime.match_score("塞尔达传说", "塞尔达传说") == 1.0)
check("包含关系得高分", playtime.match_score("塞尔达传说", "塞尔达传说 王国之泪") > 0.7)
check("无关标题得分低", playtime.match_score("塞尔达传说", "马力欧赛车") < 0.5)

GAMES = [
    {"id": 1, "title": "塞尔达传说 王国之泪", "playtime_hours": 120},
    {"id": 2, "title": "马力欧赛车 8 豪华版", "playtime_hours": 210},
    {"id": 3, "title": "空洞骑士", "playtime_hours": 0},
]
m = playtime.best_match("塞尔达传说 王国之泪", GAMES)
check("精确匹配命中 id=1", m and m["game_id"] == 1 and m["exact"], m)
m2 = playtime.best_match("马力欧赛车８豪华版", GAMES)
check("全角数字也能匹配 id=2", m2 and m2["game_id"] == 2, m2)
check("完全无关返回 None", playtime.best_match("某某不存在的游戏", GAMES) is None)

print("\n=== 3. 截图条目抽取（标题在上、时长在下）===")
items = [
    {"text": "塞尔达传说 王国之泪", "x": 30, "y": 100, "h": 20},
    {"text": "125 小时以上", "x": 30, "y": 140, "h": 18},
    {"text": "马力欧赛车 8 豪华版", "x": 30, "y": 200, "h": 20},
    {"text": "210 小时", "x": 30, "y": 240, "h": 18},
    {"text": "空洞骑士", "x": 30, "y": 300, "h": 20},
    {"text": "65 小时 30 分钟", "x": 30, "y": 340, "h": 18},
]
entries = playtime.extract_entries(items)
check("抽出 3 条", len(entries) == 3, len(entries))
by_title = {e["title"]: e["hours"] for e in entries}
check("条目1 时长 125", by_title.get("塞尔达传说 王国之泪") == 125.0, by_title)
check("条目2 时长 210", by_title.get("马力欧赛车 8 豪华版") == 210.0, by_title)
check("条目3 时长 65.5", by_title.get("空洞骑士") == 65.5, by_title)

same_line = [{"text": "塞尔达传说 王国之泪 125 小时以上", "x": 0, "y": 10, "h": 10}]
e2 = playtime.extract_entries(same_line)
check("同行标题+时长也能解析", len(e2) == 1 and e2[0]["hours"] == 125.0, e2)

dup = [
    {"text": "空洞骑士", "x": 0, "y": 10, "h": 10},
    {"text": "60 小时", "x": 0, "y": 40, "h": 10},
    {"text": "空洞骑士", "x": 0, "y": 100, "h": 10},
    {"text": "65 小时", "x": 0, "y": 130, "h": 10},
]
check("同标题去重保留较大值", len(playtime.extract_entries(dup)) == 1)

cands = playtime.build_candidates(items, GAMES)
check("候选行带匹配结果", sum(1 for c in cands if c["match"]) == 3, cands)
check("候选行 source=ocr", all(c["source"] == "ocr" for c in cands))

print("\n=== 4. 任天堂鉴权流程（打桩）===")
state, verifier = np.new_login_state()
url = np.ParentalClient.login_url(state, verifier)
check("登录链接指向任天堂账号域", url.startswith("https://accounts.nintendo.com/connect/1.0.0/authorize?"))
query = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
check("client_id 为家长控制 App", query.get("client_id") == "54789befb391a838", query.get("client_id"))
check("redirect_uri 正确", query.get("redirect_uri") == "npf54789befb391a838://auth")
check("response_type=session_token_code", query.get("response_type") == "session_token_code")
check("PKCE 方法为 S256", query.get("session_token_code_challenge_method") == "S256")
check("challenge 与 verifier 对应", query.get("session_token_code_challenge") == np.code_challenge(verifier))
check("申请了游玩汇总权限", "moonDailySummary" in (query.get("scope") or ""))
check("verifier 长度 50", len(verifier) == 50, len(verifier))

import base64  # noqa: E402
import hashlib  # noqa: E402
expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().replace("=", "")
check("code_challenge 算法与规范一致", np.code_challenge(verifier) == expected_challenge)

good = "npf54789befb391a838://auth#session_token_code=abc123&state=xyz&session_state=ss"
parsed = np.parse_redirect_url(good)
check("解析回调链接", parsed["session_token_code"] == "abc123", parsed)

for bad, label in [("", "空字符串"), ("npf54789befb391a838://auth#state=only", "缺少 code"),
                   ("随便一段文字", "完全无关")]:
    try:
        np.parse_redirect_url(bad)
        check(f"非法链接应报错（{label}）", False)
    except np.NintendoError:
        check(f"非法链接报错（{label}）", True)

try:
    np.parse_redirect_url("npf54789befb391a838://auth#state=only")
    check("缺少 code 时给出具体提示", False)
except np.NintendoError as exc:
    check("缺少 code 时给出具体提示", "session_token_code" in str(exc), str(exc))

print("\n=== 5. 响应解析与时长聚合 ===")
sample_daily = {
    "dailySummaries": [
        {"date": "2026-09-17", "playingTime": 90, "players": [
            {"playingTime": 90, "playedGames": [
                {"playingTime": 60, "meta": {"applicationId": "0100AAAA", "title": "塞尔达"}},
                {"playingTime": 30, "meta": {"applicationId": "0100BBBB", "title": "空洞骑士"}},
            ]},
        ]},
        {"date": "2026-09-16", "playingTime": 45, "players": [
            {"playingTime": 45, "playedGames": [
                {"playingTime": 45, "meta": {"applicationId": "0100aaaa"}},
            ]},
        ]},
        {"date": "2026-09-15", "playingTime": 0, "players": []},
    ]
}
minutes = {}
np._accumulate(sample_daily, minutes)
check("跨天累加同一游戏（大小写不敏感）", minutes.get("0100AAAA") == 105, minutes)
check("第二个游戏累计 30 分钟", minutes.get("0100BBBB") == 30, minutes)

found = np._find_list(sample_daily, ("dailySummaries",))
check("_find_list 取出日报数组", len(found) == 3, len(found))
nested = {"result": {"data": {"whitelistedApplicationList": [{"applicationId": "X", "title": "T"}]}}}
check("_find_list 支持嵌套外层", np._find_list(nested, ("whitelistedApplicationList",))[0]["title"] == "T")
check("_find_list 找不到返回空表", np._find_list({}, ("nope",)) == [])

print("\n=== 6. playtime_by_title 汇总（打桩 HTTP）===")
calls = []
SAMPLE_SETTING = {"parentalControlSetting": {"whitelistedApplicationList": [
    {"applicationId": "0100AAAA", "title": "塞尔达传说 王国之泪"},
    {"applicationId": "0100BBBB", "title": "空洞骑士"},
]}}


def fake_request(method, url, **kwargs):
    calls.append(url)
    if "fetchDailySummaries" in url:
        return sample_daily
    if "fetchParentalControlSetting" in url:
        return SAMPLE_SETTING
    if "fetchMonthlySummary" in url:
        return {"monthlySummary": sample_daily}
    if "fetchOwnedDevices" in url:
        return {"ownedDevices": [{"deviceId": "DEV1", "name": "客厅的 Switch"}]}
    return {}


orig_request = np._request
np._request = fake_request
try:
    client = np.ParentalClient(session_token="fake")
    client.id_token = "fake-id-token"       # 跳过换令牌
    client.token_expiry = 9e9
    entries, meta = client.playtime_by_title("DEV1", days=0)

    check("汇总出 2 款游戏", len(entries) == 2, entries)
    top = entries[0]
    check("按时长降序", top["application_id"] == "0100AAAA", top)
    check("小时换算正确（105 分钟 → 1.75h）", abs(top["hours"] - 1.75) < 0.01, top["hours"])
    check("标题由白名单映射", top["title"] == "塞尔达传说 王国之泪", top["title"])
    check("统计口径写入 meta", meta["game_count"] == 2 and "sources" in meta, meta)
    check("请求带上 deviceId", any("deviceId=DEV1" in c for c in calls), calls)

    entries7, _ = client.playtime_by_title("DEV1", days=1)
    # 第一天有 2 款游戏：塞尔达 60 分钟、空洞骑士 30 分钟
    check("days=1 只统计第一天", len(entries7) == 2
          and abs(dict((e["application_id"], e["hours"]) for e in entries7)["0100BBBB"] - 0.5) < 0.01,
          entries7)

    entries_m, meta_m = client.playtime_by_title("DEV1", year=2026, month=9)
    check("月报模式可用", len(entries_m) == 2 and "月报" in meta_m["sources"][0], meta_m)

    # 白名单接口挂了也不该整体失败
    def fail_setting(method, url, **kwargs):
        if "fetchParentalControlSetting" in url:
            raise np.NintendoError("boom")
        return fake_request(method, url, **kwargs)
    np._request = fail_setting
    entries2, _ = client.playtime_by_title("DEV1", days=0)
    check("标题接口失败时回退为 applicationId", entries2[0]["title"] == "0100AAAA", entries2)
    np._request = fake_request

    # 令牌过期时的错误提示
    def unauthorized(method, url, **kwargs):
        raise np.NintendoError("任天堂接口返回 HTTP 401：unauthorized")
    np._request = unauthorized
    client.token_expiry = 0
    try:
        client.refresh()
        check("应抛出令牌错误", False)
    except np.NintendoError as exc:
        check("HTTP 401 转成可读错误", "401" in str(exc), str(exc))
finally:
    np._request = orig_request

print("\n=== 7. 路由：写入时长 ===")
appmod.init_db()
appmod.app.config.update(TESTING=True)
client = appmod.app.test_client()

import re  # noqa: E402
html = client.get("/").get_data(as_text=True)
TOKEN = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
check("首页出现同步入口", 'data-open="syncModal"' in html and 'id="syncModal"' in html)
check("同步弹窗包含任天堂页签", 'data-sync-tab="nintendo"' in html)
# 整库标题改为按需加载，不应出现在首页 HTML 里
check("首页不再内联整库标题", "data-library=" not in html)
lib = client.get("/library").get_json()
check("整库接口可用", lib.get("ok") and isinstance(lib.get("games"), list), lib)

client.post("/add", data={"_csrf": TOKEN, "title": "空洞骑士", "playtime_hours": "65.5"})
with appmod.db() as conn:
    gid = conn.execute("SELECT id FROM games WHERE title=?", ("空洞骑士",)).fetchone()["id"]

r = client.post("/playtime/apply", json={"items": [{"game_id": gid, "hours": 70, "mode": "set"}]},
                headers={"X-CSRF-Token": TOKEN})
check("写入接口返回 200", r.status_code == 200, r.status_code)
with appmod.db() as conn:
    hours = conn.execute("SELECT playtime_hours FROM games WHERE id=?", (gid,)).fetchone()[0]
check("覆盖模式写入 70", hours == 70.0, hours)

client.post("/playtime/apply", json={"items": [{"game_id": gid, "hours": 5, "mode": "add"}]},
            headers={"X-CSRF-Token": TOKEN})
with appmod.db() as conn:
    hours = conn.execute("SELECT playtime_hours FROM games WHERE id=?", (gid,)).fetchone()[0]
check("累加模式写入 75", hours == 75.0, hours)

r = client.post("/playtime/apply", json={"items": [{"game_id": 999999, "hours": 5}]},
                headers={"X-CSRF-Token": TOKEN})
check("不存在的游戏被跳过并报告",
      r.status_code == 200 and r.get_json()["count"] == 0 and r.get_json()["errors"])

r = client.post("/playtime/apply", json={"items": []}, headers={"X-CSRF-Token": TOKEN})
check("空列表返回 400", r.status_code == 400, r.status_code)

r = client.post("/playtime/apply", json={"items": [{"game_id": gid, "hours": -5}]},
                headers={"X-CSRF-Token": TOKEN})
with appmod.db() as conn:
    hours = conn.execute("SELECT playtime_hours FROM games WHERE id=?", (gid,)).fetchone()[0]
check("负数时长被拒绝", r.get_json()["count"] == 0 and hours == 75.0)

r = client.post("/playtime/apply", json={"items": [{"game_id": gid, "hours": 80}]})
check("缺少 CSRF 令牌被拒绝", r.status_code == 400, r.status_code)

print("\n=== 8. 路由：任天堂绑定流程（打桩）===")
r = client.get("/nintendo")
check("初始状态为未绑定", r.get_json()["linked"] is False, r.get_json())

r = client.post("/nintendo/link", data={"_csrf": TOKEN})
body = r.get_json()
check("生成登录链接", r.status_code == 200 and body["url"].startswith("https://accounts.nintendo.com/"), body)

r = client.post("/nintendo/complete", data={"_csrf": TOKEN, "redirect_url": ""})
check("空回调链接被拒绝", r.status_code == 400, r.status_code)

r = client.post("/nintendo/sync", data={"_csrf": TOKEN})
check("未绑定时同步被拒绝", r.status_code == 400 and "尚未绑定" in r.get_json()["error"], r.get_json())


def fake_complete(self, redirect_url, verifier):
    return "SESSION-TOKEN-XYZ"


def fake_account(self):
    return {"id": "acc1", "nickname": "测试玩家", "country": {"name": "Japan"}}


def fake_devices(self):
    return [{"deviceId": "DEV1", "name": "客厅的 Switch"}]


np.ParentalClient.complete_login = fake_complete
np.ParentalClient.account_info = fake_account
np.ParentalClient.devices = fake_devices

r = client.post("/nintendo/complete", data={
    "_csrf": TOKEN,
    "redirect_url": "npf54789befb391a838://auth#session_token_code=abc&state=s",
})
body = r.get_json()
check("完成绑定", r.status_code == 200 and body["ok"], body)
check("返回昵称", (body.get("account") or {}).get("nickname") == "测试玩家", body)
check("返回主机", (body.get("devices") or [{}])[0].get("name") == "客厅的 Switch", body)

r = client.get("/nintendo")
check("状态变为已绑定", r.get_json()["linked"] is True, r.get_json())
check("令牌已落盘", os.path.isfile(appmod.NINTENDO_PATH))


def fake_by_title(self, device_id, days=0, year=0, month=0):
    return ([{"application_id": "0100AAAA", "title": "空洞骑士", "hours": 3.5, "minutes": 210}],
            {"sources": ["测试"], "game_count": 1, "total_hours": 3.5, "titles_resolved": 1})


np.ParentalClient.playtime_by_title = fake_by_title
r = client.post("/nintendo/sync", data={"_csrf": TOKEN, "days": "7"})
body = r.get_json()
check("同步返回候选行", r.status_code == 200 and len(body["candidates"]) == 1, body)
check("候选行匹配到库内游戏", body["candidates"][0]["match"]["game_id"] == gid, body["candidates"])
check("候选行 source=nintendo", body["candidates"][0]["source"] == "nintendo")
check("记录上次同步时间", bool(client.get("/nintendo").get_json()["last_sync"]))

r = client.post("/nintendo/unlink", data={"_csrf": TOKEN})
check("解除绑定", r.status_code == 200 and client.get("/nintendo").get_json()["linked"] is False)
check("令牌文件已删除", not os.path.isfile(appmod.NINTENDO_PATH))

print("\n=== 9. OCR 端到端：真实截图 → 识别 → 匹配 ===")
# 先把截图里会出现的三款游戏放进库里，才能验证匹配
for title in ("塞尔达传说 王国之泪", "马力欧赛车 8 豪华版"):
    client.post("/add", data={"_csrf": TOKEN, "title": title, "playtime_hours": "0"})

try:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (760, 420), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 34)
    except Exception:
        font = ImageFont.load_default()
    rows = [("塞尔达传说 王国之泪", "125 小时以上"),
            ("马力欧赛车 8 豪华版", "210 小时"),
            ("空洞骑士", "65 小时 30 分钟")]
    y = 40
    for title, hours in rows:
        draw.text((40, y), title, fill="black", font=font)
        draw.text((40, y + 46), hours, fill="#333333", font=font)
        y += 130
    buf = io.BytesIO()
    img.save(buf, "PNG")

    r = client.post("/playtime/scan",
                    data={"_csrf": TOKEN, "screenshot": (io.BytesIO(buf.getvalue()), "s.png")},
                    content_type="multipart/form-data")
    body = r.get_json()
    check("扫描接口返回 200", r.status_code == 200, body)
    cands = body.get("candidates") or []
    check("识别出 3 条游玩记录", len(cands) == 3, cands)
    by_title = {c["raw_title"]: c["hours"] for c in cands}
    check("识别到 125 小时", any(abs(v - 125) < 0.01 for v in by_title.values()), by_title)
    check("识别到 210 小时", any(abs(v - 210) < 0.01 for v in by_title.values()), by_title)
    check("识别到 65.5 小时", any(abs(v - 65.5) < 0.01 for v in by_title.values()), by_title)
    check("三条全部匹配到库内游戏", sum(1 for c in cands if c["match"]) == 3, cands)
    check("统计信息正确", (body.get("stats") or {}).get("matched") == 3, body.get("stats"))
except Exception as exc:  # pragma: no cover
    check(f"OCR 端到端测试执行失败：{exc}", False)

r = client.post("/playtime/scan", data={"_csrf": TOKEN, "screenshot": (io.BytesIO(b"x"), "a.txt")},
                content_type="multipart/form-data")
check("非图片被拒绝", r.status_code == 400, r.status_code)

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for name in FAIL:
    print("  FAILED:", name)
print("=" * 62)
sys.exit(1 if FAIL else 0)
