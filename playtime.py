"""
游玩时长同步 —— 截图 OCR 解析与游戏匹配。

支持识别以下截图里的「游戏名 + 时长」：
  · Switch 个人主页的「游玩记录」列表（简体 / 繁体 / 日文 / 英文界面）
  · Nintendo Switch Parental Controls App 的月报、日报、今日游玩
  · Switch 2 的个人资料游玩记录

时长格式覆盖：
  123 小时以上 / 123小时 / 123 小时 45 分钟 / 45 分钟
  123 時間以上 / 123 分
  123 hours or more / 123 hrs / 123h / 123 hours 45 minutes

设计要点：完整保留「原始文本 → 结构化条目 → 与库内匹配」三段，
中间结果全部返回给前端做人工确认，不直接写库。
"""
from __future__ import annotations

import difflib
import re
import unicodedata

# --------------------------------------------------------------------------- #
# 时长解析
# --------------------------------------------------------------------------- #
_NUM = r"(\d{1,4}(?:[,，]\d{3})?(?:\.\d)?)"

# (正则, 处理函数) —— 顺序很重要：先匹配「小时+分钟」这种完整形态
_DURATION_RULES = [
    # 123 小时 45 分钟 / 123時間45分
    (re.compile(_NUM + r"\s*(?:小时|小時|時間|时间)\s*" + _NUM + r"\s*(?:分钟|分鐘|分)"),
     lambda m: _to_float(m.group(1)) + _to_float(m.group(2)) / 60),
    # 123 小时以上 / 123時間以上
    (re.compile(_NUM + r"\s*(?:小时|小時|時間|时间)\s*(?:以上|\+|更多|or more|以上です)"),
     lambda m: _to_float(m.group(1))),
    # 123 小时 / 123時間 / 123h
    (re.compile(_NUM + r"\s*(?:小时|小時|時間|时间)"),
     lambda m: _to_float(m.group(1))),
    # 123 hours or more / 123 hrs
    (re.compile(_NUM + r"\s*(?:hours?|hrs?|h)\b\s*(?:or more|and more|\+)?", re.I),
     lambda m: _to_float(m.group(1))),
    # 45 分钟（没有小时）
    (re.compile(_NUM + r"\s*(?:分钟|分鐘|分)\b"),
     lambda m: _to_float(m.group(1)) / 60),
    (re.compile(_NUM + r"\s*(?:minutes?|mins?)\b", re.I),
     lambda m: _to_float(m.group(1)) / 60),
]

# 「未满 1 小时」这类描述
_LESS_THAN = re.compile(r"(?:未满|未滿|不满|不滿|less than|未満)\s*" + _NUM + r"\s*(?:小时|小時|時間|hour)", re.I)

_NOISE = re.compile(
    r"(游玩时间|遊玩時間|プレイ時間|play\s*time|总游玩|合計|合计|今日|昨天|昨日|今天|"
    r"本月|今月|先月|上月|最近|first played|最后游玩|最後|最初|开始|開始|以上|or more)",
    re.I,
)


def _to_float(text: str) -> float:
    try:
        return float(str(text).replace(",", "").replace("，", ""))
    except (TypeError, ValueError):
        return 0.0


def parse_duration(text: str):
    """从一行文本中解析时长。返回 (小时数, 匹配到的原文) 或 None。"""
    if not text:
        return None

    less = _LESS_THAN.search(text)
    if less:
        return 1.0, less.group(0)

    for pattern, handler in _DURATION_RULES:
        match = pattern.search(text)
        if not match:
            continue
        try:
            hours = handler(match)
        except (TypeError, ValueError):
            continue
        if hours is None:
            continue
        if hours > 99999:            # 明显是别的数字（价格、日期）
            continue
        return round(hours, 2), match.group(0)
    return None


# --------------------------------------------------------------------------- #
# 标题归一化与匹配
# --------------------------------------------------------------------------- #
_PUNCT = re.compile(r"[\s\-_·・:：,，.。!！?？'\"“”‘’()（）\[\]【】<>《》/\\|+~—–]+")

_FULLWIDTH = {ord(c): ord(c) - 0xFEE0 for c in map(chr, range(0xFF01, 0xFF5F))}


