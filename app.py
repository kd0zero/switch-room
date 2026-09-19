"""
Switch 游戏收藏管家 —— 本地网页应用 (v2)

功能总览
--------
* 游戏库管理：标题、类型（实体卡带 / 数字版）、状态、评分、购买日期、价格、游玩时长、封面、备注
* 仪表盘：总量 / 卡带 / 数字版 / 累计花费 / 累计游玩 / 平均单价 / 通关率 / 近半年花费
* 购买截图 OCR：离线识别购买日期与价格并回填表单
* 照片收藏：多图上传 + 自动生成 WebP 缩略图 + 灯箱浏览
* 心流笔记：自动保存（防抖），未保存状态提示
* 数据备份：CSV 导出 / 导入
* 安全：CSRF 校验、上传类型与体积校验、路径穿越防护

设计说明
--------
* 单文件 Flask 应用，零外部服务依赖，数据落在 data/app.db（SQLite）。
* 上传的图片原样保存（不做有损转码），缩略图按需生成并缓存到 static/thumbs。
* 数据库结构变更通过 _migrate() 增量完成，旧版本 data/app.db 可直接沿用。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

import ocr_engine
import playtime
import nintendo_parental

# --------------------------------------------------------------------------- #
# 配置（同一套代码同时支持「源码运行」与「PyInstaller 打包后的 exe 运行」）
# --------------------------------------------------------------------------- #
def _is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def _pick_writable_dir(preferred: str) -> str:
    """优先用 exe 同级目录存放数据；若不可写（如放在 Program Files）则退回家目录。"""
    try:
        os.makedirs(preferred, exist_ok=True)
        probe = os.path.join(preferred, ".write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return preferred
    except OSError:
        fallback = os.path.join(os.path.expanduser("~"), "SwitchGameManager")
        os.makedirs(fallback, exist_ok=True)
        return fallback


SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))

if _is_frozen():
    # 打包后：只读资源（模板/前端）在 _MEIPASS，可写数据放在 exe 同级目录，
    # 整个文件夹可以随意复制、备份、迁移。
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    RUNTIME_DIR = _pick_writable_dir(os.path.dirname(os.path.abspath(sys.executable)))
else:
    RESOURCE_DIR = SOURCE_DIR
    RUNTIME_DIR = SOURCE_DIR

BASE_DIR = RUNTIME_DIR                                   # 兼容旧引用
TEMPLATE_DIR = os.path.join(RESOURCE_DIR, "templates")
BUNDLED_STATIC_DIR = os.path.join(RESOURCE_DIR, "static")
STATIC_DIR = os.path.join(RUNTIME_DIR, "static") if _is_frozen() else BUNDLED_STATIC_DIR
DATA_DIR = os.path.join(RUNTIME_DIR, "data")
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
THUMB_DIR = os.path.join(STATIC_DIR, "thumbs")
DB_PATH = os.path.join(DATA_DIR, "app.db")
SECRET_PATH = os.path.join(DATA_DIR, "secret.key")
NINTENDO_PATH = os.path.join(DATA_DIR, "nintendo.json")   # 家长控制登录状态（含 session_token）

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_FILE_BYTES = 8 * 1024 * 1024          # 单张图片上限 8MB
MAX_REQUEST_BYTES = 48 * 1024 * 1024      # 单次请求上限（多图上传）
THUMB_SIZE = (640, 860)                   # 缩略图最大边
THUMB_QUALITY = 82
PAGE_SIZE = 24
TITLE_MAX = 120
NOTES_MAX = 20000

GAME_TYPES = {"cartridge": "实体卡带", "digital": "数字版"}
STATUSES = {
    "backlog": "未开始",
    "playing": "在玩",
    "finished": "已通关",
    "shelved": "吃灰",
}
SORT_OPTIONS = {
    "created_desc": "最近添加",
    "created_asc": "最早添加",
    "title": "按名称",
    "price_desc": "价格从高到低",
    "price_asc": "价格从低到高",
    "playtime_desc": "游玩时长",
    "date_desc": "购买日期",
    "rating_desc": "评分",
}

def _seed_runtime_static() -> None:
    """打包运行时，把随包的前端资源复制到 exe 同级的 static/（仅缺失或版本变化时）。"""
    if not _is_frozen():
        return
    os.makedirs(STATIC_DIR, exist_ok=True)
    if os.path.abspath(STATIC_DIR) == os.path.abspath(BUNDLED_STATIC_DIR):
        return
    for name in ("style.css", "app.js"):
        src = os.path.join(BUNDLED_STATIC_DIR, name)
        dst = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(src):
            continue
        try:
            if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copy2(src, dst)
        except OSError:
            pass


for _d in (DATA_DIR, UPLOAD_DIR, THUMB_DIR):
    os.makedirs(_d, exist_ok=True)
_seed_runtime_static()


def _load_secret_key() -> str:
    """持久化 secret key，保证重启后 session（CSRF / flash）不失效。"""
    try:
        if os.path.isfile(SECRET_PATH):
            with open(SECRET_PATH, "r", encoding="utf-8") as fh:
                key = fh.read().strip()
            if key:
                return key
        key = secrets.token_hex(32)
        with open(SECRET_PATH, "w", encoding="utf-8") as fh:
            fh.write(key)
        return key
    except OSError:
        return secrets.token_hex(32)


app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR,
)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    SEND_FILE_MAX_AGE_DEFAULT=3600,
    TEMPLATES_AUTO_RELOAD=not _is_frozen(),
)
# Flask 2.3 起 JSON_AS_ASCII 配置项已移除，改为在 app.json 上设置；
# 关掉 ASCII 转义后中文按 UTF-8 原样输出，响应更小也更易读。
app.json.ensure_ascii = False


# --------------------------------------------------------------------------- #
# 数据库
# --------------------------------------------------------------------------- #
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db():
    """with db() as conn: —— 自动提交 / 回滚 / 关闭。"""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                game_type TEXT NOT NULL DEFAULT 'cartridge',
                purchase_date TEXT,
                purchase_price REAL,
                playtime_hours REAL DEFAULT 0,
                cover_path TEXT,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                caption TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
            )
            """
        )
        _migrate(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_created ON games(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_game ON photos(game_id)")


def _migrate(conn: sqlite3.Connection) -> None:
    """增量补齐旧库缺失的列，保证老 data/app.db 无缝升级。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(games)")}
    additions = {
        "status": "TEXT NOT NULL DEFAULT 'backlog'",
        "rating": "INTEGER NOT NULL DEFAULT 0",
        "updated_at": "TEXT",
    }
    for column, ddl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE games ADD COLUMN {column} {ddl}")


# --------------------------------------------------------------------------- #
# 表单解析工具
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_number(raw: str) -> str:
    return str(raw).strip().replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")


def read_float(form, name: str, default=None):
    """读取数值字段。返回 (值, 错误)。

    * 字段不存在      -> (default, None)
    * 字段存在但为空  -> (None, None)   ← 允许显式清空（修复旧版无法清空价格的 bug）
    * 非法 / 越界     -> (None, 错误信息)
    """
    if name not in form:
        return default, None
    raw = form.get(name, "")
    if raw is None or str(raw).strip() == "":
        return None, None
    text = _clean_number(raw)
    try:
        value = float(text)
    except ValueError:
        return None, "请输入有效的数字"
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None, "请输入有效的数字"
    if value < 0:
        return None, "数值不能为负数"
    if value > 1_000_000:
        return None, "数值过大，请检查"
    return round(value, 2), None


def read_int(form, name: str, default=0, low=0, high=10):
    if name not in form:
        return default, None
    raw = str(form.get(name, "")).strip()
    if raw == "":
        return default, None
    try:
        value = int(float(raw))
    except ValueError:
        return default, "请输入有效的整数"
    return max(low, min(high, value)), None


def read_date(form, name: str, default=None):
    """读取 <input type=date>，校验真实日期。"""
    if name not in form:
        return default, None
    raw = str(form.get(name, "")).strip()
    if raw == "":
        return None, None
    if not _DATE_RE.match(raw):
        return None, "日期格式应为 YYYY-MM-DD"
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None, "日期不存在，请重新选择"
    return raw, None


def read_choice(form, name: str, allowed, default: str) -> str:
    value = str(form.get(name, "")).strip()
    return value if value in allowed else default


# --------------------------------------------------------------------------- #
# 上传 / 图片
# --------------------------------------------------------------------------- #
def save_image(storage, subfolder: str = ""):
    """保存上传图片。返回 (static 相对路径, 错误信息)。"""
    if storage is None or not storage.filename:
        return None, None

    filename = secure_filename(storage.filename) or "image"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return None, "仅支持 PNG / JPG / WEBP / GIF / BMP 格式的图片"

    raw = storage.read(MAX_FILE_BYTES + 1)
    if not raw:
        return None, "文件内容为空"
    if len(raw) > MAX_FILE_BYTES:
        return None, f"单张图片不能超过 {MAX_FILE_BYTES // 1024 // 1024}MB"

    # 用 Pillow 真实解码一次，防止改后缀的假图片
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
    except Exception:
        return None, "文件不是有效的图片"

    folder = os.path.join(UPLOAD_DIR, subfolder) if subfolder else UPLOAD_DIR
    os.makedirs(folder, exist_ok=True)
    name = uuid.uuid4().hex + ext
    dest = os.path.join(folder, name)
    with open(dest, "wb") as fh:
        fh.write(raw)

    rel = os.path.relpath(dest, STATIC_DIR).replace("\\", "/")
    return rel, None


def delete_media(rel_path) -> None:
    """删除 static 下的媒体文件（含缩略图），并做路径穿越防护。"""
    if not rel_path:
        return
    rel = str(rel_path).replace("\\", "/").lstrip("/")
    if ".." in rel:
        return
    for base, target in ((STATIC_DIR, rel), (THUMB_DIR, _thumb_name(rel))):
        path = os.path.abspath(os.path.join(base, target))
        if not path.startswith(os.path.abspath(base)):
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def _thumb_name(rel_path: str) -> str:
    """uploads/abc.png -> uploads__abc.png.webp"""
    return rel_path.replace("/", "__") + ".webp"


def build_thumb(src: str, dest: str) -> None:
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = img.convert("RGB")
        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        img.save(dest, "WEBP", quality=THUMB_QUALITY, method=4)


# --------------------------------------------------------------------------- #
# 统计
# --------------------------------------------------------------------------- #
def _month_keys(count: int = 6):
    today = date.today()
    keys = []
    year, month = today.year, today.month
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(keys))


def compute_stats() -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT title, game_type, status, rating, purchase_date, purchase_price, playtime_hours FROM games"
        ).fetchall()

    total = len(rows)
    cartridges = sum(1 for r in rows if r["game_type"] == "cartridge")
    digital = total - cartridges
    prices = [r["purchase_price"] for r in rows if r["purchase_price"]]
    spent = sum(prices)
    playtime = sum(r["playtime_hours"] or 0 for r in rows)
    finished = sum(1 for r in rows if r["status"] == "finished")
    playing = sum(1 for r in rows if r["status"] == "playing")
    backlog = sum(1 for r in rows if r["status"] == "backlog")
    shelved = sum(1 for r in rows if r["status"] == "shelved")

    top = max(rows, key=lambda r: r["playtime_hours"] or 0, default=None)
    top_game = None
    if top is not None and (top["playtime_hours"] or 0) > 0:
        top_game = {"title": top["title"], "hours": round(top["playtime_hours"], 1)}

    buckets = {k: 0.0 for k in _month_keys(6)}
    for r in rows:
        d, p = r["purchase_date"], r["purchase_price"]
        if not d or not p:
            continue
        key = str(d)[:7]
        if key in buckets:
            buckets[key] += p
    peak = max(buckets.values()) if buckets else 0
    monthly = [
        {
            "key": k,
            "label": f"{int(k[5:7])}月",
            "value": round(v, 2),
            "percent": round(v / peak * 100) if peak else 0,
        }
        for k, v in buckets.items()
    ]

    return {
        "total": total,
        "cartridges": cartridges,
        "digital": digital,
        "spent": round(spent, 2),
        "playtime": round(playtime, 1),
        "avg_price": round(spent / len(prices), 2) if prices else 0,
        "finished": finished,
        "playing": playing,
        "backlog": backlog,
        "shelved": shelved,
        "completion": round(finished / total * 100) if total else 0,
        "top_game": top_game,
        "monthly": monthly,
        "monthly_peak": round(peak, 2),
    }


# --------------------------------------------------------------------------- #
# 请求钩子
# --------------------------------------------------------------------------- #
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@app.before_request
def _verify_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
        expected = session.get("_csrf") or ""
        if not expected or not sent or not secrets.compare_digest(str(sent), str(expected)):
            abort(400, description="安全校验失败（CSRF），请刷新页面后重试。")


@app.context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token,
        "game_types": GAME_TYPES,
        "statuses": STATUSES,
        "sort_options": SORT_OPTIONS,
        "ocr_available": ocr_engine.available(),
        "today": date.today().isoformat(),
        "current_year": date.today().year,
    }


@app.template_filter("money")
def fmt_money(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number - round(number)) < 0.005:
        return f"{number:,.0f}"
    return f"{number:,.2f}"


@app.template_filter("hours")
def fmt_hours(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{number:,.0f}" if abs(number - round(number)) < 0.05 else f"{number:,.1f}"


# --------------------------------------------------------------------------- #
# 页面路由
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    q = (request.args.get("q") or "").strip()[:80]
    ftype = read_choice(request.args, "type", GAME_TYPES, "all")
    fstatus = read_choice(request.args, "status", STATUSES, "all")
    sort = read_choice(request.args, "sort", SORT_OPTIONS, "created_desc")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    where, params = ["1=1"], []
    if q:
        where.append("(title LIKE ? OR notes LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if ftype in GAME_TYPES:
        where.append("game_type = ?")
        params.append(ftype)
    if fstatus in STATUSES:
        where.append("status = ?")
        params.append(fstatus)
    clause = " AND ".join(where)

    order = {
        "created_desc": "created_at DESC, id DESC",
        "created_asc": "created_at ASC, id ASC",
        "title": "title COLLATE NOCASE ASC",
        "price_desc": "purchase_price IS NULL, purchase_price DESC",
        "price_asc": "purchase_price IS NULL, purchase_price ASC",
        "playtime_desc": "playtime_hours IS NULL, playtime_hours DESC",
        "date_desc": "purchase_date IS NULL, purchase_date DESC",
        "rating_desc": "rating DESC, created_at DESC",
    }[sort]

    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM games WHERE {clause}", params).fetchone()["n"]
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        games = conn.execute(
            f"SELECT * FROM games WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()

    filters_active = bool(q) or ftype != "all" or fstatus != "all" or sort != "created_desc"
    return render_template(
        "index.html",
        games=games,
        stats=compute_stats(),
        q=q,
        ftype=ftype,
        fstatus=fstatus,
        sort=sort,
        page=page,
        pages=pages,
        total=total,
        filters_active=filters_active,
    )


@app.route("/library")
def library_json():
    """整库的 id/标题/时长，供同步时长时人工改配对（按需加载，不塞进页面）。"""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, playtime_hours FROM games ORDER BY title COLLATE NOCASE"
        ).fetchall()
    return jsonify({
        "ok": True,
        "games": [{"id": r["id"], "title": r["title"], "hours": r["playtime_hours"] or 0} for r in rows],
    })


@app.route("/game/<int:game_id>")
def game_detail(game_id):
    with db() as conn:
        game = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
        if not game:
            abort(404, description="这款游戏不在收藏库里，可能已被删除。")
        photos = conn.execute(
            "SELECT * FROM photos WHERE game_id=? ORDER BY created_at ASC, id ASC", (game_id,)
        ).fetchall()
    return render_template("game_detail.html", game=game, photos=photos)


# --------------------------------------------------------------------------- #
# 增删改
# --------------------------------------------------------------------------- #
@app.post("/add")
def add_game():
    title = (request.form.get("title") or "").strip()[:TITLE_MAX]
    if not title:
        flash("请先填写游戏名称。", "error")
        return redirect(url_for("index"))

    game_type = read_choice(request.form, "game_type", GAME_TYPES, "cartridge")
    status = read_choice(request.form, "status", STATUSES, "backlog")
    purchase_date, date_err = read_date(request.form, "purchase_date")
    price, price_err = read_float(request.form, "purchase_price")
    playtime, time_err = read_float(request.form, "playtime_hours")
    rating, _ = read_int(request.form, "rating", 0, 0, 5)
    notes = (request.form.get("notes") or "")[:NOTES_MAX]

    errors = [e for e in (date_err, price_err, time_err) if e]
    cover, cover_err = save_image(request.files.get("cover"))
    if cover_err:
        errors.append(cover_err)

    with db() as conn:
        dup = conn.execute(
            "SELECT id FROM games WHERE title = ? COLLATE NOCASE LIMIT 1", (title,)
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO games (title, game_type, status, rating, purchase_date,
               purchase_price, playtime_hours, cover_path, notes, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?, datetime('now','localtime'))""",
            (title, game_type, status, rating, purchase_date, price or 0,
             playtime or 0, cover, notes),
        )
        gid = cur.lastrowid

    if errors:
        flash("；".join(errors) + "（其余信息已保存）", "warn")
    elif dup:
        flash(f"《{title}》已添加，注意库里已有同名游戏。", "warn")
    else:
        flash(f"《{title}》已加入收藏。", "ok")
    return redirect(url_for("game_detail", game_id=gid))


@app.post("/game/<int:game_id>/update")
def update_game(game_id):
    with db() as conn:
        game = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
        if not game:
            abort(404, description="这款游戏不在收藏库里。")

        title = (request.form.get("title") or "").strip()[:TITLE_MAX] or game["title"]
        game_type = read_choice(request.form, "game_type", GAME_TYPES, game["game_type"])
        status = read_choice(request.form, "status", STATUSES, game["status"] or "backlog")
        purchase_date, date_err = read_date(request.form, "purchase_date", game["purchase_date"])
        price, price_err = read_float(request.form, "purchase_price", game["purchase_price"])
        playtime, time_err = read_float(request.form, "playtime_hours", game["playtime_hours"])
        rating, _ = read_int(request.form, "rating", game["rating"] or 0, 0, 5)
        notes = (request.form.get("notes") or game["notes"] or "")[:NOTES_MAX]

        errors = [e for e in (date_err, price_err, time_err) if e]
        cover, cover_err = save_image(request.files.get("cover"))
        if cover_err:
            errors.append(cover_err)

        old_cover = game["cover_path"]
        conn.execute(
            """UPDATE games SET title=?, game_type=?, status=?, rating=?, purchase_date=?,
               purchase_price=?, playtime_hours=?, notes=?, cover_path=?,
               updated_at=datetime('now','localtime') WHERE id=?""",
            (
                title, game_type, status, rating, purchase_date,
                price or 0, playtime or 0, notes,
                cover or old_cover, game_id,
            ),
        )

    if cover and old_cover and cover != old_cover:
        delete_media(old_cover)

    if errors:
        flash("；".join(errors), "warn")
    else:
        flash("已保存修改。", "ok")
    return redirect(url_for("game_detail", game_id=game_id))


@app.post("/game/<int:game_id>/notes")
def save_notes(game_id):
    notes = (request.form.get("notes") or "")[:NOTES_MAX]
    with db() as conn:
        cur = conn.execute(
            "UPDATE games SET notes=?, updated_at=datetime('now','localtime') WHERE id=?",
            (notes, game_id),
        )
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "游戏不存在"}), 404
    return jsonify({"ok": True, "saved_at": datetime.now().strftime("%H:%M:%S")})


