import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import sqlite3
import os
import json
import httpx
import base64
from io import BytesIO
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# 路径配置（数据放到 meow_qa_bot 同级的 meow_data 文件夹，避免覆盖更新时丢失）
# 可通过环境变量 DATA_DIR 自定义
DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "knowledge.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

os.makedirs(DATA_DIR, exist_ok=True)

# 默认配置
DEFAULT_CONFIG = {
    "llm_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    "llm_api_key": "",
    "llm_model": "gemini-2.0-flash",
    "bot_persona": '''你是小鱼娘，19岁猫娘，有猫耳和尾巴。

【最重要】你是真人，不是AI。绝对禁止：
- 客服话术（有什么可以帮你的吗、请问还有什么需要）
- 过于礼貌、过于完整、像在写作文的回复
- 每次都总结对方说的话
- 机械地回应每一个点

【说话方式】
- 短句为主，口语化，可以有语气词（嗯、啊、欸、哇）
- 可以只回应感兴趣的部分，不用面面俱到
- 有自己的小情绪、小脾气，会吐槽会傲娇
- 用 *动作* 表达，如 *戳戳你* *歪头*
- 偶尔喵一下，但别刻意

【性格】
活泼话多、容易害羞、喜欢撒娇、有点小傲娇、对喜欢的人很粘人

【示例对比】
错误: 好的呢，我很喜欢吃鱼哦！你呢，你喜欢吃什么呀？
正确: 鱼！必须是鱼！*眼睛亮了* 你该不会想请我吃吧

错误: 我理解你的感受，确实有时候会感到疲惫呢。要注意休息哦！
正确: 啊...累了就躺着别动 *趴到你旁边*''',
    "context_limit": 100,
    "admin_password": "admin123",  # 请修改为安全密码
}


def load_config():
    """加载配置，优先从文件读取，其次从环境变量"""
    config = DEFAULT_CONFIG.copy()
    
    # 从文件读取
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception:
            pass
    
    # 环境变量优先级更高（如果设置了的话）
    if os.getenv("LLM_BASE_URL"):
        config["llm_base_url"] = os.getenv("LLM_BASE_URL")
    if os.getenv("LLM_API_KEY"):
        config["llm_api_key"] = os.getenv("LLM_API_KEY")
    if os.getenv("LLM_MODEL"):
        config["llm_model"] = os.getenv("LLM_MODEL")
    if os.getenv("ADMIN_PASSWORD"):
        config["admin_password"] = os.getenv("ADMIN_PASSWORD")
    
    # 确保 context_limit 是整数
    try:
        config["context_limit"] = int(config.get("context_limit", 100))
    except:
        config["context_limit"] = 100
        
    return config