def normalize_title(text: str) -> str:
    """归一化标题：全角转半角、去标点空白、去「游玩时间」等噪声词、转小写。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.translate(_FULLWIDTH)
    text = _NOISE.sub("", text)
    text = _PUNCT.sub("", text)
    return text.strip().lower()


def match_score(needle: str, haystack: str) -> float:
    """两个已归一化标题的相似度（0~1）。"""
    if not needle or not haystack:
        return 0.0
    if needle == haystack:
        return 1.0
    if needle in haystack or haystack in needle:
        shorter, longer = sorted((needle, haystack), key=len)
        return 0.72 + 0.28 * (len(shorter) / len(longer))
    return difflib.SequenceMatcher(None, needle, haystack).ratio()


def best_match(title: str, games) -> dict | None:
    """在游戏库里找最相似的条目。games 为含 id/title/playtime_hours 的行。"""
    needle = normalize_title(title)
    if not needle:
        return None

    scored = []
    for game in games:
        score = match_score(needle, normalize_title(game["title"]))
        if score > 0:
            scored.append((score, game))
    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    score, game = scored[0]
    if score < 0.55:
        return None
    return {
        "game_id": game["id"],
        "title": game["title"],
        "current_hours": game["playtime_hours"] or 0,
        "score": round(score, 3),
        "exact": score >= 0.999,
    }


# --------------------------------------------------------------------------- #
# 截图条目抽取
# --------------------------------------------------------------------------- #
def _row_key(item) -> float:
    return float(item.get("y", 0))


def extract_entries(items, gap_factor: float = 4.0):
    """把 OCR 结果（含坐标）整理成 [{title, hours, raw, note}]。

    规则：某行含时长 → 该行剩余文字即标题；若剩余文字太短，
    则取相邻行作为标题（Switch 游玩记录是「标题在上、时长在下」的排版）。
    上下搜索的像素距离按文字高度自适应，避免截图被放大后阈值失效。
    """
    if not items:
        return []

    ordered = sorted(items, key=_row_key)

    # 阈值随字号缩放：小图被 OCR 预处理放大后，行距也会等比变大
    heights = [float(it.get("h") or 0) for it in ordered if (it.get("h") or 0) > 0]
    median_h = sorted(heights)[len(heights) // 2] if heights else 0.0
    gap_limit = max(80.0, median_h * gap_factor)

    def usable(text: str) -> bool:
        return bool(text) and not parse_duration(text) and len(normalize_title(text)) >= 2

    entries = []

    for index, item in enumerate(ordered):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        parsed = parse_duration(text)
        if not parsed:
            continue

        hours, matched = parsed
        remainder = text.replace(matched, " ")
        remainder = _NOISE.sub(" ", remainder)
        remainder = re.sub(r"[\s\-–—:：|]+", " ", remainder).strip(" -–—:：|")

        note = ""
        if len(normalize_title(remainder)) < 3:
            # 标题多半在相邻行：先往上找，再往下找（不同界面排版不同）
            title = ""
            y_here = _row_key(item)
            for step in (-1, 1):
                probe = index + step
                while 0 <= probe < len(ordered):
                    candidate = ordered[probe]
                    if abs(_row_key(candidate) - y_here) > gap_limit:
                        break
                    if usable((candidate.get("text") or "").strip()):
                        title = candidate["text"].strip()
                        note = "标题取自上一行" if step < 0 else "标题取自下一行"
                        break
                    probe += step
                if title:
                    break

            if not title:
                title = remainder
                note = "标题取自同行文本，请确认"
        else:
            title = remainder

        title = _NOISE.sub("", title).strip(" -–—:：|")
        if not title:
            continue

        entries.append({
            "title": title,
            "hours": hours,
            "raw": text,
            "note": note,
        })

    # 同一标题去重，保留时长更大的一条
    merged = {}
    for entry in entries:
        key = normalize_title(entry["title"])
        if not key:
            continue
        if key not in merged or entry["hours"] > merged[key]["hours"]:
            merged[key] = entry
    return list(merged.values())


def build_candidates(items, games):
    """把 OCR 结果变成「候选同步行」列表，供前端确认。"""
    candidates = []
    for entry in extract_entries(items):
        candidates.append({
            "source": "ocr",
            "raw_title": entry["title"],
            "hours": entry["hours"],
            "note": entry["note"],
            "raw": entry["raw"],
            "match": best_match(entry["title"], games),
        })
    return candidates