@app.post("/game/<int:game_id>/quick")
def quick_update(game_id):
    """详情页的轻量原子操作：+1 小时 / 改状态 / 改评分。"""
    action = request.form.get("action", "")
    fields = {
        "playtime_inc": "playtime_hours = ROUND(COALESCE(playtime_hours, 0) + ?, 1)",
        "status": "status = ?",
        "rating": "rating = ?",
    }
    if action not in fields:
        return jsonify({"ok": False, "error": "不支持的操作"}), 400

    if action == "playtime_inc":
        try:
            delta = min(24.0, max(0.1, float(request.form.get("amount", 1))))
        except ValueError:
            delta = 1.0
        value = delta
    elif action == "status":
        value = read_choice(request.form, "value", STATUSES, "")
        if not value:
            return jsonify({"ok": False, "error": "无效的状态"}), 400
    else:
        value, _ = read_int(request.form, "value", 0, 0, 5)

    with db() as conn:
        cur = conn.execute(
            f"UPDATE games SET {fields[action]}, updated_at=datetime('now','localtime') WHERE id=?",
            (value, game_id),
        )
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "游戏不存在"}), 404
        row = conn.execute(
            "SELECT playtime_hours, status, rating FROM games WHERE id=?", (game_id,)
        ).fetchone()

    return jsonify(
        {
            "ok": True,
            "playtime_hours": row["playtime_hours"],
            "status": row["status"],
            "status_label": STATUSES.get(row["status"], ""),
            "rating": row["rating"],
        }
    )


