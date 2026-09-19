"""
OCR 引擎封装 —— 基于 RapidOCR（离线、中英文）。

对外接口
--------
available()          -> bool            引擎是否可用
warmup_async()       -> None            后台预加载模型，避免首次识别卡顿
ocr_image_file(path) -> dict            {raw, lines, date, price, ...}
extract_fields(text) -> (date, price)   纯文本提取（可单测）

相比 v1 的改进
--------------
1. 图片预处理：自动摆正 EXIF 方向、过小的截图放大、低对比度截图自动增强。
2. 日期识别：支持 2023年5月12日 / 2023-05-12 / 2023.5.12 / 23年5月12日 等，
   并校验月份与日期的合法性（不会再把 2023-13-45 当成日期）。
3. 价格识别：从「第一个匹配」升级为「多候选加权打分」——
   关键词（实付/合计/总计…）> 货币符号 > 「元」后缀 > 裸小数，
   再叠加「有两位小数」「金额在合理区间」等权重，显著降低误取订单号、日期、数量的概率。
4. 返回候选列表与识别原文，前端可展示、可人工改选。
"""
from __future__ import annotations

import os
import re
import threading
from datetime import date as _date

try:  # 新老包名都兼容
    from rapidocr_onnxruntime import RapidOCR
except Exception:  # pragma: no cover
    try:
        from rapidocr import RapidOCR  # type: ignore
    except Exception:
        RapidOCR = None

_OCR = None
_OCR_LOCK = threading.Lock()


def available() -> bool:
    return RapidOCR is not None


def _get_ocr():
    global _OCR
    if _OCR is None:
        with _OCR_LOCK:
            if _OCR is None:
                _OCR = RapidOCR()
    return _OCR


def warmup_async() -> None:
    """在后台线程预加载模型，首次上传截图时无需等待。"""
    if not available():
        return

    def _run():
        try:
            _get_ocr()
        except Exception:
            pass

    threading.Thread(target=_run, name="ocr-warmup", daemon=True).start()


# --------------------------------------------------------------------------- #
# 文本提取
# --------------------------------------------------------------------------- #
_PRICE_KEYWORDS = r"(?:实付|实付款|付款金额|支付金额|合计|总计|总价|总金额|应付|订单金额|小计|金额|价格|花费|售价)"

_CURRENCY = r"[¥￥$€£]"

# (正则, 基础权重)  —— 权重越高越可信
_PRICE_RULES = [
    (re.compile(_PRICE_KEYWORDS + r"\D{0,10}?(\d{1,3}(?:[,，]\d{3})*(?:\.\d{1,2})?)"), 6),
    (re.compile(_CURRENCY + r"\s*(\d{1,3}(?:[,，]\d{3})*(?:\.\d{1,2})?)"), 5),
    # OCR 常把 ¥ 认成 ? / ？/ Y / 羊，只在这些字符紧贴数字时可作候选
    (re.compile(r"[?？]\s*(\d{1,3}(?:[,，]\d{3})*\.\d{2})(?![\d])"), 2),
    (re.compile(r"(\d{1,3}(?:[,，]\d{3})*(?:\.\d{1,2})?)\s*元"), 5),
    (re.compile(r"(?:JPY|USD|CNY|RMB|HK\$)\s*(\d{1,3}(?:[,，]\d{3})*(?:\.\d{1,2})?)", re.I), 4),
    (re.compile(r"(?<![\d.,])(\d{1,3}(?:[,，]\d{3})*\.\d{2})(?![\d])"), 1),
]

_MONTHS = r"(1[0-2]|0?[1-9])"          # 长分支在前：避免把 "12" 拆成 "1"
_DAYS = r"(3[01]|[12]\d|0?[1-9])"      # 同理，避免把 "25" 拆成 "2"
_DATE_RULES = [
    # 带分隔符：2023-05-12 / 2023.5.12 / 2023/05/12 / 2023年5月12日
    (re.compile(r"(20\d{2})\s*[年\-/.]\s*" + _MONTHS + r"\s*[月\-/.]\s*" + _DAYS + r"(?!\d)\s*日?"), 4),
    # 两位年份：23年5月12日
    (re.compile(r"(\d{2})\s*年\s*" + _MONTHS + r"\s*月\s*" + _DAYS + r"(?!\d)\s*日"), 3),
    # 紧凑格式：20230512（权重最低，且前后不能再接数字）
    (re.compile(r"(?<!\d)(20\d{2})" + _MONTHS + _DAYS + r"(?!\d)"), 1),
]
_DATE_KEYWORDS = r"(?:购买时间|购买日期|下单时间|订单时间|交易时间|支付时间|成交时间|购买|日期)"


def _to_float(text: str):
    try:
        return float(text.replace(",", "").replace("，", ""))
    except (TypeError, ValueError):
        return None


def _score_price(value: float, weight: int, matched: str, has_decimal: bool) -> float:
    score = float(weight)
    if has_decimal:
        score += 2.5
    if 5 <= value <= 2000:
        score += 1.5
    elif value > 5000 or value < 1:
        score -= 3
    if _CURRENCY in matched:
        score += 1.5
    if "元" in matched:
        score += 1.0
    if value == int(value) and value >= 1900 and not has_decimal:
        score -= 2.0          # 很像年份
    return score