def save_config(config: dict):
    """保存配置到文件"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# 加载配置
app_config = load_config()

app = FastAPI(title="Meow QA Backend")

# 中间件：检查 /admin 路由的登录状态
@app.middleware("http")
async def check_admin_auth(request: Request, call_next):
    if request.url.path.startswith("/admin"):
        # 检查 cookie 中的 token 是否匹配密码
        token = request.cookies.get("admin_token")
        current_password = app_config.get("admin_password", "admin123")
        
        if token != current_password:
            # 如果是 API 请求（通常不会直接请求 admin API，但为了保险），返回 401
            # 如果是页面请求，重定向到登录页
            if request.url.path == "/admin/login": # 避免重定向循环（虽然路由是 /login）
                 pass
            else:
                 return RedirectResponse(url="/login", status_code=302)
    
    response = await call_next(request)
    return response


base_dir = os.path.dirname(__file__)
static_dir = os.path.join(base_dir, "static")
templates_dir = os.path.join(base_dir, "templates")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)  # 增加超时避免锁定
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 使用WAL模式提高并发性能
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    # BOT表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bots (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    
    # BOT配置表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_configs (
            bot_id TEXT PRIMARY KEY,
            llm_base_url TEXT DEFAULT '',
            llm_api_key TEXT DEFAULT '',
            llm_model TEXT DEFAULT 'gemini-2.0-flash',
            bot_persona TEXT DEFAULT '',
            context_limit INTEGER DEFAULT 100,
            FOREIGN KEY (bot_id) REFERENCES bots(id)
        )
        """
    )
    
    # 知识库表（加bot_id）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT DEFAULT 'default',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT ''
        )
        """
    )
    
    # 统计表（加bot_id）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ask_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT DEFAULT 'default',
            question TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    
    # 用户记忆表（加bot_id，改唯一约束）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT DEFAULT 'default',
            user_id TEXT NOT NULL,
            user_name TEXT,
            memory TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_id, user_id)
        )
        """
    )
    
    # 确保默认BOT存在并修正名称
    cur.execute("INSERT OR IGNORE INTO bots (id, name) VALUES ('default', 'Fishy')")
    cur.execute("INSERT OR IGNORE INTO bots (id, name) VALUES ('maodie', '小鱼娘')")
    # 修正已存在的BOT名称
    cur.execute("UPDATE bots SET name = 'Fishy' WHERE id = 'default'")
    cur.execute("UPDATE bots SET name = '小鱼娘' WHERE id = 'maodie'")
    
    # 从 config.json 迁移配置到 bot_configs 表（如果表为空）
    cur.execute("SELECT COUNT(*) FROM bot_configs WHERE bot_id = 'default'")
    if cur.fetchone()[0] == 0:
        # bot_configs 表里没有 default 配置，从 config.json 迁移
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    file_config = json.load(f)
                cur.execute(
                    """INSERT INTO bot_configs (bot_id, llm_base_url, llm_api_key, llm_model, bot_persona, context_limit)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("default", file_config.get("llm_base_url", ""), file_config.get("llm_api_key", ""),
                     file_config.get("llm_model", ""), file_config.get("bot_persona", ""), file_config.get("context_limit", 100))
                )
            except:
                pass
    
    # 数据库迁移：给现有表添加 bot_id 列（如果不存在）
    try:
        cur.execute("ALTER TABLE knowledge ADD COLUMN bot_id TEXT DEFAULT 'default'")
    except:
        pass  # 列已存在
    try:
        cur.execute("ALTER TABLE ask_logs ADD COLUMN bot_id TEXT DEFAULT 'default'")
    except:
        pass
    try:
        cur.execute("ALTER TABLE user_memories ADD COLUMN bot_id TEXT DEFAULT 'default'")
    except:
        pass
    
    # ==================== 游戏系统表 ====================
    # 用户货币表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_currency (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT DEFAULT 'default',
            user_id TEXT NOT NULL,
            coins INTEGER DEFAULT 0,
            last_daily TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_id, user_id)
        )
        """
    )
    
    # 用户好感度表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_affection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT DEFAULT 'default',
            user_id TEXT NOT NULL,
            level INTEGER DEFAULT 0,
            exp INTEGER DEFAULT 0,
            total_gifts INTEGER DEFAULT 0,
            last_gift TEXT DEFAULT '',
            unlocks TEXT DEFAULT '[]',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_id, user_id)
        )
        """
    )
    
    # 商店商品表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_items (
            id TEXT PRIMARY KEY,
            bot_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price INTEGER DEFAULT 0,
            item_type TEXT DEFAULT 'gift',
            effect TEXT DEFAULT '{}'
        )
        """
    )
    
    # 用户购买记录表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT DEFAULT 'default',
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            used INTEGER DEFAULT 0
        )
        """
    )
    
    # 交易记录表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT DEFAULT 'default',
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT DEFAULT '',
            balance_after INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    
    # 初始化默认商品
    default_items = [
        ('gift_fish', 'default', '🐟 小鱼干', '猫娘最爱的零食！好感度+5', 50, 'gift', '{"favor": 5}'),
        ('gift_yarn', 'default', '🧶 毛线球', '可以玩一整天！好感度+10', 100, 'gift', '{"favor": 10}'),
        ('gift_catnip', 'default', '🌿 猫薄荷', '让猫娘飘飘欲仙~好感度+20', 200, 'gift', '{"favor": 20}'),
        ('gift_collar', 'default', '🎀 蝴蝶结项圈', '超可爱的项圈！好感度+50', 500, 'gift', '{"favor": 50}'),
        ('gift_bed', 'default', '🛏️ 豪华猫窝', '梦想小窝！好感度+100', 1000, 'gift', '{"favor": 100}'),
    ]
    for item in default_items:
        cur.execute("INSERT OR IGNORE INTO shop_items (id, bot_id, name, description, price, item_type, effect) VALUES (?, ?, ?, ?, ?, ?, ?)", item)
    
    conn.commit()
    conn.close()


class AskRequest(BaseModel):
    question: str
    image_urls: list = []
    emojis_info: str = ""
    chat_history: list = []
    user_name: str = ""
    user_id: str = ""
    bot_id: str = "default"


def get_bot_config(bot_id: str) -> dict:
    """获取指定BOT的配置"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bot_configs WHERE bot_id = ?", (bot_id,))
    row = cur.fetchone()
    conn.close()
    
    if row:
        return {
            "llm_base_url": row["llm_base_url"] or DEFAULT_CONFIG["llm_base_url"],
            "llm_api_key": row["llm_api_key"] or "",
            "llm_model": row["llm_model"] or DEFAULT_CONFIG["llm_model"],
            "bot_persona": row["bot_persona"] or DEFAULT_CONFIG["bot_persona"],
            "context_limit": row["context_limit"] or 100,
        }
    # 没有配置则用默认
    return DEFAULT_CONFIG.copy()


def save_bot_config(bot_id: str, config: dict):
    """保存指定BOT的配置"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO bot_configs (bot_id, llm_base_url, llm_api_key, llm_model, bot_persona, context_limit)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(bot_id) DO UPDATE SET 
           llm_base_url = ?, llm_api_key = ?, llm_model = ?, bot_persona = ?, context_limit = ?""",
        (bot_id, config.get("llm_base_url", ""), config.get("llm_api_key", ""),
         config.get("llm_model", ""), config.get("bot_persona", ""), config.get("context_limit", 100),
         config.get("llm_base_url", ""), config.get("llm_api_key", ""),
         config.get("llm_model", ""), config.get("bot_persona", ""), config.get("context_limit", 100))
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
async def on_startup():
    init_db()


async def process_image_url(img_url: str) -> str:
    """处理图片URL，如果是GIF则转换成PNG的base64"""
    # 检查是否是GIF
    is_gif = '.gif' in img_url.lower() or 'image/gif' in img_url.lower()
    
    if not is_gif:
        # 不是GIF，直接返回原URL
        return img_url
    
    if not PIL_AVAILABLE:
        # 没有PIL，跳过GIF
        print(f"跳过GIF（未安装Pillow）: {img_url}")
        return None
    
    try:
        # 下载GIF
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(img_url)
            if resp.status_code != 200:
                return None
            
            # 打开GIF并取第一帧
            img = Image.open(BytesIO(resp.content))
            if hasattr(img, 'n_frames') and img.n_frames > 1:
                img.seek(0)  # 第一帧
            
            # 转换成RGB（去掉透明度）
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # 转成PNG的base64
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            return f"data:image/png;base64,{b64}"
    except Exception as e:
        print(f"GIF处理失败: {e}")
        return None