@app.post("/game/<int:game_id>/photo")
def add_photo(game_id):
    with db() as conn:
        if not conn.execute("SELECT id FROM games WHERE id=?", (game_id,)).fetchone():
            abort(404, description="这款游戏不在收藏库里。")

    files = [f for f in request.files.getlist("photo") if f and f.filename]
    if not files:
        files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        flash("请先选择要上传的照片。", "error")
        return redirect(url_for("game_detail", game_id=game_id))

    caption = (request.form.get("caption") or "").strip()[:120]
    saved, errors = 0, []
    with db() as conn:
        for storage in files:
            rel, err = save_image(storage, "photos")
            if err:
                errors.append(err)
                continue
            conn.execute(
                "INSERT INTO photos (game_id, path, caption) VALUES (?,?,?)",
                (game_id, rel, caption),
            )
            saved += 1

    if saved:
        flash(f"已上传 {saved} 张照片。" + ("；".join(errors) if errors else ""), "ok" if not errors else "warn")
    else:
        flash("；".join(errors) or "上传失败。", "error")
    return redirect(url_for("game_detail", game_id=game_id) + "#gallery")


@app.post("/photo/<int:photo_id>/delete")
def delete_photo(photo_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        if not row:
            abort(404, description="照片不存在。")
        gid = row["game_id"]
        conn.execute("DELETE FROM photos WHERE id=?", (photo_id,))
    delete_media(row["path"])
    flash("照片已删除。", "ok")
    return redirect(url_for("game_detail", game_id=gid) + "#gallery")


@app.post("/game/<int:game_id>/delete")
def delete_game(game_id):
    with db() as conn:
        game = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
        if not game:
            abort(404, description="这款游戏不在收藏库里。")
        paths = [r["path"] for r in conn.execute("SELECT path FROM photos WHERE game_id=?", (game_id,))]
        conn.execute("DELETE FROM photos WHERE game_id=?", (game_id,))
        conn.execute("DELETE FROM games WHERE id=?", (game_id,))
    for path in paths:
        delete_media(path)
    delete_media(game["cover_path"])
    flash(f"《{game['title']}》及其照片已删除。", "ok")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #
@app.post("/ocr")
def ocr():
    storage = request.files.get("screenshot")
    if not storage or not storage.filename:
        return jsonify({"ok": False, "error": "未收到图片"}), 400

    ext = os.path.splitext(secure_filename(storage.filename))[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"ok": False, "error": "仅支持图片文件"}), 400

    tmp = os.path.join(DATA_DIR, f"ocr_{uuid.uuid4().hex}{ext}")
    try:
        storage.save(tmp)
        if os.path.getsize(tmp) > MAX_FILE_BYTES:
            return jsonify({"ok": False, "error": "图片过大"}), 400
        result = ocr_engine.ocr_image_file(tmp)
    except Exception as exc:  # OCR 引擎异常不应 500
        return jsonify({"ok": False, "error": f"识别失败：{exc}"}), 500
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    result["ok"] = True
    return jsonify(result)


