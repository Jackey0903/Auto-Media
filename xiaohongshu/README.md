# Auto-Media (小红书内容自动生成系统)

这是一个基于 AI 的全自动化自媒体内容生成与发布系统，专为小红书平台设计。它集成了多模态搜索、智能写作、图片处理和自动发布功能，能够一键生成高质量、去 AI 味的爆款笔记。

![Web UI](xiaohongshu_web_ui_1770774117525.png)

## ✨ 核心特性

*   **🤖 智能体工作流**：
    *   **Step 1 信息检索**：通过 Tavily/Jina 搜索全网最新资讯、热门话题或学术论文。
    *   **Step 2 内容创作**：基于 GPT-4o/Claude-3.5 撰写深度文章，严格去 AI 味，风格自然流畅。
    *   **Step 3 格式适配**：自动提取标签、缩写标题（<20字）、适配小红书排版。
*   **🖼️ 强大的配图系统**：
    *   自动搜索并筛选 5-7 张高质量相关图片。
    *   内置冗余机制：搜索 10+ 张备选图，自动过滤失效链接，确保发布成功率。
    *   智能图片验证：自动检测图片有效性、尺寸和格式。
*   **🛡️ 极致稳定性**：
    *   **防 500 错误**：修复了 Paper Analysis 等模式下的 ID 匹配问题。
    *   **防 Context Overflow**：内置超长文本智能总结功能。
    *   **防发布失败**：自动重试机制，标题/内容超长自动缩减。
*   **📑 多种生成模式**：
    *   **通用模式**：适合热点新闻、科普介绍、好物分享。
    *   **论文分析**：输入论文标题或主题，自动生成深度解读笔记。

## 🚀 快速开始

### 1. 环境准备

确保已安装 Python 3.8+ 和 Node.js 18+。

```bash
# 克隆仓库
git clone https://github.com/Jackey0903/Auto-Media.git
cd Auto-Media

# 安装 Python 依赖
pip install -r requirements.txt

# 启动 MCP 服务 (需先安装依赖)
cd xiaohongshu-mcp-darwin-arm64
npm install  # 如果是源码运行
# 或者直接运行预编译的二进制文件
./xiaohongshu-mcp-darwin-arm64
```

### 2. 配置

在 Web UI (`http://localhost:8080`) 中配置以下参数：

*   **LLM API Key**: OpenAI/Claude/SiliconFlow 等兼容接口的 Key。
*   **Base URL**: LLM API 的基础地址。
*   **Tavily API Key**: 用于联网搜索 (必需)。
*   **Jina API Key**: (可选) 用于更深度的网页抓取。

### 3. 运行 Web 应用

```bash
# 回到项目根目录
python app.py
```

浏览器访问 `http://localhost:8080` 即可开始使用。

## 🛠️ 故障排查

如果遇到问题，请查看 logs 目录下的日志文件。常见问题解决方案请参考 [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)。

## 📝 最近更新

*   **2026-02-12**: 
    *   优化写作提示词，去除 AI 味，风格更自然。
    *   升级配图逻辑，支持 5-7 张图片，增加搜索冗余。
    *   修复多项发布稳定性 bug。

## 📄 License

MIT