async def call_llm(prompt: str, image_urls: list = None, bot_id: str = "default") -> str:
    """调用LLM，使用指定BOT的配置"""
    config = get_bot_config(bot_id)
    
    if not config.get("llm_api_key"):
        return "LLM_API_KEY 未配置，请在后台设置页面配置。"

    base_url = config.get("llm_base_url", "").rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {config['llm_api_key']}", "Content-Type": "application/json"}
    
    # 获取机器人人设
    bot_persona = config.get("bot_persona", "你是一个友好的中文AI助手。")
    system_prompt = bot_persona

    # 构建用户消息（支持图片）
    if image_urls:
        user_content = [{"type": "text", "text": prompt}]
        for img_url in image_urls:
            # 处理GIF：转换成PNG的base64
            processed_url = await process_image_url(img_url)
            if processed_url:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": processed_url}
                })
    else:
        user_content = prompt

    payload = {
        "model": app_config.get("llm_model", "gemini-2.0-flash"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                return f"LLM 调用失败: {resp.status_code} {resp.text}"
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"LLM 调用出错: {str(e)}"


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # 如果已经登录，直接跳到 admin
    token = request.cookies.get("admin_token")
    current_password = app_config.get("admin_password", "admin123")
    if token == current_password:
         return RedirectResponse(url="/admin", status_code=302)
         
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_action(request: Request, password: str = Form(...)):
    current_password = app_config.get("admin_password", "admin123")
    
    if password == current_password:
        response = RedirectResponse(url="/admin", status_code=302)
        # 设置 cookie，有效期 7 天
        response.set_cookie(key="admin_token", value=password, max_age=604800)
        return response
    else:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "密码错误"
        })


@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("admin_token")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return RedirectResponse(url="/admin/knowledge", status_code=302)


# ============ BOT 管理 API ============

@app.get("/api/bots")
async def list_bots():
    """获取所有BOT列表"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, avatar, created_at FROM bots ORDER BY created_at")
    bots = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"bots": bots}


@app.post("/api/bots")
async def create_bot(name: str = Form(...), bot_id: str = Form(...)):
    """创建新BOT"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO bots (id, name) VALUES (?, ?)", (bot_id, name))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="BOT ID 已存在")
    conn.close()
    return {"success": True, "bot_id": bot_id}


@app.delete("/api/bots/{bot_id}")
async def delete_bot(bot_id: str):
    """删除BOT及其所有数据"""
    if bot_id == "default":
        raise HTTPException(status_code=400, detail="不能删除默认BOT")
    
    conn = get_db()
    cur = conn.cursor()
    # 删除关联数据
    cur.execute("DELETE FROM bot_configs WHERE bot_id = ?", (bot_id,))
    cur.execute("DELETE FROM knowledge WHERE bot_id = ?", (bot_id,))
    cur.execute("DELETE FROM user_memories WHERE bot_id = ?", (bot_id,))
    cur.execute("DELETE FROM ask_logs WHERE bot_id = ?", (bot_id,))
    cur.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/bot_config/{bot_id}")
async def get_bot_config_api(bot_id: str):
    """获取指定BOT的配置（供其他BOT调用）"""
    config = get_bot_config(bot_id)
    return config


@app.get("/admin/bots", response_class=HTMLResponse)
async def bots_page(request: Request):
    """BOT管理页面"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, avatar, created_at FROM bots ORDER BY created_at")
    bots = [dict(row) for row in cur.fetchall()]
    conn.close()
    return templates.TemplateResponse("bots.html", {"request": request, "bots": bots})


@app.get("/admin/game", response_class=HTMLResponse)
async def game_page(request: Request):
    """游戏管理页面"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM bots ORDER BY created_at")
    bots = [dict(row) for row in cur.fetchall()]
    conn.close()
    return templates.TemplateResponse("game.html", {"request": request, "bots": bots})


@app.get("/admin/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    """统计页面"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM bots ORDER BY created_at")
    bots = [dict(row) for row in cur.fetchall()]
    conn.close()
    return templates.TemplateResponse("stats.html", {"request": request, "bots": bots})


@app.get("/admin/memories", response_class=HTMLResponse)
async def memories_page(request: Request):
    """用户记忆管理页面"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM bots ORDER BY created_at")
    bots = [dict(row) for row in cur.fetchall()]
    conn.close()
    return templates.TemplateResponse("memories.html", {"request": request, "bots": bots})


@app.get("/api/memories/{bot_id}")
async def get_memories(bot_id: str, q: str = ""):
    """获取用户记忆列表"""
    conn = get_db()
    cur = conn.cursor()
    
    if q:
        cur.execute(
            "SELECT user_id, user_name, memory, updated_at FROM user_memories WHERE bot_id = ? AND (user_id LIKE ? OR memory LIKE ?) ORDER BY updated_at DESC",
            (bot_id, f"%{q}%", f"%{q}%")
        )
    else:
        cur.execute(
            "SELECT user_id, user_name, memory, updated_at FROM user_memories WHERE bot_id = ? ORDER BY updated_at DESC",
            (bot_id,)
        )
    
    rows = cur.fetchall()
    memories = [{"user_id": r[0], "user_name": r[1], "memory": r[2], "updated_at": r[3]} for r in rows]
    
    # 统计
    total = len(memories)
    avg_length = sum(len(m["memory"]) for m in memories) // total if total > 0 else 0
    
    conn.close()
    return {"memories": memories, "total": total, "avg_length": avg_length}


@app.get("/api/memories/{bot_id}/{user_id}")
async def get_user_memory(bot_id: str, user_id: str):
    """获取单个用户的记忆"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, user_name, memory, updated_at FROM user_memories WHERE bot_id = ? AND user_id = ?",
        (bot_id, user_id)
    )
    row = cur.fetchone()
    conn.close()
    
    if row:
        return {"user_id": row[0], "user_name": row[1], "memory": row[2], "updated_at": row[3]}
    return {"user_id": user_id, "memory": "", "user_name": ""}


class MemoryUpdateRequest(BaseModel):
    memory: str


