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
    """通过 New API 注册用户"""
    if not NEWAPI_URL or not NEWAPI_ADMIN_KEY:
        return {"success": False, "message": "New API 未配置"}
    
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{NEWAPI_URL.rstrip('/')}/api/user/register",
                json={
                    "username": username,
                    "password": password,
                    "display_name": display_name or username
                },
                headers={"Authorization": f"Bearer {NEWAPI_ADMIN_KEY}"}
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("success"):
                return {"success": True, "message": "注册成功", "data": data.get("data")}
            return {"success": False, "message": data.get("message", "注册失败")}
    except Exception as e:
        return {"success": False, "message": f"请求失败: {e}"}


async def newapi_login(username: str, password: str):
    """通过 New API 登录获取 Token"""
    if not NEWAPI_URL:
        return {"success": False, "message": "New API 未配置"}
    
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{NEWAPI_URL.rstrip('/')}/api/user/login",
                json={"username": username, "password": password}
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("success"):
                return {"success": True, "token": data.get("data", {}).get("token"), "data": data.get("data")}
            return {"success": False, "message": data.get("message", "登录失败")}
    except Exception as e:
        return {"success": False, "message": f"请求失败: {e}"}


async def newapi_get_user_info(token: str):
    """获取用户信息（余额、Key等）"""
    if not NEWAPI_URL:
        return {"success": False, "message": "New API 未配置"}
    
    try:
        async with httpx.AsyncClient(timeout=30) as http:
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
        # 注册命令（管理员）
        @self.tree.command(name="注册", description="为用户注册 New API 账号（管理员专用）")
        @app_commands.describe(用户名="注册的用户名", 密码="初始密码", 昵称="显示昵称（可选）")
        async def cmd_register(interaction: discord.Interaction, 用户名: str, 密码: str, 昵称: str = ""):
            if not is_admin(str(interaction.user.id)):
                await interaction.response.send_message("❌ 此命令仅管理员可用", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            result = await newapi_register(用户名, 密码, 昵称)
            if result["success"]:
                await interaction.followup.send(f"✅ 注册成功！\n👤 用户名：`{用户名}`\n🔑 密码：`{密码}`", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

        # 登录命令
        @self.tree.command(name="登录", description="登录你的 New API 账号")
        @app_commands.describe(用户名="你的用户名", 密码="你的密码")
        async def cmd_login(interaction: discord.Interaction, 用户名: str, 密码: str):
            await interaction.response.defer(ephemeral=True)
            result = await newapi_login(用户名, 密码)
            if result["success"]:
                # 保存 token
                user_tokens[str(interaction.user.id)] = result["token"]
                await interaction.followup.send("✅ 登录成功！现在可以使用 /账号 /余额 /令牌 等命令了", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

        # 账号命令
        @self.tree.command(name="账号", description="查看你的 New API 账号信息")
        async def cmd_account(interaction: discord.Interaction):
            token = user_tokens.get(str(interaction.user.id))
            if not token:
                await interaction.response.send_message("❌ 请先使用 /登录 命令登录", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
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
            token = user_tokens.get(str(interaction.user.id))
            if not token:
                await interaction.response.send_message("❌ 请先使用 /登录 命令登录", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            result = await newapi_get_user_info(token)
            if result["success"]:
                data = result["data"]
                quota = data.get('quota', 0) / 500000
                used = data.get('used_quota', 0) / 500000
                await interaction.followup.send(
                    f"💰 **余额查询**\n"
                    f"可用余额：**${quota:.4f}**\n"
                    f"已使用：${used:.4f}",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

        # 令牌/Key 命令
        @self.tree.command(name="令牌", description="查看你的 API Key")
        async def cmd_token(interaction: discord.Interaction):
            token = user_tokens.get(str(interaction.user.id))
            if not token:
                await interaction.response.send_message("❌ 请先使用 /登录 命令登录", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            # 获取用户的 API Keys
            try:
                async with httpx.AsyncClient(timeout=30) as http:
                    resp = await http.get(
                        f"{NEWAPI_URL.rstrip('/')}/api/token/?p=0&size=10",
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    data = resp.json()
                    if resp.status_code == 200 and data.get("success"):
                        tokens = data.get("data", [])
                        if not tokens:
                            await interaction.followup.send("📭 你还没有创建 API Key，请在网页端创建", ephemeral=True)
                            return
                        
                        msg = "🔑 **你的 API Keys**\n"
                        for t in tokens[:5]:  # 最多显示5个
                            name = t.get('name', '未命名')
                            key = t.get('key', '')
                            status = "✅" if t.get('status') == 1 else "❌"
                            msg += f"\n{status} **{name}**\n`{key}`\n"
                        
                        await interaction.followup.send(msg, ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ {data.get('message', '获取失败')}", ephemeral=True)
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
                async with httpx.AsyncClient(timeout=30) as http:
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