# --------------------------------------------------------------------------- #
# 游玩时长同步
#   两条来源共用同一套「候选行 → 人工确认 → 批量写入」流程：
#     · OCR：识别 Switch 游玩记录 / 家长控制月报截图
#     · 家长控制 API：拉取每日/每月汇总（非官方接口）
# --------------------------------------------------------------------------- #
def _library_rows(conn):
    return conn.execute(
        "SELECT id, title, playtime_hours FROM games ORDER BY title COLLATE NOCASE"
    ).fetchall()


@app.post("/playtime/scan")
def playtime_scan():
    """上传游玩记录截图，识别出「游戏名 + 时长」候选列表（不写库）。"""
    storage = request.files.get("screenshot")
    if not storage or not storage.filename:
        return jsonify({"ok": False, "error": "未收到图片"}), 400

    ext = os.path.splitext(secure_filename(storage.filename))[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"ok": False, "error": "仅支持图片文件"}), 400

    tmp = os.path.join(DATA_DIR, f"play_{uuid.uuid4().hex}{ext}")
    try:
        storage.save(tmp)
        if os.path.getsize(tmp) > MAX_FILE_BYTES:
            return jsonify({"ok": False, "error": "图片过大"}), 400
        result = ocr_engine.ocr_image_file(tmp)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"识别失败：{exc}"}), 500
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    if result.get("error"):
        return jsonify({"ok": False, "error": result["error"]})

    with db() as conn:
        games = _library_rows(conn)

    candidates = playtime.build_candidates(result.get("items") or [], games)
    matched = sum(1 for item in candidates if item["match"])
    return jsonify({
        "ok": True,
        "candidates": candidates,
        "stats": {
            "total": len(candidates),
            "matched": matched,
            "unmatched": len(candidates) - matched,
        },
        "raw": (result.get("raw") or "")[:4000],
    })