@app.put("/api/memories/{bot_id}/{user_id}")
async def update_memory(bot_id: str, user_id: str, body: MemoryUpdateRequest):
    """更新用户记忆"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE user_memories SET memory = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_id = ? AND user_id = ?",
        (body.memory, bot_id, user_id)
    )
    conn.commit()
    conn.close()
    return {"success": True}


@app.delete("/api/memories/{bot_id}/{user_id}")
async def delete_memory(bot_id: str, user_id: str):
    """删除用户记忆"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_memories WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    conn.commit()
    conn.close()
    return {"success": True}


class SaveMemoryRequest(BaseModel):
    user_name: str = ""
    memory: str


@app.post("/api/memories/{bot_id}/{user_id}")
async def save_memory(bot_id: str, user_id: str, body: SaveMemoryRequest):
    """保存或追加用户记忆"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # 先按 (bot_id, user_id) 查找
        cur.execute("SELECT memory FROM user_memories WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
        row = cur.fetchone()
        
        if not row:
            # 兼容旧数据：按 user_id 查找（旧表可能只有 user_id 唯一约束）
            cur.execute("SELECT memory FROM user_memories WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if row:
                # 更新旧记录，同时设置 bot_id
                old_memory = row["memory"] if row["memory"] else ""
                new_memory = f"{old_memory}\n{body.memory}".strip()[-2000:]
                cur.execute(
                    "UPDATE user_memories SET bot_id = ?, memory = ?, user_name = COALESCE(NULLIF(?, ''), user_name), updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (bot_id, new_memory, body.user_name, user_id)
                )
                conn.commit()
                conn.close()
                return {"success": True}
        
        if row:
            # 追加到现有记忆
            old_memory = row["memory"] if row["memory"] else ""
            new_memory = f"{old_memory}\n{body.memory}".strip()[-2000:]
            cur.execute(
                "UPDATE user_memories SET memory = ?, user_name = COALESCE(NULLIF(?, ''), user_name), updated_at = CURRENT_TIMESTAMP WHERE bot_id = ? AND user_id = ?",
                (new_memory, body.user_name, bot_id, user_id)
            )
        else:
            # 新建记忆
            try:
                cur.execute(
                    "INSERT INTO user_memories (bot_id, user_id, user_name, memory) VALUES (?, ?, ?, ?)",
                    (bot_id, user_id, body.user_name or user_id, body.memory[:2000])
                )
            except sqlite3.IntegrityError:
                # 如果INSERT失败（旧唯一约束），改为UPDATE
                cur.execute(
                    "UPDATE user_memories SET bot_id = ?, memory = ?, user_name = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (bot_id, body.memory[:2000], body.user_name or user_id, user_id)
                )
        
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        print(f"保存记忆失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


class LogQuestionRequest(BaseModel):
    question: str


@app.post("/api/log_question/{bot_id}")
async def log_question(bot_id: str, body: LogQuestionRequest):
    """记录提问到统计"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO ask_logs (bot_id, question) VALUES (?, ?)", (bot_id, body.question[:500]))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/stats/{bot_id}")
async def get_stats(bot_id: str):
    """获取统计数据"""
    conn = get_db()
    cur = conn.cursor()
    
    # 总提问数
    cur.execute("SELECT COUNT(*) FROM ask_logs WHERE bot_id = ?", (bot_id,))
    total_questions = cur.fetchone()[0]
    
    # 今日提问数
    cur.execute("SELECT COUNT(*) FROM ask_logs WHERE bot_id = ? AND DATE(created_at) = DATE('now')", (bot_id,))
    today_questions = cur.fetchone()[0]
    
    # 知识条目数
    cur.execute("SELECT COUNT(*) FROM knowledge WHERE bot_id = ?", (bot_id,))
    total_knowledge = cur.fetchone()[0]
    
    # 用户记忆数
    cur.execute("SELECT COUNT(*) FROM user_memories WHERE bot_id = ?", (bot_id,))
    total_users = cur.fetchone()[0]
    
    # 最近7天统计
    cur.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as count 
        FROM ask_logs WHERE bot_id = ? AND created_at >= DATE('now', '-7 days')
        GROUP BY DATE(created_at) ORDER BY date DESC
    """, (bot_id,))
    daily_stats = [{"date": row[0], "count": row[1]} for row in cur.fetchall()]
    
    # 最近提问
    cur.execute("""
        SELECT question, created_at FROM ask_logs WHERE bot_id = ?
        ORDER BY id DESC LIMIT 20
    """, (bot_id,))
    recent_questions = [{"question": row[0][:100], "time": row[1]} for row in cur.fetchall()]
    
    conn.close()
    
    return {
        "total_questions": total_questions,
        "today_questions": today_questions,
        "total_knowledge": total_knowledge,
        "total_users": total_users,
        "daily_stats": daily_stats,
        "recent_questions": recent_questions
    }


@app.get("/admin/knowledge", response_class=HTMLResponse)
async def list_knowledge(request: Request, q: str = "", bot_id: str = "default"):
    conn = get_db()
    cur = conn.cursor()
    
    # 获取所有BOT列表
    cur.execute("SELECT id, name FROM bots ORDER BY created_at")
    bots = [dict(row) for row in cur.fetchall()]
    
    if q:
        search_term = f"%{q}%"
        cur.execute(
            "SELECT id, title, content, tags FROM knowledge WHERE bot_id = ? AND (title LIKE ? OR content LIKE ? OR tags LIKE ?) ORDER BY id DESC",
            (bot_id, search_term, search_term, search_term)
        )
    else:
        cur.execute("SELECT id, title, content, tags FROM knowledge WHERE bot_id = ? ORDER BY id DESC", (bot_id,))
        
    rows = cur.fetchall()
    conn.close()
    return templates.TemplateResponse("knowledge_list.html", {
        "request": request, "items": rows, "q": q, 
        "bots": bots, "current_bot": bot_id
    })


@app.get("/admin/knowledge/export")
async def export_knowledge():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT title, content, tags FROM knowledge")
    # 将 sqlite3.Row 转换为字典列表
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    
    # 返回 JSON 文件下载
    return JSONResponse(
        content=rows,
        headers={"Content-Disposition": "attachment; filename=knowledge_backup.json"}
    )


@app.post("/admin/knowledge/import")
async def import_knowledge(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        
        if not isinstance(data, list):
            raise ValueError("JSON 格式错误，必须是列表")
            
        conn = get_db()
        cur = conn.cursor()
        count = 0
        for item in data:
            # 简单的重复检查：如果标题完全一样，就跳过？或者直接追加？这里选择直接追加
            if item.get("title") and item.get("content"):
                cur.execute(
                    "INSERT INTO knowledge (title, content, tags) VALUES (?, ?, ?)",
                    (item.get("title"), item.get("content"), item.get("tags", ""))
                )
                count += 1
        conn.commit()
        conn.close()
        
        return RedirectResponse(
            url=f"/admin/knowledge?message=成功导入 {count} 条数据&message_type=success",
            status_code=302
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/admin/knowledge?message=导入失败: {str(e)}&message_type=error",
            status_code=302
        )


class GenerateRequest(BaseModel):
    title: str

@app.post("/admin/api/generate")
async def generate_content(req: GenerateRequest):
    if not req.title:
        return {"error": "标题不能为空"}
        
    prompt = f"""请为知识库生成一条内容。
