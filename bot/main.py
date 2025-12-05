import os
import re
import discord
import httpx
import json
import asyncio

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")
BOT_ID = os.getenv("BOT_ID", "default")

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


class MeowClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

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