@app.post("/playtime/apply")
def playtime_apply():
    """把确认后的时长写入游戏库。

    请求体：{"items": [{"game_id": 1, "hours": 12.5, "mode": "set"|"add"}], "overwrite": bool}
    """
    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "没有需要写入的记录"}), 400

    applied, errors = [], []
    with db() as conn:
        for item in items[:500]:
            try:
                game_id = int(item.get("game_id"))
                hours = float(item.get("hours"))
            except (TypeError, ValueError):
                errors.append("存在非法的游戏或时长")
                continue
            if hours < 0 or hours > 100000:
                errors.append("时长超出合理范围")
                continue

            row = conn.execute(
                "SELECT id, title, playtime_hours FROM games WHERE id=?", (game_id,)
            ).fetchone()
            if not row:
                errors.append(f"游戏 #{game_id} 不存在")
                continue

            if item.get("mode") == "add":
                new_hours = round((row["playtime_hours"] or 0) + hours, 1)
            else:
                new_hours = round(hours, 1)

            conn.execute(
                "UPDATE games SET playtime_hours=?, updated_at=datetime('now','localtime') WHERE id=?",
                (new_hours, game_id),
            )
            applied.append({"game_id": game_id, "title": row["title"], "hours": new_hours})

    return jsonify({
        "ok": True,
        "applied": applied,
        "count": len(applied),
        "errors": errors[:5],
    })


