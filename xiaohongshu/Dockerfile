# 使用官方 Python 3.11 基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 避免 pip 版本检查警告
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖和 Node.js (用于运行 tavily-mcp 和 jina-mcp-tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制当前目录下的所有文件到工作目录
COPY . .

# 创建必要的目录
RUN mkdir -p config cache logs

# 设置启动命令 (默认为调度器模式)
CMD ["python", "scheduler.py"]