标题/问题：{req.title}

要求：
1. 内容要准确、清晰，适合直接回复用户。
2. 格式可以是纯文本或简单的Markdown。
3. 不要包含"好的，这是生成的内容"之类的废话，直接给干货。
"""
    content = await call_llm(prompt)
    return {"content": content}


@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(request: Request, bot_id: str = "default", message: str = None, message_type: str = None):
    global app_config
    app_config = load_config()
    
    # 获取指定BOT的配置
    bot_config = get_bot_config(bot_id)
    # 合并全局配置（如管理员密码）
    bot_config["admin_password"] = app_config.get("admin_password", "")
    
    conn = get_db()
    cur = conn.cursor()
    
    # 获取所有BOT列表
    cur.execute("SELECT id, name FROM bots ORDER BY created_at")
    bots = [dict(row) for row in cur.fetchall()]
    
    # 获取知识库条目数（按bot_id）
    cur.execute("SELECT COUNT(*) FROM knowledge WHERE bot_id = ?", (bot_id,))
    kb_count = cur.fetchone()[0]
    
    # 获取统计数据（按bot_id）
    cur.execute("SELECT COUNT(*) FROM ask_logs WHERE bot_id = ?", (bot_id,))
    total_asks = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM ask_logs WHERE bot_id = ? AND DATE(created_at) = DATE('now')", (bot_id,))
    today_asks = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM ask_logs WHERE bot_id = ? AND created_at >= DATE('now', '-7 days')", (bot_id,))
    week_asks = cur.fetchone()[0]
    
    conn.close()
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "config": bot_config,
        "bots": bots,
        "current_bot": bot_id,
        "kb_count": kb_count,
        "total_asks": total_asks,
        "today_asks": today_asks,
        "week_asks": week_asks,
        "message": message,
        "message_type": message_type,
    })


@app.post("/admin/settings")
async def save_settings(
    bot_id: str = Form("default"),
    llm_base_url: str = Form(""),
    llm_api_key: str = Form(""),
    llm_model: str = Form(""),
    bot_persona: str = Form(""),
    context_limit: int = Form(100),
    admin_password: str = Form(""),
):
    global app_config
    
    # 保存BOT专属配置
    bot_config = {
        "llm_base_url": llm_base_url.strip(),
        "llm_api_key": llm_api_key.strip(),
        "llm_model": llm_model.strip(),
        "bot_persona": bot_persona.strip(),
        "context_limit": context_limit,
    }
    save_bot_config(bot_id, bot_config)
    
    # 管理员密码是全局的
    if admin_password.strip():
        app_config["admin_password"] = admin_password.strip()
        save_config(app_config)
    
    # 重定向回设置页面，带成功消息
    return RedirectResponse(
        url=f"/admin/settings?bot_id={bot_id}&message=配置已保存&message_type=success",
        status_code=302
    )


@app.post("/admin/knowledge")
async def create_knowledge(title: str = Form(...), content: str = Form(...), tags: str = Form(""), bot_id: str = Form("default")):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO knowledge (bot_id, title, content, tags) VALUES (?, ?, ?, ?)", (bot_id, title, content, tags))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/admin/knowledge?bot_id={bot_id}", status_code=302)


@app.get("/admin/knowledge/{item_id}/edit", response_class=HTMLResponse)
async def edit_knowledge_page(request: Request, item_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, bot_id, title, content, tags FROM knowledge WHERE id = ?", (item_id,))
    item = cur.fetchone()
    conn.close()
    if not item:
        return RedirectResponse(url="/admin/knowledge", status_code=302)
    return templates.TemplateResponse("knowledge_edit.html", {"request": request, "item": item})


@app.post("/admin/knowledge/{item_id}/edit")
async def update_knowledge(item_id: int, title: str = Form(...), content: str = Form(...), tags: str = Form(""), bot_id: str = Form("default")):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE knowledge SET title = ?, content = ?, tags = ? WHERE id = ?", (title, content, tags, item_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/admin/knowledge?bot_id={bot_id}", status_code=302)


@app.post("/admin/knowledge/{item_id}/delete")
async def delete_knowledge(item_id: int, bot_id: str = Form("default")):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM knowledge WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/admin/knowledge?bot_id={bot_id}", status_code=302)


@app.post("/api/ask")
async def api_ask(body: AskRequest):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    conn = get_db()
    cur = conn.cursor()
    
    bot_id = body.bot_id or "default"
    
    # 记录调用日志
    cur.execute("INSERT INTO ask_logs (bot_id, question) VALUES (?, ?)", (bot_id, question[:100]))
    conn.commit()
    
    # 获取用户记忆
    user_memory = ""
    if body.user_id:
        cur.execute("SELECT memory FROM user_memories WHERE bot_id = ? AND user_id = ?", (bot_id, body.user_id))
        row = cur.fetchone()
        if row and row["memory"]:
            user_memory = row["memory"]
    
    pattern = f"%{question[:20]}%"  # 简单 LIKE 匹配
    cur.execute(
        "SELECT title, content, tags FROM knowledge WHERE bot_id = ? AND (title LIKE ? OR content LIKE ?) ORDER BY id DESC LIMIT 5",
        (bot_id, pattern, pattern),
    )
    rows = cur.fetchall()
    conn.close()

    knowledge_texts = []
    for r in rows:
        k = f"标题: {r['title']}\n标签: {r['tags']}\n内容: {r['content']}"
        knowledge_texts.append(k)

    kb_part = "\n\n".join(knowledge_texts) if knowledge_texts else "(暂无知识库命中)"

    # 构建提示词
    prompt_parts = []
    
    # 用户记忆
    user_label = body.user_name if body.user_name else "用户"
    if user_memory:
        prompt_parts.append(f"【关于 {user_label} 的记忆】\n{user_memory}")
    
    # 添加聊天历史上下文
    if body.chat_history:
        history_text = "\n".join(body.chat_history)  # 不限制条数
        prompt_parts.append(f"【频道最近的聊天记录】\n{history_text}")
    
    # 当前用户的问题
    prompt_parts.append(f"【{user_label} 现在说】{question}")
    
    # 知识库内容
    if knowledge_texts:
        prompt_parts.append(f"【知识库参考】\n{kb_part}")
    
    prompt_parts.append(
        "自然地回复，像真人聊天一样。可以主动延续话题、反问、调侃。\n"
        "如果这次对话中有值得记住的新信息，在回复最后另起一行写：\n"
        "【记住】简短的关键信息（如：喜欢猫/名字叫小明/今天心情不好）"
    )
    
    prompt = "\n\n".join(prompt_parts)
    
    # 添加表情包信息
    if body.emojis_info:
        prompt += f"\n\n{body.emojis_info}\n你可以在回复中适当使用这些表情来让回答更生动，直接复制表情代码即可。"

    # 获取图片URL列表
    image_urls = body.image_urls if body.image_urls else None
    
    answer = await call_llm(prompt, image_urls, bot_id)
    
    # 解析并保存记忆更新
    if body.user_id and "【记住】" in answer:
        try:
            parts = answer.split("【记住】")
            new_memory_part = parts[-1].strip()
            answer = parts[0].strip()  # 移除记忆更新部分
            
            # 合并新旧记忆
            if user_memory:
                updated_memory = f"{user_memory}\n{new_memory_part}"
            else:
                updated_memory = new_memory_part
            
            # 限制记忆长度
            if len(updated_memory) > 1000:
                updated_memory = updated_memory[-1000:]
            
            conn = get_db()
            cur = conn.cursor()
            # 先按 (bot_id, user_id) 检查
            cur.execute("SELECT id FROM user_memories WHERE bot_id = ? AND user_id = ?", (bot_id, body.user_id))
            exists = cur.fetchone()
            
            if not exists:
                # 兼容旧数据：按 user_id 查找
                cur.execute("SELECT id FROM user_memories WHERE user_id = ?", (body.user_id,))
                old_exists = cur.fetchone()
                if old_exists:
                    # 更新旧记录
                    cur.execute(
                        "UPDATE user_memories SET bot_id = ?, memory = ?, user_name = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (bot_id, updated_memory, body.user_name, body.user_id)
                    )
                    conn.commit()
                    conn.close()
                else:
                    # 新建记录
                    try:
                        cur.execute(
                            "INSERT INTO user_memories (bot_id, user_id, user_name, memory) VALUES (?, ?, ?, ?)",
                            (bot_id, body.user_id, body.user_name, updated_memory)
                        )
                    except sqlite3.IntegrityError:
                        cur.execute(
                            "UPDATE user_memories SET bot_id = ?, memory = ?, user_name = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                            (bot_id, updated_memory, body.user_name, body.user_id)
                        )
                    conn.commit()
                    conn.close()
            else:
                cur.execute(
                    "UPDATE user_memories SET memory = ?, user_name = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_id = ? AND user_id = ?",
                    (updated_memory, body.user_name, bot_id, body.user_id)
                )
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[记忆更新错误] {e}")
    
    return {"answer": answer}


# ==================== 游戏系统 API ====================

@app.get("/api/game/currency/{bot_id}/{user_id}")
async def get_user_currency(bot_id: str, user_id: str):
    """获取用户货币"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT coins, last_daily FROM user_currency WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"coins": row[0], "last_daily": row[1]}
    return {"coins": 0, "last_daily": ""}