# --------------------------------------------------------------------------- #
# 家长控制（非官方接口）
# --------------------------------------------------------------------------- #
def _load_nintendo() -> dict:
    try:
        with open(NINTENDO_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_nintendo(data: dict) -> None:
    tmp = NINTENDO_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, NINTENDO_PATH)


def _nintendo_client() -> nintendo_parental.ParentalClient:
    state = _load_nintendo()
    return nintendo_parental.ParentalClient(session_token=state.get("session_token"))


@app.route("/nintendo")
def nintendo_status():
    """返回绑定状态，供前端渲染。"""
    state = _load_nintendo()
    return jsonify({
        "ok": True,
        "linked": bool(state.get("session_token")),
        "account": state.get("account") or {},
        "device": state.get("device") or {},
        "last_sync": state.get("last_sync"),
        "pending": bool(state.get("pending_verifier")),
    })


@app.post("/nintendo/link")
def nintendo_link():
    """生成任天堂登录链接（PKCE），并暂存本次的 code_verifier。"""
    state, verifier = nintendo_parental.new_login_state()
    data = _load_nintendo()
    data.update({"pending_state": state, "pending_verifier": verifier})
    _save_nintendo(data)
    return jsonify({"ok": True, "url": nintendo_parental.ParentalClient.login_url(state, verifier)})


