# Self-Media 小红书自动化运营系统

这是一个自用的全自动化小红书运营工具，旨在通过 AI 技术实现从"选题挖掘"到"内容发布"的全流程自动化。

项目的设计理念是**解放双手**：你只需要提供一个简单的想法，剩下的即刻交给系统完成。

---

## 系统架构

项目主要由两部分组成，模拟了真人的操作逻辑：

### 1. 执行端 (Hand) —— `xiaohongshu-mcp`

这是系统的"手"。它基于 MCP (Model Context Protocol) 协议，通过 Playwright 技术直接控制浏览器。

- **作用**：模拟真人操作，包括登录账号、上传图片、填写文案、点击发布等。
- **优势**：避开复杂的 API 逆向，直接在浏览器层面操作，安全性更高，且能通过复用浏览器上下文保持登录状态。

### 2. 控制端 (Brain) —— `xiaohongshu` (Web App)

这是系统的"脑"。它是一个基于 FastAPI 的 Web 应用程序。

- **作用**：
  - **思考**：利用大模型 (如 GPT-4, Claude, GLM-4) 生成符合小红书调性的文案。
  - **搜索**：调用 Tavily 等搜索工具获取最新的联网信息。
  - **调度**：指挥"执行端"进行具体的操作。

---

## 快速开始

### 环境准备

确保你的电脑已安装：

- **Python 3.8+**
- **Node.js 18+** (运行 MCP 服务必须)

### 第一步：启动执行端 (Mcp Server)

这个服务负责接管浏览器，需要一直保持运行。

打开一个新的终端窗口：

```bash
# 1. 进入 MCP 服务目录
cd xiaohongshu-mcp-darwin-arm64

# 2. 启动服务
./xiaohongshu-mcp-darwin-arm64
```

> **首次运行提示**：终端会弹出一个二维码，请使用小红书 App 扫码登录。登录成功后，Session 信息会自动保存，后续无需重复登录。

### 第二步：启动控制端 (Web App)

这是你的操作界面。

打开另一个终端窗口：

```bash
# 1. 进入 Web 应用目录
cd xiaohongshu

# 2. 启动应用
python app.py
```

启动成功后，浏览器访问：[http://localhost:8080](http://localhost:8080)

---

## 使用指南

### 1. 基础配置

进入 Web 界面后，点击左下角的 **⚙️ 设置** 图标：

- **LLM API Key**：填写你的大模型 API 密钥 (推荐 SiliconFlow 或 OpenAI)。
- **Base URL**：模型服务的接口地址 (例如 SiliconFlow 为 `https://api.siliconflow.cn/v1`)。
- **Tavily API Key**：(强烈推荐) 用于联网搜索最新素材，没有它 AI 只能"瞎编"。

### 2. 开始创作

回到首页：

1. 在输入框填写主题，例如 "2024年最值得买的数码产品"。
2. 点击 **✨ 生成**。
3. 系统会自动执行以下流程：
   - **联网搜索**：获取最新的相关资讯。
   - **撰写文案**：生成标题、正文、标签。
   - **智能配图**：自动搜索无版权高清配图。
   - **自动发布**：直接发布到你的小红书账号。

### 3. 如何停止程序

- **正常停止**：在运行程序的终端窗口，按 **`Ctrl + C`** 即可。
- **强制停止**（如果提示端口占用）：
  ```bash
  lsof -ti :8080 | xargs kill -9
  ```

---

## 自动发布任务管理 (进阶)

除了手动在 Web 界面生成，系统还支持后台自动定时发布。

### 1. 启动任务

你可以使用 Docker Compose 在后台运行特定的发布任务。

**方式一：启动 AI 新闻速览 (默认模式)**
```bash
# 后台启动 (每小时自动执行)
docker compose run -d --name ai_news app python scheduler.py --mode general

# 立即执行一次 (测试用)
docker compose run --rm app python scheduler.py --run-now --mode general
```

**方式二：启动 AI 论文速览**
```bash
# 后台启动 (每小时自动执行)
docker compose run -d --name ai_paper app python scheduler.py --mode paper_analysis

# 立即执行一次 (测试用)
docker compose run --rm app python scheduler.py --run-now --mode paper_analysis
```

**方式三：启动知乎深度解读**
```bash
# 后台启动
docker compose run -d --name ai_zhihu app python scheduler.py --mode zhihu
```

### 2. 停止任务

如果你在后台启动了任务 (使用了 `-d` 参数)，可以使用以下命令停止它们：

```bash
# 停止 AI 新闻任务
docker stop ai_news && docker rm ai_news

# 停止 AI 论文任务
docker stop ai_paper && docker rm ai_paper

# 停止所有任务
docker stop $(docker ps -q --filter ancestor=auto-media-app)
```

### 3. 查看日志

查看后台任务的运行情况：

```bash
docker logs -f ai_news
# 或
docker logs -f ai_paper
```

---

## 常见问题排查

1. **发布时提示 "MCP Server not initialized"**

   - 检查 `xiaohongshu-mcp` 那个终端窗口是否被关闭了，它必须一直运行。
2. **内容生成卡在搜索阶段**

   - 首次使用搜索功能时，系统会自动下载必要的 MCP 搜索组件，可能会消耗几分钟时间，请耐心等待，不要关闭程序。
3. **端口 8080 被占用**

   - 如果重启 `app.py` 时报错端口占用，可以使用以下命令清理：
     ```bash
     lsof -ti :8080 | xargs kill -9
     ```

---

**Happy Creating! 🚀**