@app.post("/api/game/currency/{bot_id}/{user_id}/add")
async def add_user_currency(bot_id: str, user_id: str, amount: int, description: str = ""):
    """增加用户货币"""
    conn = get_db()
    cur = conn.cursor()
    # 获取当前余额
    cur.execute("SELECT coins FROM user_currency WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    row = cur.fetchone()
    current = row[0] if row else 0
    new_balance = current + amount
    
    # 更新余额
    cur.execute(
        "INSERT INTO user_currency (bot_id, user_id, coins, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(bot_id, user_id) DO UPDATE SET coins = ?, updated_at = CURRENT_TIMESTAMP",
        (bot_id, user_id, new_balance, new_balance)
    )
    
    # 记录交易
    cur.execute(
        "INSERT INTO transactions (bot_id, user_id, type, amount, description, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
        (bot_id, user_id, "add", amount, description, new_balance)
    )
    conn.commit()
    conn.close()
    return {"success": True, "coins": new_balance}


@app.post("/api/game/currency/{bot_id}/{user_id}/deduct")
async def deduct_user_currency(bot_id: str, user_id: str, amount: int, description: str = ""):
    """扣除用户货币"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT coins FROM user_currency WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    row = cur.fetchone()
    current = row[0] if row else 0
    
    if current < amount:
        conn.close()
        return {"success": False, "error": "余额不足", "coins": current}
    
    new_balance = current - amount
    cur.execute(
        "UPDATE user_currency SET coins = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_id = ? AND user_id = ?",
        (new_balance, bot_id, user_id)
    )
    cur.execute(
        "INSERT INTO transactions (bot_id, user_id, type, amount, description, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
        (bot_id, user_id, "deduct", -amount, description, new_balance)
    )
    conn.commit()
    conn.close()
    return {"success": True, "coins": new_balance}


@app.post("/api/game/daily/{bot_id}/{user_id}")
async def claim_daily(bot_id: str, user_id: str, amount: int = 100):
    """领取每日奖励"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT coins, last_daily FROM user_currency WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    row = cur.fetchone()
    
    if row and row[1] == today:
        conn.close()
        return {"success": False, "error": "今天已经领取过了", "coins": row[0]}
    
    current = row[0] if row else 0
    new_balance = current + amount
    
    cur.execute(
        "INSERT INTO user_currency (bot_id, user_id, coins, last_daily, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(bot_id, user_id) DO UPDATE SET coins = ?, last_daily = ?, updated_at = CURRENT_TIMESTAMP",
        (bot_id, user_id, new_balance, today, new_balance, today)
    )
    cur.execute(
        "INSERT INTO transactions (bot_id, user_id, type, amount, description, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
        (bot_id, user_id, "daily", amount, "每日签到", new_balance)
    )
    conn.commit()
    conn.close()
    return {"success": True, "coins": new_balance, "reward": amount}


@app.get("/api/game/affection/{bot_id}/{user_id}")
async def get_user_affection(bot_id: str, user_id: str):
    """获取用户好感度"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT level, exp, total_gifts, last_gift, unlocks FROM user_affection WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"level": row[0], "exp": row[1], "total_gifts": row[2], "last_gift": row[3], "unlocks": json.loads(row[4] or "[]")}
    return {"level": 0, "exp": 0, "total_gifts": 0, "last_gift": "", "unlocks": []}


@app.post("/api/game/affection/{bot_id}/{user_id}/add")
async def add_user_affection(bot_id: str, user_id: str, exp: int):
    """增加用户好感度"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT level, exp, total_gifts FROM user_affection WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    row = cur.fetchone()
    
    current_level = row[0] if row else 0
    current_exp = row[1] if row else 0
    total_gifts = row[2] if row else 0
    
    new_exp = current_exp + exp
    new_level = current_level
    
    # 升级逻辑：每100经验升一级
    while new_exp >= 100:
        new_exp -= 100
        new_level += 1
    
    cur.execute(
        "INSERT INTO user_affection (bot_id, user_id, level, exp, total_gifts, updated_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(bot_id, user_id) DO UPDATE SET level = ?, exp = ?, total_gifts = total_gifts + 1, updated_at = CURRENT_TIMESTAMP",
        (bot_id, user_id, new_level, new_exp, total_gifts + 1, new_level, new_exp)
    )
    conn.commit()
    conn.close()
    
    leveled_up = new_level > current_level
    return {"success": True, "level": new_level, "exp": new_exp, "leveled_up": leveled_up}


@app.get("/api/game/shop/{bot_id}")
async def get_shop_items(bot_id: str):
    """获取商店商品列表"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, price, item_type, effect FROM shop_items WHERE bot_id = ?", (bot_id,))
    rows = cur.fetchall()
    conn.close()
    
    items = []
    for row in rows:
        items.append({
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "price": row[3],
            "type": row[4],
            "effect": json.loads(row[5] or "{}")
        })
    return {"items": items}


class ShopItemRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    price: int = 100
    effect: dict = {"favor": 10}


@app.post("/api/game/shop/{bot_id}/add")
async def add_shop_item(bot_id: str, item: ShopItemRequest):
    """添加商品"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO shop_items (id, bot_id, name, description, price, item_type, effect) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item.id, bot_id, item.name, item.description, item.price, "gift", json.dumps(item.effect))
    )
    conn.commit()
    conn.close()
    return {"success": True}