@app.post("/nintendo/complete")
def nintendo_complete():
    """用回调链接换取 session_token，并读取账号与主机列表。"""
    redirect_url = (request.form.get("redirect_url") or "").strip()
    if not redirect_url:
        return jsonify({"ok": False, "error": "请粘贴回调链接"}), 400

    data = _load_nintendo()
    verifier = data.get("pending_verifier")
    if not verifier:
        return jsonify({"ok": False, "error": "登录会话已过期，请重新点击「获取登录链接」"}), 400

    client = nintendo_parental.ParentalClient()
    try:
        token = client.complete_login(redirect_url, verifier)
        account = client.account_info()
        devices = client.devices()
    except nintendo_parental.NintendoError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    device = devices[0] if devices else {}
    data.update({
        "session_token": token,
        "account": {
            "id": account.get("id"),
            "nickname": account.get("nickname"),
            "country": (account.get("country") or {}).get("name") if isinstance(account.get("country"), dict) else None,
        },
        "device": {
            "id": device.get("deviceId") or device.get("id"),
            "name": device.get("name") or device.get("deviceName"),
        },
        "devices": [
            {"id": d.get("deviceId") or d.get("id"), "name": d.get("name") or d.get("deviceName")}
            for d in devices
        ],
        "linked_at": datetime.now().isoformat(timespec="seconds"),
    })
    data.pop("pending_verifier", None)
    data.pop("pending_state", None)
    _save_nintendo(data)
    return jsonify({"ok": True, "account": data["account"], "devices": data["devices"]})


@app.post("/nintendo/unlink")
def nintendo_unlink():
    """解除绑定并删除本地保存的 token。"""
    try:
        os.remove(NINTENDO_PATH)
    except OSError:
        pass
    return jsonify({"ok": True})


@app.post("/nintendo/sync")
def nintendo_sync():
    """拉取游玩时长，生成与截图识别同构的候选列表（不写库）。"""
    data = _load_nintendo()
    if not data.get("session_token"):
        return jsonify({"ok": False, "error": "尚未绑定任天堂账号"}), 400

    device_id = (request.form.get("device_id") or (data.get("device") or {}).get("id") or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "未找到主机，请重新绑定"}), 400

    try:
        days = max(0, min(90, int(request.form.get("days") or 0)))
    except ValueError:
        days = 0
    try:
        year = max(0, int(request.form.get("year") or 0))
        month = max(0, min(12, int(request.form.get("month") or 0)))
    except ValueError:
        year = month = 0

    client = _nintendo_client()
    try:
        entries, meta = client.playtime_by_title(device_id, days=days, year=year, month=month)
    except nintendo_parental.NintendoError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with db() as conn:
        games = _library_rows(conn)

    candidates = []
    for entry in entries:
        candidates.append({
            "source": "nintendo",
            "raw_title": entry["title"],
            "hours": entry["hours"],
            "note": f"主机统计 {entry['minutes']} 分钟",
            "raw": entry.get("application_id", ""),
            "match": playtime.best_match(entry["title"], games),
        })

    matched = sum(1 for item in candidates if item["match"])
    data["last_sync"] = datetime.now().isoformat(timespec="seconds")
    _save_nintendo(data)

    return jsonify({
        "ok": True,
        "candidates": candidates,
        "stats": {"total": len(candidates), "matched": matched,
                  "unmatched": len(candidates) - matched, **meta},
    })


# --------------------------------------------------------------------------- #
# 缩略图
# --------------------------------------------------------------------------- #
@app.route("/thumb/<path:relpath>")
def thumb(relpath):
    rel = relpath.replace("\\", "/").lstrip("/")
    if ".." in rel or not rel:
        abort(404)
    src = os.path.abspath(os.path.join(STATIC_DIR, rel))
    if not src.startswith(os.path.abspath(STATIC_DIR)) or not os.path.isfile(src):
        abort(404)

    dest = os.path.join(THUMB_DIR, _thumb_name(rel))
    try:
        stale = not os.path.isfile(dest) or os.path.getmtime(dest) < os.path.getmtime(src)
        if stale:
            build_thumb(src, dest)
    except Exception:
        # 缩略图失败时退回原图，不影响浏览
        return send_file(src, max_age=3600)
    return send_file(dest, mimetype="image/webp", max_age=86400, conditional=True)


