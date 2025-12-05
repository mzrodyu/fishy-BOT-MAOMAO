import os
import re
import discord
from discord import app_commands
import httpx
import json
import asyncio
import hashlib

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")
BOT_ID = os.getenv("BOT_ID", "default")

# New API 配置
NEWAPI_URL = os.getenv("NEWAPI_URL", "")  # New API 地址，例如 https://api.example.com
NEWAPI_ADMIN_KEY = os.getenv("NEWAPI_ADMIN_KEY", "")  # 管理员 API Key（用于注册用户）
ADMIN_USER_IDS = os.getenv("ADMIN_USER_IDS", "").split(",")  # 管理员 Discord ID 列表
NEWAPI_VERIFY_SSL = os.getenv("NEWAPI_VERIFY_SSL", "false").lower() == "true"  # 是否验证SSL证书

# 用户消息计数器（用于定期总结）
user_message_counts = {}


async def save_user_memory(user_id: str, user_name: str, user_msg: str):
    """直接记录用户发言到记忆"""
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            await http.post(
                f"{BACKEND_URL.rstrip('/')}/api/memories/{BOT_ID}/{user_id}",
                json={"user_name": user_name, "memory": user_msg[:200]}
            )
            print(f'🧠 [记忆已追加] {user_name}: {user_msg[:30]}...', flush=True)
    except Exception as e:
        print(f'🧠 [记忆追加失败] {e}', flush=True)


async def summarize_user_memory(user_id: str, user_name: str):
    """每50条消息总结一次用户记忆"""
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            # 获取当前记忆
            resp = await http.get(f"{BACKEND_URL.rstrip('/')}/api/memories/{BOT_ID}/{user_id}")
            if resp.status_code != 200:
                return
            data = resp.json()
            current_memory = data.get('memory', '')
            
            if len(current_memory) < 500:
                return
            
            # 调用后端 AI 总结（使用 /api/ask）
            summary_resp = await http.post(
                f"{BACKEND_URL.rstrip('/')}/api/ask",
                json={
                    "question": f"请将以下聊天记录整理成简洁的个人信息摘要，提取关键信息如姓名、爱好、性格等，用简短要点：\n{current_memory[-2000:]}",
                    "bot_id": BOT_ID,
                }
            )
            if summary_resp.status_code == 200:
                summary = summary_resp.json().get('answer', '')
                if summary:
                    # 更新为总结后的记忆
                    await http.put(
                        f"{BACKEND_URL.rstrip('/')}/api/memories/{BOT_ID}/{user_id}",
                        json={"memory": summary[:1500]}
                    )
                    print(f'🧠 [记忆已总结] {user_name}', flush=True)
    except Exception as e:
        print(f'🧠 [记忆总结失败] {e}', flush=True)


# 配置文件路径
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "config.json")


def get_config():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}


def get_context_limit():
    # 0 或负数表示不限制，默认获取100条
    limit = get_config().get("context_limit", 100)
    if limit is None or int(limit) <= 0:
        return 100  # 不限制时默认取100条
    return int(limit)


intents = discord.Intents.default()
intents.message_content = True


# ==================== New API 功能 ====================

