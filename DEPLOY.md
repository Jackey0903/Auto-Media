# 🚀 部署指南 (Deployment Guide) - 7x24h 全自动运行

> **“一次配置，永久躺平。”**

本指南将带你从零开始，把这套「小红书自动内容矩阵系统」部署到你的服务器上。部署完成后，系统将完全脱离人工干预，每小时自动为你寻找热点并发布爆款笔记。

---

## 📋 准备工作

在开始之前，请确保你拥有一台 Linux 服务器和基本的网络环境。

1.  **服务器配置**:
    *   **系统**: 推荐 Ubuntu 20.04 或 22.04 LTS。
    *   **内存**: 至少 **2GB RAM** (虽然我们的代码很轻量，但内置的无头浏览器稍吃内存)。
    *   **硬盘**: 预留 10GB 空间。

2.  **环境依赖**:
    *   **Docker & Docker Compose**: 这是能够“一键运行”的关键。如果你还没安装，一行命令搞定：
        ```bash
        curl -fsSL https://get.docker.com | bash
        ```

3.  **网络畅通**:
    *   服务器需要能访问 **GitHub** (拉取代码)。
    *   需要能访问 **OpenAI/Claude API** (或国内中转 API)。
    *   需要能访问 **Unsplash/Google 图片** (用于自动配图)。

---

## 🛠️ 第一步：代码就位

登录你的服务器，找个喜欢的目录，把代码拉下来：

```bash
git clone https://github.com/Jackey0903/Auto-Media.git
cd Auto-Media
```

---

## ⚙️ 第二步：配置账号 (关键)

这是最重要的一步。系统需要知道你是谁，以及用哪个 Key 去调用大模型。

### 1. 准备配置文件

我们需要准备两个文件。推荐的做法是**在本地电脑上配置好，然后上传到服务器**，这样最不容易出错。

#### A. `app_config.json` (API配置)

在本地项目的 `xiaohongshu/config/` 目录下找到或新建 `app_config.json`，填入你的 Key：

```json
{
  "llm_api_key": "sk-your-openai-key-here",
  "openai_base_url": "https://api.openai.com/v1",
  "default_model": "gpt-4o",
  "tavily_api_key": "tvly-your-tavily-key",
  "jina_api_key": "jina-optional-key"
}
```

#### B. `cookies.json` (小红书登录信息)

这是系统能自动发帖的通行证。

1.  **在本地电脑运行项目**：
    *   如果你是 Mac：运行 `./xiaohongshu-mcp-darwin-arm64`
    *   如果你是 Windows/Linux：运行对应版本的 MCP 服务或源码。
2.  **扫码登录**：终端会跳出二维码，用小红书 App 扫码登录。
3.  **获取文件**：登录成功后，会在 `xiaohongshu-mcp/cookies/` 目录下生成一个 `cookies.json` 文件。**这个文件就是你的登录凭证，请妥善保管！**

### 2. 上传到服务器

使用 SCP、FTP 或哪怕是直接复制粘贴内容，将这两个文件放到服务器的对应位置：

*   `app_config.json`  ➡️  `Auto-Media/xiaohongshu/config/app_config.json`
*   `cookies.json`     ➡️  `Auto-Media/xiaohongshu-mcp/cookies/cookies.json`

---

## 🐳 第三步：一键启动

见证奇迹的时刻到了。在服务器的 `Auto-Media` 根目录下，运行：

```bash
docker compose up -d --build
```

系统会开始构建镜像（第一次可能需要几分钟，别急，去喝杯咖啡☕️）。
当看到 `Creating self-media-mcp ... done` 和 `Creating self-media-scheduler ... done` 时，恭喜你，部署成功！

---

## 🔍 第四步：检查状态

想看看它在干什么？

**查看实时日志**：
```bash
# 就像在看直播一样，看着它思考、写作、发帖
docker compose logs -f app
```

**查看运行状态**：
```bash
docker compose ps
```
只要状态显示 `Up`，就说明它正如常工作。

---

## 💡 进阶技巧 & 常见问题

### Q: 它是怎么跑的？(调度策略)
默认情况下，系统每 **1 小时** 会醒来一次，去全网抓取最新的 AI/科技热点，然后生成一篇笔记发布。
如果你想修改频率（比如改成每天一次），请修改 `xiaohongshu/scheduler.py` 文件中的：
```python
schedule.every(1).hours.do(job)  # 改成 .days 就是每天
```
修改后记得重新运行 `docker compose up -d --build`。

### Q: 怎么手动触发一次任务？
不想等一小时？直接进容器里踹它一脚：
```bash
docker compose exec app python scheduler.py
```
它会立即开始工作。

### Q: Cookie 失效了怎么办？
小红书的 Cookie 通常能管几天到几周。如果日志里报 "Cookie 失效" 或 "登录过期"：
1.  在本地重新扫码登录，生成新的 `cookies.json`。
2.  把新文件上传覆盖服务器上的旧文件。
3.  重启服务：`docker compose restart mcp-server`。

### Q: 构建太慢怎么办？
我们在 Dockerfile 中贴心地配置了 **阿里云镜像源**，国内服务器下载依赖也会飞快。如果还是很慢，检查一下服务器的 DNS 设置。

---

**祝您的账号流量起飞！📈**