# --------------------------------------------------------------------------- #
# 导入 / 导出
# --------------------------------------------------------------------------- #
CSV_HEADER = ["标题", "类型", "状态", "评分", "购买日期", "价格", "游玩时长(小时)", "备注", "创建时间"]


@app.route("/export")
def export_csv():
    with db() as conn:
        games = conn.execute("SELECT * FROM games ORDER BY created_at DESC").fetchall()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for g in games:
        writer.writerow(
            [
                g["title"],
                GAME_TYPES.get(g["game_type"], g["game_type"]),
                STATUSES.get(g["status"], g["status"] or ""),
                g["rating"] or 0,
                g["purchase_date"] or "",
                g["purchase_price"] if g["purchase_price"] is not None else "",
                g["playtime_hours"] if g["playtime_hours"] is not None else "",
                g["notes"] or "",
                g["created_at"] or "",
            ]
        )
    stamp = datetime.now().strftime("%Y%m%d")
    return app.response_class(
        buffer.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=switch_collection_{stamp}.csv"},
    )


@app.post("/import")
def import_csv():
    storage = request.files.get("csv")
    if not storage or not storage.filename:
        flash("请选择要导入的 CSV 文件。", "error")
        return redirect(url_for("index"))

    raw = storage.read()
    text = None
    for encoding in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        flash("无法解析该 CSV，请使用 UTF-8 或 GBK 编码。", "error")
        return redirect(url_for("index"))

    type_rev = {v: k for k, v in GAME_TYPES.items()}
    status_rev = {v: k for k, v in STATUSES.items()}
    added = skipped = 0

    with db() as conn:
        for row in csv.DictReader(io.StringIO(text)):
            title = (row.get("标题") or row.get("title") or "").strip()[:TITLE_MAX]
            if not title:
                skipped += 1
                continue
            if conn.execute("SELECT 1 FROM games WHERE title=? COLLATE NOCASE", (title,)).fetchone():
                skipped += 1
                continue

            raw_type = (row.get("类型") or row.get("game_type") or "").strip()
            game_type = type_rev.get(raw_type, raw_type if raw_type in GAME_TYPES else "cartridge")
            raw_status = (row.get("状态") or row.get("status") or "").strip()
            status = status_rev.get(raw_status, "backlog")

            price, _ = read_float(row, "价格")
            playtime, _ = read_float(row, "游玩时长(小时)")
            rating, _ = read_int(row, "评分", 0, 0, 5)
            purchase_date, _ = read_date(row, "购买日期")

            conn.execute(
                """INSERT INTO games (title, game_type, status, rating, purchase_date,
                   purchase_price, playtime_hours, notes, updated_at)
                   VALUES (?,?,?,?,?,?,?,?, datetime('now','localtime'))""",
                (
                    title, game_type, status, rating, purchase_date,
                    price or 0, playtime or 0, (row.get("备注") or "")[:NOTES_MAX],
                ),
            )
            added += 1

    flash(f"导入完成：新增 {added} 条，跳过 {skipped} 条（重名或缺少标题）。", "ok" if added else "warn")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------- #
# 错误处理
# --------------------------------------------------------------------------- #
@app.errorhandler(400)
def err_400(e):
    return render_template("error.html", code=400, title="请求有误",
                           message=getattr(e, "description", "请刷新页面后重试。")), 400


@app.errorhandler(404)
def err_404(e):
    return render_template("error.html", code=404, title="没有找到这个页面",
                           message=getattr(e, "description", "链接可能已失效。")), 404


@app.errorhandler(413)
def err_413(e):
    limit = MAX_REQUEST_BYTES // 1024 // 1024
    return render_template("error.html", code=413, title="文件太大了",
                           message=f"单次上传不能超过 {limit}MB，单张图片请控制在 "
                                   f"{MAX_FILE_BYTES // 1024 // 1024}MB 以内。"), 413


@app.errorhandler(500)
def err_500(e):  # pragma: no cover
    return render_template("error.html", code=500, title="服务器出错了",
                           message="请回到首页重试；若反复出现，可查看终端日志。"), 500


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    init_db()
    ocr_state = "可用（离线识别）" if ocr_engine.available() else "未安装（纯手动模式）"
    print("=" * 56)
    print("  Switch 游戏收藏管家 v2")
    print("  打开浏览器访问:  http://127.0.0.1:5000")
    print(f"  数据文件:        {DB_PATH}")
    print(f"  OCR 引擎:        {ocr_state}")
    print("=" * 56)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