async def newapi_register(username: str, password: str, display_name: str = ""):
    """通过 New API 管理员接口创建用户"""
    if not NEWAPI_URL or not NEWAPI_ADMIN_KEY:
        return {"success": False, "message": "New API 未配置"}
    
    try:
        async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
            # 使用管理员创建用户接口
            resp = await http.post(
                f"{NEWAPI_URL.rstrip('/')}/api/user/",
                json={
                    "username": username,
                    "password": password,
                    "display_name": display_name or username,
                    "quota": 0,
                    "group": "default",
                    "status": 1
                },
                headers={
                    "Authorization": f"Bearer {NEWAPI_ADMIN_KEY}",
                    "New-Api-User": "1"
                }
            )
            print(f"[New API 注册] 状态码: {resp.status_code}, 响应: {resp.text[:500]}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return {"success": True, "message": "注册成功", "data": data.get("data")}
                return {"success": False, "message": data.get("message", "注册失败")}
            else:
                return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": f"请求失败: {e}"}


async def newapi_login(username: str, password: str):
    """通过 New API 登录获取 Token"""
    if not NEWAPI_URL:
        return {"success": False, "message": "New API 未配置"}
    
    try:
        async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
            resp = await http.post(
                f"{NEWAPI_URL.rstrip('/')}/api/user/login",
                json={"username": username, "password": password}
            )
            print(f"[New API 登录] 状态码: {resp.status_code}, 响应: {resp.text[:500]}")
            data = resp.json()
            if resp.status_code == 200 and data.get("success"):
                # token 可能在不同位置
                token = data.get("data", {}).get("token") or data.get("data", {}).get("access_token") or data.get("token")
                print(f"[New API 登录] 获取到的 token: {token}")
                return {"success": True, "token": token, "data": data.get("data")}
            return {"success": False, "message": data.get("message", "登录失败")}
    except Exception as e:
        return {"success": False, "message": f"请求失败: {e}"}


async def newapi_get_user_info(token: str):
    """获取用户信息（余额、Key等）"""
    if not NEWAPI_URL:
        return {"success": False, "message": "New API 未配置"}
    
    try:
        async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
            resp = await http.get(
                f"{NEWAPI_URL.rstrip('/')}/api/user/self",
                headers={"Authorization": f"Bearer {token}"}
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("success"):
                return {"success": True, "data": data.get("data")}
            return {"success": False, "message": data.get("message", "获取失败")}
    except Exception as e:
        return {"success": False, "message": f"请求失败: {e}"}


# 用户 Token 存储（内存中，重启会丢失）
# 实际使用建议存储到后端数据库
user_tokens = {}


def is_admin(user_id: str) -> bool:
    """检查用户是否是管理员"""
    return str(user_id) in ADMIN_USER_IDS


class MeowClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        """注册斜杠命令"""
        
        # 检查用户是否已绑定
        async def check_user_bindng(discord_id: str):
            """检查用户是否已在后端绑定"""
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    resp = await http.get(f"{BACKEND_URL.rstrip('/')}/api/newapi-users/by-discord/{discord_id}")
                    if resp.status_code == 200:
                        return resp.json()
            except:
                pass
            return {"exists": False}
        
        # 保存用户绑定到后端
        async def save_user_binding(discord_id: str, discord_name: str, newapi_username: str, token: str = ""):
            """保存用户绑定到后端"""
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.post(
                        f"{BACKEND_URL.rstrip('/')}/api/newapi-users",
                        json={
                            "discord_id": discord_id,
                            "discord_name": discord_name,
                            "newapi_username": newapi_username,
                            "newapi_token": token
                        }
                    )
            except Exception as e:
                print(f"保存绑定失败: {e}")
        
        # 更新用户 Token
        async def update_user_token(discord_id: str, token: str):
            """更新用户 Token"""
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.put(
                        f"{BACKEND_URL.rstrip('/')}/api/newapi-users/{discord_id}/token",
                        params={"token": token}
                    )
            except:
                pass
        
        # 注册命令（用户自己注册）
        @self.tree.command(name="注册", description="注册你的 New API 账号")
        @app_commands.describe(用户名="设置你的用户名（英文字母和数字）", 密码="设置你的密码（至少8位）")
        async def cmd_register(interaction: discord.Interaction, 用户名: str, 密码: str):
            # 检查用户名
            if len(用户名) < 3 or len(用户名) > 20:
                await interaction.response.send_message("❌ 用户名需要3-20个字符", ephemeral=True)
                return
            
            # 检查密码长度
            if len(密码) < 8:
                await interaction.response.send_message("❌ 密码至少需要8位", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            discord_id = str(interaction.user.id)
            discord_name = interaction.user.display_name
            
            # 检查是否已绑定
            binding = await check_user_bindng(discord_id)
            if binding.get("exists"):
                existing = binding.get("user", {})
                await interaction.followup.send(
                    f"❌ 你已经注册过了！\n账号：`{existing.get('newapi_username', '未知')}`",
                    ephemeral=True
                )
                return
            
            # 使用用户自定义的用户名
            username = 用户名
            
            # 在 New API 注册
            result = await newapi_register(username, 密码, discord_name)
            if result["success"]:
                # 保存绑定关系到后端
                await save_user_binding(discord_id, discord_name, username)
                await interaction.followup.send(
                    f"✅ 注册成功！\n"
                    f"🔑 用户名：`{username}`\n"
                    f"🔐 密码：`{密码}`\n\n"
                    f"现在可以使用 /登录 命令登录了",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

        # 登录命令（自动使用绑定的账号）
        @self.tree.command(name="登录", description="登录你的 New API 账号")
        @app_commands.describe(密码="你的密码")
        async def cmd_login(interaction: discord.Interaction, 密码: str):
            await interaction.response.defer(ephemeral=True)
            
            discord_id = str(interaction.user.id)
            
            # 检查是否已绑定
            binding = await check_user_bindng(discord_id)
            if not binding.get("exists"):
                await interaction.followup.send(
                    "❌ 你还没有注册账号，请联系管理员使用 /注册 命令为你开通",
                    ephemeral=True
                )
                return
            
            username = binding["user"]["newapi_username"]
            
            # 登录
            result = await newapi_login(username, 密码)
            if result["success"]:
                token = result.get("token")
                print(f"[登录] discord_id={discord_id}, token={token}")
                if token:
                    # 保存到内存
                    user_tokens[discord_id] = token
                    # 更新到后端
                    await update_user_token(discord_id, token)
                    await interaction.followup.send(
                        f"✅ 登录成功！\n👤 账号：`{username}`\n\n现在可以使用 /账号 /余额 /令牌 等命令了",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"✅ 登录成功！\n👤 账号：`{username}`\n\n⚠️ 但未获取到 token，某些功能可能受限",
                        ephemeral=True
                    )
            else:
                await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

        # 账号命令
        @self.tree.command(name="账号", description="查看你的 New API 账号信息")
        async def cmd_account(interaction: discord.Interaction):
            discord_id = str(interaction.user.id)
            
            # 检查是否已绑定
            binding = await check_user_bindng(discord_id)
            if not binding.get("exists"):
                await interaction.response.send_message("❌ 你还没有注册账号，请使用 /注册 命令", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            # 使用管理员 Key 查询用户信息
            username = binding["user"]["newapi_username"]
            try:
                async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
                    # 使用搜索接口
                    resp = await http.get(
                        f"{NEWAPI_URL.rstrip('/')}/api/user/search",
                        params={"keyword": username},
                        headers={
                            "Authorization": f"{NEWAPI_ADMIN_KEY}",
                            "New-Api-User": "1"
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("success"):
                            # 数据在 data.items 里
                            items = data.get("data", {}).get("items", [])
                            user = None
                            for u in items:
                                if isinstance(u, dict) and u.get("username") == username:
                                    user = u
                                    break
                            if user:
                                info = f"""📋 **账号信息**
👤 用户名：`{user.get('username', 'N/A')}`
📛 昵称：{user.get('display_name', 'N/A')}
💰 余额：**${user.get('quota', 0) / 500000:.4f}**
🎫 已用：${user.get('used_quota', 0) / 500000:.4f}
📊 请求次数：{user.get('request_count', 0)}
🎭 角色：{'管理员' if user.get('role') == 100 else '普通用户'}
📊 状态：{'✅ 正常' if user.get('status') == 1 else '❌ 禁用'}
"""
                                await interaction.followup.send(info, ephemeral=True)
                                return
                            await interaction.followup.send(f"❌ 未找到用户 (共{len(items)}个结果)", ephemeral=True)
                            return
                        await interaction.followup.send(f"❌ {data.get('message', '查询失败')}", ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ HTTP {resp.status_code}", ephemeral=True)
                    return
            except Exception as e:
                import traceback
                print(f"[账号查询错误] {traceback.format_exc()}")
                await interaction.followup.send(f"❌ 请求失败: {type(e).__name__}: {e}", ephemeral=True)
            return
            
            result = await newapi_get_user_info(token)
            if result["success"]:
                data = result["data"]
                info = f"""📋 **账号信息**
👤 用户名：`{data.get('username', 'N/A')}`
📛 昵称：{data.get('display_name', 'N/A')}
📧 邮箱：{data.get('email', '未绑定')}
💰 余额：**{data.get('quota', 0) / 500000:.2f}** 美元
🎫 已用额度：{data.get('used_quota', 0) / 500000:.4f} 美元
📊 请求次数：{data.get('request_count', 0)}
"""
                await interaction.followup.send(info, ephemeral=True)
            else:
                if "unauthorized" in result["message"].lower():
                    user_tokens.pop(str(interaction.user.id), None)
                    await interaction.followup.send("❌ 登录已过期，请重新 /登录", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

        # 余额命令
        @self.tree.command(name="余额", description="查看你的 New API 余额")
        async def cmd_balance(interaction: discord.Interaction):
            discord_id = str(interaction.user.id)
            
            # 检查是否已绑定
            binding = await check_user_bindng(discord_id)
            if not binding.get("exists"):
                await interaction.response.send_message("❌ 你还没有注册账号，请使用 /注册 命令", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            # 使用管理员 Key 查询用户信息
            username = binding["user"]["newapi_username"]
            try:
                async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
                    resp = await http.get(
                        f"{NEWAPI_URL.rstrip('/')}/api/user/search",
                        params={"keyword": username},
                        headers={
                            "Authorization": f"{NEWAPI_ADMIN_KEY}",
                            "New-Api-User": "1"
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("success"):
                            items = data.get("data", {}).get("items", [])
                            user = None
                            for u in items:
                                if isinstance(u, dict) and u.get("username") == username:
                                    user = u
                                    break
                            if user:
                                quota = user.get('quota', 0) / 500000
                                used = user.get('used_quota', 0) / 500000
                                await interaction.followup.send(
                                    f"💰 **余额查询**\n"
                                    f"可用余额：**${quota:.4f}**\n"
                                    f"已使用：${used:.4f}",
                                    ephemeral=True
                                )
                                return
                    await interaction.followup.send("❌ 查询失败", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 请求失败: {e}", ephemeral=True)

        # 获取用户 New API ID 的辅助函数
        async def get_newapi_user_id(username: str):
            """通过用户名获取 New API 用户 ID"""
            try:
                async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
                    resp = await http.get(
                        f"{NEWAPI_URL.rstrip('/')}/api/user/search",
                        params={"keyword": username},
                        headers={
                            "Authorization": f"{NEWAPI_ADMIN_KEY}",
                            "New-Api-User": "1"
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("success"):
                            items = data.get("data", {}).get("items", [])
                            for u in items:
                                if u.get("username") == username:
                                    return u.get("id")
            except:
                pass
            return None
        
        # 令牌/Key 命令
        @self.tree.command(name="令牌", description="查看你的 API Key")
        async def cmd_token(interaction: discord.Interaction):
            discord_id = str(interaction.user.id)
            
            # 检查是否已绑定
            binding = await check_user_bindng(discord_id)
            if not binding.get("exists"):
                await interaction.response.send_message("❌ 你还没有注册账号，请使用 /注册 命令", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            username = binding["user"]["newapi_username"]
            user_id = await get_newapi_user_id(username)
            if not user_id:
                await interaction.followup.send("❌ 无法获取用户信息", ephemeral=True)
                return
            
            # 管理员获取所有令牌，然后过滤
            try:
                async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
                    resp = await http.get(
                        f"{NEWAPI_URL.rstrip('/')}/api/token/",
                        params={"p": 0, "size": 1000},
                        headers={
                            "Authorization": f"{NEWAPI_ADMIN_KEY}",
                            "New-Api-User": "1"
                        }
                    )
                    data = resp.json()
                    print(f"[令牌] user_id={user_id}, 响应: {str(data)[:500]}")
                    
                    if resp.status_code == 200 and data.get("success"):
                        tokens_data = data.get("data", {})
                        if isinstance(tokens_data, dict):
                            all_tokens = tokens_data.get("data", []) or tokens_data.get("items", [])
                        elif isinstance(tokens_data, list):
                            all_tokens = tokens_data
                        else:
                            all_tokens = []
                        
                        # 过滤当前用户的令牌
                        tokens = [t for t in all_tokens if str(t.get("user_id")) == str(user_id)]
                        print(f"[令牌] 总数: {len(all_tokens)}, 用户令牌: {len(tokens)}")
                        
                        if not tokens:
                            await interaction.followup.send(
                                f"📭 你还没有 API Key\n\n"
                                f"使用 `/创建令牌 名称` 来创建一个！\n\n"
                                f"🔍 调试: user_id={user_id}, 总令牌={len(all_tokens)}",
                                ephemeral=True
                            )
                            return
                        
                        msg = "🔑 **你的 API Keys**\n"
                        for t in tokens[:5]:
                            name = t.get('name', '未命名')
                            key = t.get('key', '')
                            if key and not key.startswith('sk-'):
                                key = f"sk-{key}"
                            status = "✅" if t.get('status') == 1 else "❌"
                            quota = t.get('remain_quota', 0)
                            unlimited = t.get('unlimited_quota', False)
                            quota_str = "无限" if unlimited else f"${quota / 500000:.4f}"
                            msg += f"\n{status} **{name}** (额度: {quota_str})\n`{key}`\n"
                        
                        await interaction.followup.send(msg, ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ {data.get('message', '获取失败')}", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 请求失败: {e}", ephemeral=True)
        
        # 创建令牌命令
        @self.tree.command(name="创建令牌", description="创建一个新的 API Key")
        @app_commands.describe(名称="令牌名称")
        async def cmd_create_token(interaction: discord.Interaction, 名称: str):
            discord_id = str(interaction.user.id)
            
            # 检查是否已绑定
            binding = await check_user_bindng(discord_id)
            if not binding.get("exists"):
                await interaction.response.send_message("❌ 你还没有注册账号，请使用 /注册 命令", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            username = binding["user"]["newapi_username"]
            user_id = await get_newapi_user_id(username)
            if not user_id:
                await interaction.followup.send("❌ 无法获取用户信息", ephemeral=True)
                return
            
            # 管理员帮用户创建令牌
            try:
                async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
                    resp = await http.post(
                        f"{NEWAPI_URL.rstrip('/')}/api/token/",
                        json={
                            "name": 名称,
                            "user_id": user_id,
                            "remain_quota": 0,
                            "unlimited_quota": True
                        },
                        headers={
                            "Authorization": f"{NEWAPI_ADMIN_KEY}",
                            "New-Api-User": "1"
                        }
                    )
                    data = resp.json()
                    print(f"[创建令牌] user_id={user_id}, 响应: {data}")
                    
                    if resp.status_code == 200 and data.get("success"):
                        token_key = data.get("data", "")
                        if isinstance(token_key, dict):
                            token_key = token_key.get("key", "")
                        if token_key and not token_key.startswith('sk-'):
                            token_key = f"sk-{token_key}"
                        
                        if token_key:
                            await interaction.followup.send(
                                f"✅ 令牌创建成功！\n\n"
                                f"📛 名称：**{名称}**\n"
                                f"🔑 Key：\n```\n{token_key}\n```\n"
                                f"⚠️ 请妥善保管，此 Key 只显示一次！",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                f"✅ 令牌创建成功！\n\n"
                                f"📛 名称：**{名称}**\n"
                                f"🔑 使用 `/令牌` 查看你的 Key",
                                ephemeral=True
                            )
                    else:
                        await interaction.followup.send(f"❌ {data.get('message', '创建失败')}", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 请求失败: {e}", ephemeral=True)

        # 查询用户命令（管理员）
        @self.tree.command(name="查询用户", description="查询指定用户信息（管理员专用）")
        @app_commands.describe(用户名="要查询的用户名")
        async def cmd_query_user(interaction: discord.Interaction, 用户名: str):
            if not is_admin(str(interaction.user.id)):
                await interaction.response.send_message("❌ 此命令仅管理员可用", ephemeral=True)
                return
            
            if not NEWAPI_URL or not NEWAPI_ADMIN_KEY:
                await interaction.response.send_message("❌ New API 未配置", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            try:
                async with httpx.AsyncClient(timeout=30, verify=NEWAPI_VERIFY_SSL) as http:
                    resp = await http.get(
                        f"{NEWAPI_URL.rstrip('/')}/api/user/search?keyword={用户名}",
                        headers={"Authorization": f"Bearer {NEWAPI_ADMIN_KEY}"}
                    )
                    data = resp.json()
                    if resp.status_code == 200 and data.get("success"):
                        users = data.get("data", [])
                        if not users:
                            await interaction.followup.send(f"❌ 未找到用户 `{用户名}`", ephemeral=True)
                            return
                        
                        user = users[0]
                        info = f"""📋 **用户信息**
👤 用户名：`{user.get('username', 'N/A')}`
📛 昵称：{user.get('display_name', 'N/A')}
💰 余额：**${user.get('quota', 0) / 500000:.4f}**
🎫 已用：${user.get('used_quota', 0) / 500000:.4f}
📊 状态：{'✅ 正常' if user.get('status') == 1 else '❌ 禁用'}
"""
                        await interaction.followup.send(info, ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ {data.get('message', '查询失败')}", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 请求失败: {e}", ephemeral=True)

        # 同步命令
        await self.tree.sync()
        print(f"✅ 斜杠命令已注册")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        if NEWAPI_URL:
            print(f"✅ New API 已配置: {NEWAPI_URL}")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # 检测是否应该响应：被@了 或者 回复了机器人的消息
        is_mentioned = self.user in message.mentions
        is_reply_to_bot = False
        if message.reference and message.reference.message_id:
            try:
                replied_msg = await message.channel.fetch_message(message.reference.message_id)
                if replied_msg.author.id == self.user.id:
                    is_reply_to_bot = True
            except:
                pass
        
        if not is_mentioned and not is_reply_to_bot:
            return

        content = message.content.strip()
        # 提取问题（用正则去掉所有@mention）
        question = re.sub(r'<@!?\d+>', '', content).strip()

        # 没有问题时，设置默认问题
        if not question:
            question = "你好"

        # 检查是否有图片附件
        image_urls = []
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                image_urls.append(att.url)

        # 获取服务器表情包列表
        emojis_info = ""
        if message.guild:
            emoji_list = []
            for emoji in message.guild.emojis[:50]:
                if emoji.animated:
                    emoji_list.append(f"<a:{emoji.name}:{emoji.id}>")
                else:
                    emoji_list.append(f"<:{emoji.name}:{emoji.id}>")
            if emoji_list:
                emojis_info = "可用的服务器表情：" + " ".join(emoji_list)

        # 获取频道最近的聊天记录作为上下文
        chat_history = []
        limit = get_context_limit()
        if limit:
            try:
                async for msg in message.channel.history(limit=limit + 1):
                    if msg.id == message.id:
                        continue
                    # 获取消息内容，保留@标记
                    msg_content = msg.content[:200] if msg.content else ""
                    # 处理附件说明
                    if not msg_content and msg.attachments:
                        msg_content = "[发送了附件]"
                    if not msg_content:
                        continue
                    # 标识发送者
                    if msg.author.id == self.user.id:
                        author_name = "你(机器人)"
                    elif msg.author.bot:
                        author_name = f"{msg.author.display_name}(机器人)"
                    else:
                        author_name = msg.author.display_name
                    chat_history.append(f"{author_name}: {msg_content}")
                chat_history.reverse()
            except Exception as e:
                print(f"[上下文读取错误] {e}")

        async with message.channel.typing():
            try:
                async with httpx.AsyncClient(timeout=90) as http:
                    resp = await http.post(
                        f"{BACKEND_URL.rstrip('/')}/api/ask",
                        json={
                            "question": question, 
                            "image_urls": image_urls,
                            "emojis_info": emojis_info,
                            "chat_history": chat_history,
                            "user_name": message.author.display_name,
                            "user_id": str(message.author.id),
                            "bot_id": BOT_ID,
                        },
                    )
                if resp.status_code != 200:
                    await message.reply(f"后端错误：{resp.status_code} {resp.text}")
                    return
                data = resp.json()
                answer = data.get("answer", "(后端没有返回answer字段)")
                if len(answer) > 1800:
                    answer = answer[:1800] + "..."
                await message.reply(answer)
                
                # 记录用户发言到记忆
                user_id = str(message.author.id)
                user_name = message.author.display_name
                asyncio.create_task(save_user_memory(user_id, user_name, question))
                
                # 更新消息计数，每50条自动总结
                user_message_counts[user_id] = user_message_counts.get(user_id, 0) + 1
                if user_message_counts[user_id] >= 50:
                    user_message_counts[user_id] = 0
                    asyncio.create_task(summarize_user_memory(user_id, user_name))
            except Exception as e:
                await message.reply(f"请求后端失败：{e}")


client = MeowClient()


def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN 未配置，请在运行环境变量中设置。")
    client.run(TOKEN)


if __name__ == "__main__":
    main()
