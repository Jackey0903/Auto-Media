# 🚀 部署指南 (Deployment Guide) - 7x24h 全自动运行

> **“一次配置，永久躺平。”**

本指南将带你从零开始，把这套「小红书自动内容矩阵系统」部署到你的服务器上。部署完成后，系统将完全脱离人工干预，每小时自动为你寻找热点并发布爆款笔记。

---

## 📋 硬件与环境准备

在购买或准备服务器时，为了保证系统稳定运行（尤其是其中的 Chrome 浏览器服务），请参考以下建议：

### 1. 硬件配置推荐

| 配置 | 状态 | 说明 |
| :--- | :--- | :--- |
| **2核 4G** | ✅ **推荐** | 最稳的配置。Chrome 吃内存，4G 能保证长期运行不崩溃。 |
| **2核 2G** | ⚠️ **极限** | **必须配置 2G Swap (虚拟内存)**，否则极易因为内存不足被系统杀掉进程。 |
| **1核 1G** | ❌ 不推荐 | 很难跑起来，除非你极其精通 Linux 优化。 |

*   **硬盘**: 预留 10GB 以上空间。
*   **系统**: 推荐 Ubuntu 20.04 或 22.04 LTS。

### 2. 软件依赖

*   **Docker & Docker Compose**: 一键与系统解耦，必装。
    ```bash
    curl -fsSL https://get.docker.com | bash
    ```

---

## 🛠️ 部署流程

### 第一步：代码就位

登录服务器，拉取代码：

```bash
git clone https://github.com/Jackey0903/Auto-Media.git
cd Auto-Media
```

### 第二步：配置账号 (关键)

我们需要将本地配置好的两个核心文件上传到服务器。

1.  **`app_config.json`** (API Key 配置)
    *   本地路径：`xiaohongshu/config/app_config.json`
    *   服务器路径：`Auto-Media/xiaohongshu/config/app_config.json`
    *   *内容示例：`{"llm_api_key": "sk-...", "tavily_api_key": "tvly-..."}`*

2.  **`cookies.json`** (小红书登录凭证)
    *   本地路径：`xiaohongshu-mcp/cookies/cookies.json`
    *   服务器路径：`Auto-Media/xiaohongshu-mcp/cookies/cookies.json`
    *   *获取方式：在本地运行 `xiaohongshu-mcp` 扫码登录后生成。*

### 第三步：一键启动

在服务器的 `Auto-Media` 根目录下运行：

```bash
docker compose up -d --build
```

---

## 💡 常见问题 & 故障排查

### 🔴 问题 1：构建镜像时卡死 / 报错 `i/o timeout`
**原因**：国内服务器连接 Docker Hub 网络不通。
**解决**：配置国内加速镜像源。

```bash
# 1. 创建配置目录
sudo mkdir -p /etc/docker

# 2. 写入加速源 (DaoCloud/1Panel 等)
sudo tee /etc/docker/daemon.json <<-'EOF'
{
    "registry-mirrors": [
        "https://docker.m.daocloud.io",
        "https://docker.1panel.live",
        "https://hub.rat.dev"
    ]
}
EOF

# 3. 重启 Docker
sudo systemctl daemon-reload
sudo systemctl restart docker
```

### 🔴 问题 2：运行一段时间后服务挂了 / 进程消失 (OOM)
**原因**：内存不足 (尤其是 2G 内存机器)，Chrome 被系统杀掉了。
**解决**：增加 2GB 虚拟内存 (Swap)。

```bash
# 1. 创建 2G 的 Swap 文件
sudo fallocate -l 2G /swapfile

# 2. 设置权限并启用
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 3. 设置开机自启
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 🔴 问题 3：提示 Cookie 失效 / 无法发布
**解决**：小红书 Cookie 有有效期。
1.  本地重新扫码登录，生成新 `cookies.json`。
2.  上传覆盖服务器文件。
3.  重启服务：`docker compose restart mcp-server`。

---

## ⚙️ 高级配置

### 修改自动发布频率
推荐通过环境变量配置，无需改代码。

在 `docker-compose.yml` 的 `app.environment` 增加：

```yaml
- AUTO_PUBLISH_INTERVAL_HOURS=1      # 每 N 小时发布一次（默认 1）
# - AUTO_PUBLISH_DAILY_AT=10:30      # 若设置该项，则按每天固定时间发布（优先级高于间隔）
- AUTO_PUBLISH_RUN_ON_START=true     # 容器启动后是否立即执行一次（默认 true）
- AUTO_PUBLISH_DOMAIN=AI             # 热点领域，如 AI/融资/论文/机器人
- AUTO_PUBLISH_CONTENT_TYPE=general  # general 或 paper_analysis
```

修改后执行：

```bash
docker compose up -d --build
```

日志文件：`xiaohongshu/logs/scheduler.log`

---

**祝您的账号流量长虹！📈**