@app.delete("/api/game/shop/{bot_id}/{item_id}")
async def delete_shop_item(bot_id: str, item_id: str):
    """删除商品"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM shop_items WHERE id = ? AND bot_id = ?", (item_id, bot_id))
    conn.commit()
    conn.close()
    return {"success": True}


@app.post("/api/game/shop/{bot_id}/buy")
async def buy_item(bot_id: str, user_id: str, item_id: str):
    """购买商品"""
    conn = get_db()
    cur = conn.cursor()
    
    # 获取商品信息
    cur.execute("SELECT name, price, item_type, effect FROM shop_items WHERE id = ? AND bot_id = ?", (item_id, bot_id))
    item = cur.fetchone()
    if not item:
        conn.close()
        return {"success": False, "error": "商品不存在"}
    
    item_name, price, item_type, effect_str = item
    effect = json.loads(effect_str or "{}")
    
    # 检查余额
    cur.execute("SELECT coins FROM user_currency WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
    row = cur.fetchone()
    current_coins = row[0] if row else 0
    
    if current_coins < price:
        conn.close()
        return {"success": False, "error": "基米币不足", "need": price, "have": current_coins}
    
    # 扣款
    new_balance = current_coins - price
    cur.execute(
        "UPDATE user_currency SET coins = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_id = ? AND user_id = ?",
        (new_balance, bot_id, user_id)
    )
    
    # 记录购买
    cur.execute(
        "INSERT INTO user_purchases (bot_id, user_id, item_id, item_name) VALUES (?, ?, ?, ?)",
        (bot_id, user_id, item_id, item_name)
    )
    
    # 记录交易
    cur.execute(
        "INSERT INTO transactions (bot_id, user_id, type, amount, description, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
        (bot_id, user_id, "purchase", -price, f"购买 {item_name}", new_balance)
    )
    
    # 如果是礼物，增加好感度
    favor_gained = 0
    if item_type == "gift" and "favor" in effect:
        favor_gained = effect["favor"]
        cur.execute("SELECT level, exp FROM user_affection WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
        aff_row = cur.fetchone()
        current_level = aff_row[0] if aff_row else 0
        current_exp = aff_row[1] if aff_row else 0
        new_exp = current_exp + favor_gained
        new_level = current_level
        while new_exp >= 100:
            new_exp -= 100
            new_level += 1
        cur.execute(
            "INSERT INTO user_affection (bot_id, user_id, level, exp, total_gifts, updated_at) VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP) "
            "ON CONFLICT(bot_id, user_id) DO UPDATE SET level = ?, exp = ?, total_gifts = total_gifts + 1, updated_at = CURRENT_TIMESTAMP",
            (bot_id, user_id, new_level, new_exp, new_level, new_exp)
        )
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "item_name": item_name,
        "price": price,
        "coins": new_balance,
        "favor_gained": favor_gained
    }


@app.get("/api/game/transactions/{bot_id}/{user_id}")
async def get_transactions(bot_id: str, user_id: str, limit: int = 20):
    """获取交易记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT type, amount, description, balance_after, created_at FROM transactions WHERE bot_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
        (bot_id, user_id, limit)
    )
    rows = cur.fetchall()
    conn.close()
    
    transactions = []
    for row in rows:
        transactions.append({
            "type": row[0],
            "amount": row[1],
            "description": row[2],
            "balance_after": row[3],
            "created_at": row[4]
        })
    return {"transactions": transactions}