def extract_price_candidates(text: str):
    """返回按可信度降序的候选价格 [{'value','score','hint'}, ...]。"""
    if not text:
        return []

    found = {}
    for pattern, weight in _PRICE_RULES:
        for match in pattern.finditer(text):
            raw = match.group(1)
            value = _to_float(raw)
            if value is None:
                continue
            has_decimal = "." in raw
            score = _score_price(value, weight, match.group(0), has_decimal)
            key = round(value, 2)
            prev = found.get(key)
            if prev is None or score > prev["score"]:
                found[key] = {
                    "value": key,
                    "score": round(score, 2),
                    "hint": match.group(0).strip()[:40],
                    "index": match.start(),
                }

    ordered = sorted(found.values(), key=lambda c: (-c["score"], c["index"]))
    return ordered


def extract_date_candidates(text: str):
    """返回按可信度降序的候选日期 [{'value','score','hint'}, ...]。"""
    if not text:
        return []

    found = {}
    for pattern, weight in _DATE_RULES:
        for match in pattern.finditer(text):
            year, month, day = match.group(1), match.group(2), match.group(3)
            year_i = int(year)
            if year_i < 100:                      # 两位年份
                year_i = 2000 + year_i if year_i <= 70 else 1900 + year_i
            month_i, day_i = int(month), int(day)
            if not (2000 <= year_i <= 2100):
                continue
            try:                                  # 校验是真实存在的日历日期
                _date(year_i, month_i, day_i)
            except ValueError:
                continue
            value = f"{year_i:04d}-{month_i:02d}-{day_i:02d}"

            score = float(weight)
            head = text[max(0, match.start() - 12): match.start()]
            if re.search(_DATE_KEYWORDS, head):
                score += 5
            prev = found.get(value)
            if prev is None or score > prev["score"]:
                found[value] = {
                    "value": value,
                    "score": round(score, 2),
                    "hint": match.group(0).strip()[:40],
                    "index": match.start(),
                }

    ordered = sorted(found.values(), key=lambda c: (-c["score"], c["index"]))
    return ordered


def extract_fields(text: str):
    """从 OCR 文本中提取 (date, price)，取不到则为 None。"""
    dates = extract_date_candidates(text)
    prices = extract_price_candidates(text)
    return (
        dates[0]["value"] if dates else None,
        prices[0]["value"] if prices else None,
    )


# --------------------------------------------------------------------------- #
# 图片预处理
# --------------------------------------------------------------------------- #
def _prepare_image(path: str):
    """读取图片 -> 摆正方向 -> 适度缩放 / 增强对比度 -> numpy 数组。"""
    from PIL import Image, ImageOps
    import numpy as np

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = img.convert("RGB")

        width, height = img.size
        longest = max(width, height)
        if longest < 1000:                       # 小截图放大，提升小字识别率
            scale = min(3.0, 1600 / max(longest, 1))
            img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
        elif longest > 2800:                     # 超大图缩小，兼顾速度
            scale = 2800 / longest
            img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

        gray = img.convert("L")
        stats = gray.getextrema()
        if stats[1] - stats[0] < 90:             # 低对比度截图（常见于夜间模式）
            img = ImageOps.autocontrast(img, cutoff=1)

        return np.array(img)


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def ocr_image_file(path: str) -> dict:
    """对图片做 OCR，返回结构化结果。"""
    empty = {"raw": "", "lines": [], "items": [], "date": None, "price": None,
             "date_candidates": [], "price_candidates": [], "engine": "none"}
    if not available():
        return {**empty, "error": "OCR 引擎未安装（pip install rapidocr-onnxruntime）"}
    if not os.path.isfile(path):
        return {**empty, "error": "文件不存在"}

    try:
        image = _prepare_image(path)
    except Exception as exc:
        return {**empty, "error": f"图片无法读取：{exc}"}

    try:
        result, _ = _get_ocr()(image)
    except Exception as exc:
        return {**empty, "error": f"识别失败：{exc}"}

    lines = [item[1] for item in (result or []) if len(item) > 1 and item[1]]
    raw = "\n".join(lines)

    # 带坐标的条目：识别「游玩时长截图」时需要按行分组（标题在上、时长在下）
    items = []
    for entry in (result or []):
        if len(entry) < 2 or not entry[1]:
            continue
        box = entry[0]
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            items.append({
                "text": entry[1],
                "x": min(xs),
                "y": (min(ys) + max(ys)) / 2,
                "h": max(ys) - min(ys),
            })
        except (TypeError, ValueError, IndexError):
            items.append({"text": entry[1], "x": 0, "y": 0, "h": 0})

    date_candidates = extract_date_candidates(raw)
    price_candidates = extract_price_candidates(raw)

    return {
        "raw": raw,
        "lines": lines,
        "items": items,
        "date": date_candidates[0]["value"] if date_candidates else None,
        "price": price_candidates[0]["value"] if price_candidates else None,
        "date_candidates": date_candidates[:5],
        "price_candidates": price_candidates[:5],
        "engine": "rapidocr",
    }
