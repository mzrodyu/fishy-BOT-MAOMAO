# 🐱 Meow QA Bot - 喵喵答疑机器人

一个基于 Discord 的智能问答机器人，支持多 Bot 管理、用户记忆、游戏系统等功能。

## ✨ 功能特点

- 🤖 **多 Bot 支持** - 可以同时管理多个不同人设的机器人
- 🧠 **用户记忆** - 自动记住用户信息，实现个性化对话
- 🎮 **游戏系统** - 签到、货币、好感度、商店等娱乐功能
- 📚 **知识库** - 可自定义知识库让机器人回答特定问题
- 🖼️ **图片理解** - 支持发送图片让机器人分析
- 😺 **表情包支持** - 自动获取服务器表情并在回复中使用
- 📊 **后台管理** - 美观的 Web 管理界面

## 🏗️ 项目结构

```
meow-qa-bot/
├── backend/          # FastAPI 后端服务
│   ├── main.py       # 主程序
│   ├── static/       # 静态资源
│   └── templates/    # HTML 模板
├── bot/              # Discord Bot
│   └── main.py       # Bot 主程序
├── .env.example      # 环境变量示例
├── requirements.txt  # Python 依赖
├── docker-compose.yml
└── README.md
```

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/meow-qa-bot.git
cd meow-qa-bot
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，填入你的配置
```

### 3. Docker 部署（推荐）

```bash
docker-compose up -d
```

### 4. 手动部署

```bash
# 安装依赖
pip install -r requirements.txt

# 启动后端
cd backend
uvicorn main:app --host 0.0.0.0 --port 8001

# 启动 Bot（另一个终端）
cd bot
python main.py
```

## ⚙️ 配置说明

### 环境变量

| 变量名              | 说明              | 示例                                                      |
| ------------------- | ----------------- | --------------------------------------------------------- |
| `DISCORD_BOT_TOKEN` | Discord Bot Token | `MTQ0MTM...`                                              |
| `BACKEND_URL`       | 后端 API 地址     | `http://localhost:8001`                                   |
| `BOT_ID`            | Bot 标识符        | `default`                                                 |
| `DATA_DIR`          | 数据存储目录      | `./data`                                                  |
| `ADMIN_PASSWORD`    | 后台管理密码      | `your_password`                                           |
| `LLM_BASE_URL`      | LLM API 地址      | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `LLM_API_KEY`       | LLM API 密钥      | `your_api_key`                                            |
| `LLM_MODEL`         | LLM 模型名称      | `gemini-2.0-flash`                                        |

### 获取 Discord Bot Token

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 创建新应用或选择已有应用
3. 进入 Bot 设置，复制 Token
4. 开启 `Message Content Intent`

### 获取 Gemini API Key

1. 前往 [Google AI Studio](https://aistudio.google.com/)
2. 创建 API Key
3. 官方文档：https://ai.google.dev/gemini-api/docs/openai

## 📖 使用方法

### Discord 中使用

- **@机器人** 或 **回复机器人消息** 即可触发对话
- 支持发送图片让机器人分析

### 后台管理

访问 `http://your-server:8001` 进入管理后台：

- **BOT 管理** - 创建和管理多个机器人
- **知识库** - 添加自定义问答知识
- **API 设置** - 配置 LLM 接口
- **游戏管理** - 管理商店和用户数据
- **用户记忆** - 查看和编辑用户记忆
- **统计** - 查看使用统计

## 🎮 游戏系统

- **签到** - 每日签到获取货币
- **好感度** - 与机器人互动提升好感
- **商店** - 使用货币购买礼物
- **排行榜** - 查看货币和好感度排名

## 📝 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
