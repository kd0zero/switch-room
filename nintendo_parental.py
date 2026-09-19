"""
Nintendo Switch 家长控制 API 客户端。

⚠️ 非官方接口
    任天堂没有公开 API。这里使用的是「Nintendo Switch Parental Controls」
    手机 App 背后的接口（端点与鉴权流程对照开源实现 pynintendoparental /
    pynintendoauth 核实过）。任天堂随时可能改动，导致功能失效。

使用前提
    主机上必须已经开启「家长控制」并绑定过手机 App，否则接口里没有数据。

鉴权流程（PKCE）
    1. 打开 login_url()，用任天堂账号登录
    2. 停在「Linking an External Account」页面，右键「Select this person」→复制链接
       （链接形如 npf54789befb391a838://auth#session_token_code=...&state=...）
    3. 把整条链接交给 complete_login()，换取 session_token
    4. session_token 可长期复用，用来换 id_token 调用业务接口

本模块只用标准库，方便随程序一起打包。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "54789befb391a838"                    # 家长控制 App 的客户端 ID
REDIRECT_URI = "npf54789befb391a838://auth"
SCOPES = [
    "openid", "user",
    "moonUser:administration", "moonDevice:create", "moonOwnedDevice:administration",
    "moonParentalControlSetting", "moonParentalControlSetting:update",
    "moonParentalControlSettingState", "moonPairingState", "moonSmartDevice:administration",
    "moonDailySummary", "moonMonthlySummary",
]

AUTHORIZE_URL = "https://accounts.nintendo.com/connect/1.0.0/authorize"
SESSION_TOKEN_URL = "https://accounts.nintendo.com/connect/1.0.0/api/session_token"
TOKEN_URL = "https://accounts.nintendo.com/connect/1.0.0/api/token"
ACCOUNT_URL = "https://api.accounts.nintendo.com/2.0.0/users/me"

API_BASE = "https://app.lp1.znma.srv.nintendo.net"
APP_PKG = "com.nintendo.znma"
APP_VERSION = "2.4.0"
APP_BUILD = "660"
OS_NAME = "ANDROID"
OS_VERSION = "34"
DEVICE_MODEL = "Pixel 4 XL"

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer-session-token"
TIMEOUT = 20


class NintendoError(Exception):
    """接口调用失败。"""


# --------------------------------------------------------------------------- #
# PKCE 工具
# --------------------------------------------------------------------------- #
def gen_verifier() -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(50))


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().replace("=", "")


def parse_redirect_url(text: str) -> dict:
    """从用户粘贴的链接里取出 session_token_code 等参数。"""
    text = (text or "").strip()
    if not text:
        raise NintendoError("请粘贴完整的回调链接")
    fragment = text.split("#", 1)[1] if "#" in text else text
    params = {}
    for pair in fragment.split("&"):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        params[key.strip()] = urllib.parse.unquote(value.strip())
    if "session_token_code" not in params:
        raise NintendoError(
            "链接里没有 session_token_code。"
            "请确认复制的是「Select this person」按钮的链接地址，而不是直接点击它。"
        )
    return params


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _request(method: str, url: str, *, json_body=None, form_body=None, headers=None) -> dict:
    data = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"

    # 允许走系统代理（国内直连任天堂常常需要）
    proxy = os.environ.get("SW_PROXY") or None
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        opener = urllib.request.build_opener()

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise NintendoError(f"任天堂接口返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise NintendoError(
            f"无法连接任天堂服务器（{exc.reason}）。"
            "若本机需要代理才能访问，请设置环境变量 SW_PROXY，例如 http://127.0.0.1:7890"
        ) from exc

    if not body:
        return {}
    try:
        return json.loads(body)
    except ValueError as exc:
        raise NintendoError(f"接口返回的不是 JSON：{body[:200]}") from exc


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class ParentalClient:
    """家长控制 API 客户端。"""

    def __init__(self, session_token: str | None = None):
        self.session_token = session_token
        self.id_token: str | None = None
        self.access_token: str | None = None
        self.token_expiry: float = 0
        self.account: dict | None = None

    # -- 登录 -------------------------------------------------------------- #
    @staticmethod
    def login_url(state: str, verifier: str) -> str:
        query = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "session_token_code",
            "scope": " ".join(SCOPES),
            "session_token_code_challenge": code_challenge(verifier),
            "session_token_code_challenge_method": "S256",
            "state": state,
            "theme": "login_form",
        }
        return AUTHORIZE_URL + "?" + urllib.parse.urlencode(query).replace("%2B", "+")

    def complete_login(self, redirect_url: str, verifier: str) -> str:
        """用回调链接换取长期有效的 session_token。"""
        params = parse_redirect_url(redirect_url)
        payload = _request(
            "POST", SESSION_TOKEN_URL,
            form_body={
                "client_id": CLIENT_ID,
                "session_token_code": params["session_token_code"],
                "session_token_code_verifier": verifier,
            },
        )
        token = payload.get("session_token")
        if not token:
            raise NintendoError("未能取得 session_token，请重新获取回调链接")
        self.session_token = token
        return token

    # -- 令牌 -------------------------------------------------------------- #
    def refresh(self) -> None:
        if not self.session_token:
            raise NintendoError("尚未登录")
        if self.id_token and time.time() < self.token_expiry:
            return

        payload = _request(
            "POST", TOKEN_URL,
            json_body={
                "client_id": CLIENT_ID,
                "grant_type": GRANT_TYPE,
                "session_token": self.session_token,
            },
        )
        if not payload.get("id_token"):
            error = payload.get("error", "未知错误")
            raise NintendoError(f"刷新令牌失败：{error}（session_token 可能已失效，请重新绑定）")
        self.id_token = payload["id_token"]
        self.access_token = payload.get("access_token")
        # 提前 5 分钟视为过期
        self.token_expiry = time.time() + max(60, int(payload.get("expires_in", 900)) - 300)

    def account_info(self) -> dict:
        """取得账号信息（昵称等）。"""
        self.refresh()
        return _request("GET", ACCOUNT_URL, headers={"Authorization": f"Bearer {self.access_token}"})

    # -- 业务接口 ---------------------------------------------------------- #
    def _api(self, path: str, tz: str = "Asia/Shanghai", lang: str = "zh-CN") -> dict:
        self.refresh()
        headers = {
            "User-Agent": f"moon_ANDROID/{APP_VERSION} ({APP_PKG}; build:{APP_BUILD}; {OS_NAME} {OS_VERSION})",
            "X-Moon-App-Id": APP_PKG,
            "X-Moon-Os": OS_NAME,
            "X-Moon-Os-Version": OS_VERSION,
            "X-Moon-Model": DEVICE_MODEL,
            "X-Moon-App-Display-Version": APP_VERSION,
            "X-Moon-App-Internal-Version": APP_BUILD,
            "X-Moon-TimeZone": tz,
            "X-Moon-Os-Language": lang,
            "X-Moon-App-Language": lang,
            # 关键：业务接口用的是 id_token
            "Authorization": f"Bearer {self.id_token}",
        }
        return _request("GET", API_BASE + path, headers=headers)

    def devices(self) -> list:
        data = self._api("/v2/actions/user/fetchOwnedDevices")
        return _find_list(data, ("ownedDevices", "devices"))

    def daily_summaries(self, device_id: str) -> list:
        data = self._api(f"/v2/actions/playSummary/fetchDailySummaries?deviceId={urllib.parse.quote(device_id)}")
        return _find_list(data, ("dailySummaries",))

    def monthly_summary(self, device_id: str, year: int, month: int) -> dict:
        path = (
            f"/v2/actions/playSummary/fetchMonthlySummary?deviceId={urllib.parse.quote(device_id)}"
            f"&year={year}&month={month:02d}&containLatest=false"
        )
        return self._api(path)

    def application_titles(self, device_id: str) -> dict:
        """返回 {applicationId: 标题} 映射。"""
        data = self._api(
            f"/v2/actions/parentalControlSetting/fetchParentalControlSetting?deviceId={urllib.parse.quote(device_id)}"
        )
        titles = {}
        for entry in _find_list(data, ("whitelistedApplicationList",)):
            app_id = str(entry.get("applicationId", "")).upper()
            title = entry.get("title")
            if app_id and title:
                titles[app_id] = title
        return titles

    # -- 汇总 -------------------------------------------------------------- #
    def playtime_by_title(self, device_id: str, days: int = 0, year: int = 0, month: int = 0):
        """汇总「每个游戏玩了多少」。

        days>0  只统计最近 N 天（来自每日汇总）
        year/month 指定时，改用月度汇总（历史更长）
        返回 (entries, meta)
        """
        minutes: dict[str, int] = {}
        sources = []

        if year and month:
            summary = self.monthly_summary(device_id, year, month)
            _accumulate(summary, minutes)
            sources.append(f"{year}-{month:02d} 月报")
        else:
            summaries = self.daily_summaries(device_id)
            if days > 0:
                summaries = summaries[:days]
            for day in summaries:
                _accumulate(day, minutes)
            if summaries:
                dates = [d.get("date") for d in summaries if d.get("date")]
                if dates:
                    sources.append(f"{min(dates)} ~ {max(dates)}（{len(summaries)} 天）")
            else:
                sources.append("近期日报")

        titles = {}
        try:
            titles = self.application_titles(device_id)
        except NintendoError:
            pass

        entries = []
        for app_id, total in minutes.items():
            if total <= 0:
                continue
            entries.append({
                "application_id": app_id,
                "title": titles.get(app_id) or app_id,
                "hours": round(total / 60, 2),
                "minutes": total,
            })
        entries.sort(key=lambda item: item["hours"], reverse=True)

        meta = {
            "sources": sources,
            "game_count": len(entries),
            "total_hours": round(sum(e["hours"] for e in entries), 2),
            "titles_resolved": len(titles),
        }
        return entries, meta


# --------------------------------------------------------------------------- #
# 容错解析：不同版本/地区的响应外层字段名可能不同
# --------------------------------------------------------------------------- #
def _find_list(node, keys) -> list:
    """在响应里按给定键名找列表，找不到就递归找同名字段。"""
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, list):
                return value
        for value in node.values():
            found = _find_list(value, keys)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_list(item, keys)
            if found:
                return found
    return []


def _accumulate(node, minutes: dict) -> None:
    """递归找出所有 playedGames，把同一 applicationId 的时长累加。"""
    if isinstance(node, dict):
        games = node.get("playedGames")
        if isinstance(games, list):
            for game in games:
                if not isinstance(game, dict):
                    continue
                meta = game.get("meta") or {}
                app_id = str(meta.get("applicationId") or game.get("applicationId") or "").upper()
                played = game.get("playingTime") or 0
                if app_id and isinstance(played, (int, float)):
                    minutes[app_id] = minutes.get(app_id, 0) + int(played)
        for value in node.values():
            _accumulate(value, minutes)
    elif isinstance(node, list):
        for item in node:
            _accumulate(item, minutes)


def new_login_state() -> tuple[str, str]:
    """生成一次登录所需的 state 与 code_verifier。"""
    return secrets.token_urlsafe(16), gen_verifier()