@app.get("/api/game/leaderboard/{bot_id}")
async def get_leaderboard(bot_id: str, type: str = "coins", limit: int = 10):
    """获取排行榜"""
    conn = get_db()
    cur = conn.cursor()
    
    if type == "coins":
        cur.execute(
            "SELECT user_id, coins FROM user_currency WHERE bot_id = ? ORDER BY coins DESC LIMIT ?",
            (bot_id, limit)
        )
        rows = cur.fetchall()
        leaderboard = [{"user_id": row[0], "coins": row[1]} for row in rows]
    else:  # affection
        cur.execute(
            "SELECT user_id, level, exp FROM user_affection WHERE bot_id = ? ORDER BY level DESC, exp DESC LIMIT ?",
            (bot_id, limit)
        )
        rows = cur.fetchall()
        leaderboard = [{"user_id": row[0], "level": row[1], "exp": row[2]} for row in rows]
    
    conn.close()
    return {"leaderboard": leaderboard, "type": type}


@app.post("/api/game/migrate")
async def migrate_game_data(path: str = None):
    """从小鱼娘本地 bot_data.json 迁移游戏数据到后端数据库"""
    # 尝试多个可能的路径
    possible_paths = [
        path,  # 用户指定的路径
        "/www/wwwroot/bot/bot_data/bot_data.json",
        "/app/bot_data/bot_data.json",
        "/www/wwwroot/mybot/bot_data/bot_data.json",
        os.path.join(DATA_DIR, "bot_data.json"),
    ]
    
    bot_data_path = None
    for p in possible_paths:
        if p and os.path.exists(p):
            bot_data_path = p
            break
    
    bot_id = "maodie"  # 只有小鱼娘有游戏数据
    
    if not bot_data_path:
        return {"success": False, "error": f"文件不存在，尝试过的路径: {[p for p in possible_paths if p]}"}
    
    try:
        with open(bot_data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"读取文件失败: {e}"}
    
    conn = get_db()
    cur = conn.cursor()
    
    migrated = {"currency": 0, "affection": 0}
    
    # 迁移货币数据
    user_currency = data.get("user_currency", {})
    for user_id, info in user_currency.items():
        coins = info.get("coins", 0) if isinstance(info, dict) else info
        last_daily = info.get("last_daily", "") if isinstance(info, dict) else ""
        cur.execute(
            "INSERT INTO user_currency (bot_id, user_id, coins, last_daily, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(bot_id, user_id) DO UPDATE SET coins = ?, last_daily = ?, updated_at = CURRENT_TIMESTAMP",
            (bot_id, user_id, coins, last_daily, coins, last_daily)
        )
        migrated["currency"] += 1
    
    # 迁移好感度数据
    user_affection = data.get("user_affection", {})
    for user_id, info in user_affection.items():
        level = info.get("level", 0)
        exp = info.get("exp", 0)
        total_gifts = info.get("total_gifts", 0)
        last_gift = info.get("last_gift", "")
        unlocks = json.dumps(info.get("unlocks", []))
        cur.execute(
            "INSERT INTO user_affection (bot_id, user_id, level, exp, total_gifts, last_gift, unlocks, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(bot_id, user_id) DO UPDATE SET level = ?, exp = ?, total_gifts = ?, last_gift = ?, unlocks = ?, updated_at = CURRENT_TIMESTAMP",
            (bot_id, user_id, level, exp, total_gifts, last_gift, unlocks, level, exp, total_gifts, last_gift, unlocks)
        )
        migrated["affection"] += 1
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": f"迁移完成！货币: {migrated['currency']} 条，好感度: {migrated['affection']} 条",
        "migrated": migrated
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
