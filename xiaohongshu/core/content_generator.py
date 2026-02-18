"""
内容生成器模块
基于原有的RealToolExecutor重构，用于生成和发布小红书内容
"""
import json
import logging
import os
import tempfile
import shutil
import asyncio
import httpx
from typing import Any, Dict, List, Optional
from core.xhs_llm_client import Configuration, Server, LLMClient, Tool
from core.server_manager import server_manager

logger = logging.getLogger(__name__)


class TavilyQuotaExceeded(Exception):
    """Tavily API配额已用尽异常"""
    pass


class ContentGenerator:
    """内容生成器 - 负责生成小红书内容并发布"""

    def __init__(self, config: Dict[str, Any]):
        """初始化内容生成器

        Args:
            config: 应用配置字典
        """
        self.config = config
        self.servers = []
        self.llm_client = None
        self.context = None
        self.context_file = None
        self._owns_context_file = False

        # 初始化Configuration
        self.mcp_config = self._create_mcp_config()

    def _create_mcp_config(self) -> Configuration:
        """创建MCP配置对象"""
        # 临时设置环境变量供Configuration使用
        os.environ['LLM_API_KEY'] = self.config.get('llm_api_key', '')
        os.environ['OPENAI_BASE_URL'] = self.config.get('openai_base_url', '')
        os.environ['DEFAULT_MODEL'] = self.config.get('default_model', 'claude-sonnet-4-20250514')

        return Configuration()

    def _prepare_context_file(self, context_file: Optional[str] = None) -> tuple[str, bool]:
        """准备上下文文件"""
        if context_file:
            return context_file, False

        # 使用原项目的模板文件
        script_dir = str(parent_dir)
        template_candidates = [
            os.path.join(script_dir, "agent_context_temple.xml"),
            os.path.join(script_dir, "agent_context.xml"),
        ]

        template_path = None
        for candidate in template_candidates:
            if os.path.exists(candidate):
                template_path = candidate
                break

        if template_path is None:
            raise FileNotFoundError("未找到agent context XML模板文件")

        # 创建临时目录
        temp_dir = tempfile.gettempdir()
        fd, temp_path = tempfile.mkstemp(prefix="agent_context_", suffix=".xml", dir=temp_dir)
        os.close(fd)

        try:
            shutil.copyfile(template_path, temp_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

        return temp_path, True

    async def validate_image_urls(self, image_urls: List[str], timeout: float = 20.0) -> List[str]:
        """验证图片 URL 的有效性,返回可访问的图片 URL 列表
        
        Args:
            image_urls: 待验证的图片 URL 列表
            timeout: 每个 URL 的超时时间(秒，默认20s)
            
        Returns:
            List[str]: 有效的图片 URL 列表
        """
        if not image_urls:
            return []

        # 确保输入是列表
        if not isinstance(image_urls, list):
            logger.warning(f"image_urls 不是列表: {type(image_urls)}")
            return []

        valid_urls = []

        async def check_url(url: str) -> Optional[str]:
            """检查单个 URL 是否可访问且为图片，支持重试和多种验证方法"""
            # 跳过空值和无效格式
            if not url or not isinstance(url, str) or not url.startswith(('http://', 'https://')):
                return None

            # 检查是否为占位符
            if any(placeholder in url.lower() for placeholder in ['example.com', 'placeholder', 'image1.jpg', 'image2.jpg', 'image3.jpg', 'test.jpg']):
                logger.warning(f"跳过占位符URL: {url}")
                return None

            # 已知会阻止直接下载的域名（防盗链），MCP Server 无法下载这些图片
            blocked_domains = [
                'freepik.com', 'smzdm.com', 'zdmimg.com', 'qiantucdn.com',
                'qnam.smzdm.com', 'am.zdmimg.com', 'preview.qiantucdn.com',
                'shutterstock.com', 'gettyimages.com', 'istockphoto.com',
                'dreamstime.com', 'stock.adobe.com', '123rf.com',
                # 国内有防盗链的CDN - Python验证能通过但Go MCP Server下载会403
                'inews.gtimg.com', 'gtimg.com', 'sinaimg.cn', 'mmbiz.qpic.cn',
            ]
            if any(domain in url.lower() for domain in blocked_domains):
                logger.warning(f"⛔ 跳过防盗链域名: {url}")
                return None

            # 重试机制：最多尝试2次
            for attempt in range(2):
                try:
                    # 判断是否需要禁用SSL验证（针对已知有证书问题的CDN）
                    verify_ssl = True
                    problematic_domains = ['9to5google.com', 'techkv.com', 'cdn.example.com']
                    if any(domain in url for domain in problematic_domains):
                        verify_ssl = False

                    async with httpx.AsyncClient(
                        timeout=timeout,
                        follow_redirects=True,
                        verify=verify_ssl,
                        trust_env=False
                    ) as client:
                        # 直接用 GET 下载前 4KB 来验证（比 HEAD 更可靠，能检测到防盗链）
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                            'Accept': 'image/*,*/*;q=0.8',
                        }
                        response = await client.get(url, headers=headers)

                        if response.status_code in [200, 206]:
                            content_type = response.headers.get('content-type', '').lower()
                            if content_type.startswith('image/'):
                                logger.info(f"✓ 图片URL有效(GET): {url}")
                                return url
                            else:
                                image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico']
                                if any(ext in url.lower() for ext in image_extensions):
                                    logger.info(f"✓ 图片URL有效(按扩展名): {url}")
                                    return url
                                logger.warning(f"URL不是图片类型 (Content-Type: {content_type}): {url}")
                        else:
                            logger.warning(f"图片URL返回状态码 {response.status_code}: {url}")

                    # 验证失败，重试
                    if attempt < 1:
                        await asyncio.sleep(1)
                        logger.info(f"重试验证URL (第{attempt + 2}次): {url}")
                        continue
                    else:
                        return None

                except httpx.TimeoutException:
                    if attempt < 1:
                        logger.warning(f"图片URL访问超时(第{attempt + 1}次)，准备重试: {url}")
                        await asyncio.sleep(1)
                        continue
                    else:
                        logger.warning(f"图片URL访问超时(已重试): {url}")
                        return None
                except Exception as e:
                    if attempt < 1:
                        logger.warning(f"图片URL验证失败(第{attempt + 1}次) {url}: {e}，准备重试")
                        await asyncio.sleep(1)
                        continue
                    else:
                        logger.warning(f"图片URL验证失败(已重试) {url}: {e}")
                        return None

            return None

        # 并发检查所有 URL
        tasks = [check_url(url) for url in image_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 收集有效的 URL
        for result in results:
            if isinstance(result, str) and result:
                valid_urls.append(result)

        logger.info(f"图片URL验证完成: {len(valid_urls)}/{len(image_urls)} 个有效")
        return valid_urls

    async def summarize_content(self, content: str, max_length: int = 5000) -> str:
        """使用 LLM 总结过长的内容"""
        try:
            logger.info(f"内容过长 ({len(content)}字符)，正在使用 LLM 进行总结...")
            
            prompt = f"""
            请总结以下内容，保留核心信息、关键数据和重要结论。
            总结后的长度控制在 {max_length} 字符以内。
            
            原始内容：
            {content[:50000]}  # 限制输入长度防止 API 报错
            """
            
            messages = [
                {"role": "user", "content": prompt}
            ]
            
            # 使用新添加的 chat 方法
            response = self.llm_client.chat(messages)
            summary = response.choices[0].message.content
            
            logger.info(f"总结完成，压缩率: {len(summary)/len(content):.2%}")
            return f"[Content Summarized by AI]\n{summary}"
            
        except Exception as e:
            logger.error(f"总结内容失败: {e}")
            # 降级处理：直接截断
            return content[:20000] + "...(content truncated due to length limit and summarization failure)"

    def get_research_plan(self, user_topic: str, content_type: str = "general") -> List[Dict[str, Any]]:
        """根据用户主题和内容类型生成研究计划（自然叙事流版）"""

        if content_type == "paper_analysis":
            return self.get_paper_analysis_plan(user_topic)
        if content_type == "zhihu":
            return self.get_zhihu_plan(user_topic)
        
        # 定义更严格的“去AI味”约束
        style_guide = (
            "1. **绝对禁止使用列表**：严禁使用 1. 2. 3. 或 - 等Markdown列表符号。必须将内容融合在段落中。\n"
            "2. **口语化连接**：使用'其实'、'不过'、'没想到'、'也就是说'等自然的连接词，而不是'首先/其次/最后'。\n"
            "3. **情绪递进**：第一段抛出话题或反差，中间段落讲细节和感受，最后一段给建议。像写日记或发朋友圈一样自然。\n"
            "4. **标点符号**：多用空格、波浪号(~)或简单的逗号句号，少用感叹号。"
        )

        return [
            {
                "id": "step1",
                "title": f"素材搜集：{user_topic}",
                "description": (
                    f"请搜索关于「{user_topic}」的**最新**信息（重点关注最近24-48小时内的新闻）。\n"
                    f"重点寻找：\n"
                    f"1. **刚刚发生的具体事件/更新**（必须是当下的热点，拒绝旧闻）。\n"
                    f"2. **网友/用户的真实评价**（好评或吐槽均可，用于增加真实感）。\n"
                    f"3. **争议点或反直觉的点**（用于制造文章的张力）。\n"
                    f"4. 搜集10张以上相关图片链接（HTTPS），确保有图可用。"
                ),
                "depends on": []
            },
            {
                "id": "step2",
                "title": f"撰写自然流初稿：{user_topic}",
                "description": (
                    f"基于搜索结果，写一篇关于「{user_topic}」的小红书笔记。\n"
                    f"**核心要求：请完全放弃说明文的写法，改为'叙事流'。**\n\n"
                    f"{style_guide}\n\n"
                    f"**写作框架参考（不要直接抄框架名）：**\n"
                    f"- **切入**：从一个具体的场景、瞬间或痛点切入。例如'昨晚熬夜试了下...' 或 '最近朋友圈都被刷屏了...'。\n"
                    f"- **展开**：用大白话讲清楚这事儿到底牛在哪里，或者坑在哪里。不要堆砌参数，要讲体验。\n"
                    f"- **收尾**：给出一个真诚的建议，或者抛出一个互动问题。\n\n"
                    f"**语气**：像是一个懂行的朋友在饭桌上跟你聊天，而不是在讲台上做汇报。"
                ),
                "depends on": ["step1"]
            },
            {
                "id": "step3",
                "title": "排版与发布",
                "description": (
                    "对内容进行最终的格式调整并发布。\n"
                    "1. **标题优化**：生成一个吸引人的标题（20字内），不要做标题党，但要有信息量。\n"
                    "2. **正文清洗**：\n"
                    "   - 再次检查：确保全文没有 Markdown 列表符号（如 - 或 1.）。\n"
                    "   - 确保分段合理，每段不要太长（3-5行），通过空行分隔段落，视觉上更轻松。\n"
                    "   - 适当插入 3-4 个 Emoji，放在句子中间或段末烘托语气，不要堆叠在开头。\n"
                    "3. **图片选择**：从Step1中选取最匹配内容的5张图片。\n"
                    "4. **Tags**：生成5个相关标签。\n"
                    "5. **动作**：调用 publish_content 工具发布。"
                ),
                "depends on": ["step1", "step2"]
            }
        ]

    def get_zhihu_plan(self, user_topic: str) -> List[Dict[str, Any]]:
        """生成知乎回答专用工作流（深度专业版）"""
        
        zhihu_style = (
            "1. **开头**：直接回答问题，不要客套（如'这是个好问题'）。不用'作为一个xx'。可以先给结论，再展开。\n"
            "2. **正文**：可以分点，但每点要有实质内容，不要只是列大纲。举例子比讲道理有效。别堆砌术语。\n"
            "3. **结尾**：不用'希望有帮助'，总结核心观点或承认局限性。\n"
            "4. **禁止**：'首先我们要明确一个概念'、'从几个方面分析'、'相信通过以上分析'。\n"
            "5. **鼓励**：用'我认为'、'在我看来'，用个人经历佐证。"
        )

        return [
            {
                "id": "step1_zhihu",
                "title": f"深度调研：{user_topic}",
                "description": (
                    f"搜索关于「{user_topic}」的深度信息和多方观点。\n"
                    f"重点寻找：\n"
                    f"1. **核心事实与数据**：不仅是新闻，还要有背景数据或技术原理。\n"
                    f"2. **不同立场的观点**：支持方、反对方、中立方的看法。\n"
                    f"3. **专业深度分析**：行业报告、专家解读、技术文档。"
                ),
                "depends on": []
            },
            {
                "id": "step2_write_zhihu",
                "title": "撰写知乎回答",
                "description": (
                    f"基于调研结果，以知乎答主的身份写一篇深度回答。\n"
                    f"**核心要求：专业、有深度、有观点、拒绝水文。**\n\n"
                    f"{zhihu_style}\n\n"
                    f"写作建议：\n"
                    f"- 像一个行业老兵在分享经验，而不是AI在背书。\n"
                    f"- 每一个论点后面最好都要跟一个具体的例子或数据。\n"
                    f"- 保持逻辑的连贯性，但不要用僵硬的连接词。\n"
                    f"- 字数控制在 1000-2000 字。"
                ),
                "depends on": ["step1_zhihu"]
            },
            {
                "id": "step3_publish_zhihu",
                "title": "发布知乎回答",
                "description": (
                    "1. **标题**：知乎通常是在问题下回答，如果是写文章，标题要专业且引发思考。\n"
                    "2. **正文**：进行最终润色，确保没有AI味（再次检查禁止词汇）。\n"
                    "3. **图片**：插入3-5张有信息增量的图表或配图。\n"
                    "4. **发布**：调用 publish_content 发布。"
                ),
                "depends on": ["step1_zhihu", "step2_write_zhihu"]
            }
        ]

    def get_paper_analysis_plan(self, user_topic: str) -> List[Dict[str, Any]]:
        """生成论文分析专用工作流（通俗解读版）"""
        return [
            {
                "id": "step1_paper",
                "title": f"论文检索：{user_topic}",
                "description": (
                    f"搜索「{user_topic}」相关的最新高质量论文（arXiv, CVPR, Nature等）。\n"
                    f"寻找那些**确实解决了具体问题**或**效果惊人**的研究。\n"
                    f"搜集论文链接、核心图表（架构图、效果对比图）的图片链接。"
                ),
                "depends on": []
            },
            {
                "id": "step2_analysis",
                "title": "通俗化解读",
                "description": (
                    "请将这篇论文“翻译”成人话。\n"
                    "**严禁使用 '摘要-方法-实验-结论' 的八股文结构。**\n\n"
                    "请按照以下逻辑进行**连续的段落写作**：\n"
                    "1. **背景/痛点**：以前大家做这个东西有什么痛点？（比如：以前的生成视频都很假，而且很慢...）\n"
                    "2. **高光时刻**：这篇论文究竟做到了什么？（比如：现在居然能实时生成了，而且连毛孔都看得清...）\n"
                    "3. **原理解密（简单说）**：它大概是怎么做到的？用比喻的修辞手法，不要堆砌术语。\n"
                    "4. **我的看法**：这个技术未来会对我们有什么影响？\n\n"
                    "要求：\n"
                    "- 全文必须是自然的段落文本。\n"
                    "- 不要使用任何列表符号。\n"
                    "- 语言要通俗，假设读者是你的大一学弟学妹。"
                ),
                "depends on": ["step1_paper"]
            },
            {
                "id": "step3_format",
                "title": "发布论文解读",
                "description": (
                    "1. **标题**：突出论文的“爽点”或“颠覆性”。\n"
                    "2. **正文**：保持Step2生成的自然段落结构，检查不要有生硬的转折词。\n"
                    "3. **图片**：必须包含论文的效果图或架构图（5张左右）。\n"
                    "4. **发布**：调用 publish_content 发布。"
                ),
                "depends on": ["step1_paper", "step2_analysis"]
            }
        ]

    async def initialize_servers(self):
        """初始化MCP服务器连接"""
        try:
            # 动态构建服务器配置（使用 self.config，不从文件读取）
            server_config = {
                "mcpServers": {
                    "jina-mcp-tools": {
                        "args": ["jina-mcp-tools"],
                        "command": "npx",
                        "env": {
                            "JINA_API_KEY": self.config.get('jina_api_key', '')
                        }
                    },
                    "tavily-mcp": {
                        "command": "npx",
                        "args": [
                            "-y",
                            "tavily-mcp@latest"
                        ],
                        "env": {
                            "TAVILY_API_KEY": self.config.get('tavily_api_key', '')
                        }
                    },
                    "xhs": {
                        "type": "streamable_http",
                        "url": self.config.get('xhs_mcp_url', 'http://localhost:18060/mcp')
                    }
                }
            }

            # 创建服务器实例
            self.servers = [
                Server(name, srv_config)
                for name, srv_config in server_config["mcpServers"].items()
            ]

            # 初始化LLM客户端
            self.llm_client = LLMClient(
                self.config.get('llm_api_key'),
                self.config.get('openai_base_url'),
                self.config.get('default_model', 'claude-sonnet-4-20250514')
            )

            # 初始化所有服务器（带超时和错误隔离）
            # npx 首次下载可能较慢，给 120 秒超时
            INIT_TIMEOUT = 120
            initialized_servers = []
            for server in self.servers:
                try:
                    await asyncio.wait_for(server.initialize(), timeout=INIT_TIMEOUT)
                    logger.info(f"✅ 成功初始化服务器: {server.name}")
                    initialized_servers.append(server)
                except asyncio.TimeoutError:
                    logger.error(f"⏰ 初始化服务器 {server.name} 超时（{INIT_TIMEOUT}秒），跳过")
                except Exception as e:
                    logger.error(f"❌ 初始化服务器 {server.name} 失败: {e}，跳过")

            # 只保留成功初始化的服务器
            self.servers = initialized_servers
            
            if not self.servers:
                raise RuntimeError("所有 MCP 服务器初始化均失败，请检查网络和配置")
            
            logger.info(f"MCP 服务器初始化完成: {len(self.servers)}/{len(server_config['mcpServers'])} 个成功")

        except Exception as e:
            logger.error(f"初始化服务器失败: {e}")
            raise

    async def get_available_tools(self) -> List[Tool]:
        """获取所有可用的工具"""
        all_tools = []
        for server in self.servers:
            try:
                tools = await server.list_tools()
                all_tools.extend(tools)
                logger.info(f"服务器 {server.name} 提供 {len(tools)} 个工具")
            except Exception as e:
                logger.error(f"从服务器 {server.name} 获取工具失败: {e}")

        return all_tools

    async def fetch_trending_topics(self, domain: str = "") -> List[Dict[str, str]]:
        """获取今日热点新闻主题

        Args:
            domain: 指定的领域（如：AI、融资、论文、机器人等）

        Returns:
            List[Dict[str, str]]: 热点主题列表，每个主题包含 title 和 summary
        """
        try:
            logger.info(f"开始获取今日热点新闻主题{f'（{domain}领域）' if domain else ''}...")

            # 优先使用全局服务器管理器
            if server_manager.is_initialized():
                logger.info("使用全局服务器管理器")
                self.servers = server_manager.get_servers()
                self.llm_client = server_manager.get_llm_client()
                available_tools = await server_manager.get_available_tools()
            else:
                logger.info("全局服务器未初始化,使用本地获取")
                # 获取可用工具
                available_tools = await self.get_available_tools()

            if not available_tools:
                logger.error("没有可用的工具")
                return []

            # 将工具转换为OpenAI格式
            openai_tools = [tool.to_openai_tool() for tool in available_tools]

            # 获取当前时间
            from datetime import datetime, timezone, timedelta
            # 使用中国时区 (UTC+8)
            china_tz = timezone(timedelta(hours=8))
            current_time = datetime.now(china_tz)
            current_date_str = current_time.strftime('%Y年%m月%d日')
            current_datetime_str = current_time.strftime('%Y年%m月%d日 %H:%M')

            logger.info(f"当前时间: {current_datetime_str}")

            # 根据是否指定领域构建不同的提示词
            if domain:
                # 构建针对不同领域的搜索策略
                domain_search_config = {
                    "AI": {
                        "keywords": ["AI", "人工智能", "大模型", "深度学习", "机器学习", "AGI"],
                        "focus": "AI技术突破、AI应用、AI公司动态"
                    },
                    "融资": {
                        "keywords": ["AI融资", "人工智能投资", "AI公司融资", "AI领域投资"],
                        "focus": "AI领域的融资事件、投资动态、AI初创公司"
                    },
                    "论文": {
                        "keywords": ["arXiv AI论文", "arXiv 人工智能", "arXiv machine learning", "arXiv deep learning", "最新AI论文"],
                        "focus": "arXiv上AI领域的最新学术论文、研究成果、技术创新"
                    },
                    "机器人": {
                        "keywords": ["AI机器人", "智能机器人", "机器人技术", "人形机器人", "工业机器人"],
                        "focus": "AI驱动的机器人技术、机器人应用、机器人公司动态"
                    }
                }

                # 获取领域配置,如果没有则使用通用AI搜索
                config = domain_search_config.get(domain, {
                    "keywords": [f"AI {domain}", f"人工智能 {domain}"],
                    "focus": f"AI {domain}领域的最新动态"
                })

                keywords_str = "、".join(config["keywords"])

                system_prompt = f"""你是一个专业的AI行业新闻分析师，擅长发现和总结AI领域的热点话题。

【当前时间】{current_datetime_str}

【领域定位】「{domain}」是人工智能(AI)大领域下的一个重要分支

请使用网络搜索工具查找「{domain}」在过去24小时内（{current_date_str}）最热门的新闻话题。

**搜索范围**：
- 主题：{config["focus"]}
- 关键词：{keywords_str}
- 时间：{current_date_str}（最近24小时）

**搜索要求**：
1. 必须使用搜索工具获取最新信息
2. 关注AI领域的{domain}相关内容
3. 优先选择{current_date_str}发布的权威内容
4. 确保信息的准确性和时效性
"""

                # 针对论文领域的特殊提示
                if domain == "论文":
                    user_prompt = f"""请搜索并列出arXiv上{current_date_str}最新发布的10篇AI相关论文。

**搜索策略**：
- 推荐关键词：{keywords_str}
- 可以组合搜索：如"{config['keywords'][0]} {current_date_str}"、"arXiv AI 最新论文"
- **重点**：优先搜索 arxiv.org 网站上的最新论文
- 关注分类：cs.AI, cs.LG, cs.CV, cs.CL, cs.RO 等AI相关类别

**信息来源**：
- 主要来源：调用搜索工具搜索网页(https://arxiv.org/search/?query=llm&searchtype=all&abstracts=show&order=-announced_date_first&size=50)
- 辅助来源：Papers with Code、AI科技媒体对论文的报道

**内容要求**：
对于每篇论文，请提供：
1. 论文标题（15-20字,可以简化）
2. 简短的研究摘要（30-50字,重点说明创新点和应用价值）

请确保这些论文都是{current_date_str}或最近几天在arXiv上发布的最新研究，与AI领域密切相关，有学术价值和实用性，适合在社交媒体上创作科普内容。

搜索完成后，请按照以下JSON格式整理结果（注意：你的最终回复必须是纯JSON格式，不要包含任何其他文字）：
```json
[
  {{
    "title": "论文标题",
    "summary": "论文摘要"
  }}
]
```
"""
                else:
                    user_prompt = f"""请搜索并列出「{domain}」在{current_date_str}最热门的10个新闻话题。

**搜索策略**：
- 推荐关键词：{keywords_str}
- 可以组合搜索：如"{config['keywords'][0]} {current_date_str}"、"{config['keywords'][0]} 今日"
- 信息来源：
  * AI领域：机器之心、量子位、新智元、AI科技评论
  * 融资领域：36氪、投资界、创业邦、IT桔子
  * 机器人领域：机器人大讲堂、机器人在线、IEEE Robotics

**内容要求**：
对于每个话题，请提供：
1. 简洁的标题（15-20字）
2. 简短的摘要说明（30-50字）

请确保这些话题都是{current_date_str}的最新内容、与AI {domain}密切相关、有热度的，适合在社交媒体上创作内容。

搜索完成后，请按照以下JSON格式整理结果（注意：你的最终回复必须是纯JSON格式，不要包含任何其他文字）：
```json
[
  {{
    "title": "话题标题",
    "summary": "话题摘要"
  }}
]
```
"""
            else:
                system_prompt = f"""你是一个专业的新闻分析师，擅长发现和总结当前的热点话题。

【当前时间】{current_datetime_str}

请使用网络搜索工具查找过去24小时内（{current_date_str}）最热门的新闻话题。
重点关注：科技、AI、互联网、社交媒体等领域的热点新闻。

**搜索要求**：
1. 必须使用搜索工具获取最新信息
2. 关注时效性，优先选择{current_date_str}发布的内容
3. 确保信息的准确性和可靠性
"""

                user_prompt = f"""请搜索并列出{current_date_str}最热门的10个新闻话题。

**搜索指引**：
- 搜索关键词示例："今日热点", "最新新闻 {current_date_str}", "科技新闻"
- 时间范围：过去24小时内
- 信息来源：主流媒体、科技媒体、官方发布

对于每个话题，请提供：
1. 简洁的标题（15-20字）
2. 简短的摘要说明（30-50字）

请确保这些话题都是{current_date_str}的最新内容、有热度的，适合在社交媒体上创作内容。

搜索完成后，请按照以下JSON格式整理结果（注意：你的最终回复必须是纯JSON格式，不要包含任何其他文字）：
```json
[
  {
    "title": "话题标题",
    "summary": "话题摘要"
  }
]
```
"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 进行多轮工具调用
            max_iterations = 5
            iteration = 0

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"热点主题检索 - 第 {iteration} 轮")

                # 获取工具调用响应
                response = self.llm_client.get_tool_call_response(messages, openai_tools)
                message = response.choices[0].message

                if message.tool_calls:
                    # 添加助手消息
                    assistant_msg = {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in message.tool_calls
                        ]
                    }
                    messages.append(assistant_msg)

                    # 执行所有工具调用
                    for tool_call in message.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            arguments = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                        except json.JSONDecodeError:
                            arguments = {}

                        logger.info(f"执行工具: {tool_name}")

                        # 查找对应的服务器并执行工具
                        tool_result = None
                        for server in self.servers:
                            tools = await server.list_tools()
                            if any(tool.name == tool_name for tool in tools):
                                try:
                                    tool_result = await server.execute_tool(tool_name, arguments)
                                    break
                                except Exception as e:
                                    logger.error(f"执行工具 {tool_name} 出错: {e}")
                                    tool_result = f"Error: {str(e)}"

                        if tool_result is None:
                            tool_result = f"未找到工具 {tool_name}"

                        # 添加工具结果消息
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(tool_result)
                        })

                    # 获取最终响应
                    final_response = self.llm_client.get_final_response(messages, openai_tools)
                    final_message = final_response.choices[0].message

                    if final_message.tool_calls:
                        # 继续下一轮
                        response = final_response
                    else:
                        # 获取最终内容并解析
                        final_content = final_message.content or ""
                        logger.info("热点主题检索完成，开始解析结果")

                        # 尝试从返回内容中提取JSON
                        topics = self._parse_topics_from_response(final_content)
                        return topics
                else:
                    # 没有工具调用，直接返回内容
                    final_content = message.content or ""
                    topics = self._parse_topics_from_response(final_content)
                    return topics

            logger.warning("达到最大迭代次数，未能完成热点主题检索")
            return []

        except Exception as e:
            # 检查是否是Tavily API错误
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "unauthorized" in error_str or "403" in error_str:
                logger.warning(f"检测到Tavily API可能受限: {e}，尝试轮换Key...")
                if await server_manager.rotate_tavily_key():
                    logger.info("Key轮换成功，重试获取热点主题...")
                    # 递归重试一次
                    return await self.fetch_trending_topics(domain)
            
            logger.error(f"获取热点主题失败: {e}", exc_info=True)
            return []

    def _parse_topics_from_response(self, content: str) -> List[Dict[str, str]]:
        """从LLM响应中解析主题列表"""
        try:
            # 1. 尝试直接解析
            try:
                topics = json.loads(content)
                if self._validate_topics(topics):
                    return topics[:20]
            except json.JSONDecodeError:
                pass

            # 2. 尝试提取 JSON 块
            import re
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
            if json_match:
                json_str = json_match.group(1)
                try:
                    topics = json.loads(json_str)
                    if self._validate_topics(topics):
                        return topics[:20]
                except json.JSONDecodeError:
                    pass

            # 3. 尝试提取数组部分
            json_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', content)
            if json_match:
                json_str = json_match.group(0)
                try:
                    topics = json.loads(json_str)
                    if self._validate_topics(topics):
                        return topics[:20]
                except json.JSONDecodeError:
                    # 尝试修复常见 JSON 错误 (如尾部逗号)
                    try:
                        fixed_json = re.sub(r',\s*([\]}])', r'\1', json_str)
                        topics = json.loads(fixed_json)
                        if self._validate_topics(topics):
                            return topics[:20]
                    except:
                        pass

            logger.warning("无法解析 JSON，尝试使用正则表达式提取内容")
            
            # 4. 最后的手段：使用正则强行提取 title 和 summary
            topics = []
            # 匹配 {"title": "...", "summary": "..."} 模式
            # 注意：这个正则比较宽通过，可能匹配到不该匹配的，但在 fallback 情况下是可以接受的
            items = re.findall(r'\{\s*"title"\s*:\s*"(.*?)"\s*,\s*"summary"\s*:\s*"(.*?)"\s*\}', content, re.DOTALL)
            
            for title, summary in items:
                topics.append({
                    "title": title.strip(),
                    "summary": summary.strip()
                })
            
            if topics:
                logger.info(f"通过正则回退机制提取到 {len(topics)} 个主题")
                return topics[:20]

            logger.error("所有解析方法均失败")
            return []

        except Exception as e:
            logger.error(f"解析主题彻底失败: {e}")
            return []

    def _validate_topics(self, topics: Any) -> bool:
        """验证解析出的主题列表格式"""
        if not isinstance(topics, list):
            return False
        if not topics:
            return False
            
        # 验证前几个元素
        for i, topic in enumerate(topics[:3]):
            if not isinstance(topic, dict):
                return False
            if 'title' not in topic:
                return False
        
        logger.info(f"成功解析出 {len(topics)} 个热点主题")
        return True

    async def fetch_topics_from_url(self, url: str) -> List[Dict[str, str]]:
        """从URL爬取内容并提取主题

        Args:
            url: 要爬取的网页URL

        Returns:
            List[Dict[str, str]]: 提取的主题列表，每个主题包含 title 和 summary
        """
        try:
            logger.info(f"开始从URL提取主题: {url}")

            # 优先使用全局服务器管理器
            if server_manager.is_initialized():
                logger.info("使用全局服务器管理器")
                self.servers = server_manager.get_servers()
                self.llm_client = server_manager.get_llm_client()
                available_tools = await server_manager.get_available_tools()
            else:
                logger.info("全局服务器未初始化,使用本地获取")
                # 获取可用工具
                available_tools = await self.get_available_tools()

            if not available_tools:
                logger.error("没有可用的工具")
                return []

            # 将工具转换为OpenAI格式
            openai_tools = [tool.to_openai_tool() for tool in available_tools]

            # 构建提示词
            system_prompt = """你是一个专业的内容分析师，擅长从网页内容中提取有价值的主题。
            请使用网络爬取工具访问指定的URL，读取页面内容，然后分析提取出其中最有价值的主题。
            """

            user_prompt = f"""请访问以下网页并提取其中最有价值的20个主题：

            URL: {url}

            对于每个主题，请提供：
            1. 简洁的标题（15-20字）
            2. 简短的摘要说明（30-50字）

            请确保提取的主题具有独立性，适合作为社交媒体内容创作的选题。

            提取完成后，请按照以下JSON格式整理结果（注意：你的最终回复必须是纯JSON格式，不要包含任何其他文字）：
            ```json
            [
              {{
                "title": "话题标题",
                "summary": "话题摘要"
              }}
            ]
            ```
            """

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 进行多轮工具调用
            max_iterations = 5
            iteration = 0

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"URL内容提取 - 第 {iteration} 轮")

                # 获取工具调用响应
                response = self.llm_client.get_tool_call_response(messages, openai_tools)
                message = response.choices[0].message

                if message.tool_calls:
                    # 添加助手消息
                    assistant_msg = {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in message.tool_calls
                        ]
                    }
                    messages.append(assistant_msg)

                    # 执行所有工具调用
                    for tool_call in message.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            arguments = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                        except json.JSONDecodeError:
                            arguments = {}

                        logger.info(f"执行工具: {tool_name}")

                        # 查找对应的服务器并执行工具
                        tool_result = None
                        for server in self.servers:
                            tools = await server.list_tools()
                            if any(tool.name == tool_name for tool in tools):
                                try:
                                    tool_result = await server.execute_tool(tool_name, arguments)
                                    break
                                except Exception as e:
                                    logger.error(f"执行工具 {tool_name} 出错: {e}")
                                    tool_result = f"Error: {str(e)}"

                        if tool_result is None:
                            tool_result = f"未找到工具 {tool_name}"

                        # 添加工具结果消息
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(tool_result)
                        })

                    # 获取最终响应
                    final_response = self.llm_client.get_final_response(messages, openai_tools)
                    final_message = final_response.choices[0].message

                    if final_message.tool_calls:
                        # 继续下一轮
                        response = final_response
                    else:
                        # 获取最终内容并解析
                        final_content = final_message.content or ""
                        logger.info("URL内容提取完成，开始解析结果")

                        # 尝试从返回内容中提取JSON
                        topics = self._parse_topics_from_response(final_content)
                        return topics
                else:
                    # 没有工具调用，直接返回内容
                    final_content = message.content or ""
                    topics = self._parse_topics_from_response(final_content)
                    return topics

            logger.warning("达到最大迭代次数，未能完成URL内容提取")
            return []

            logger.warning("达到最大迭代次数，未能完成URL内容提取")
            return []

        except Exception as e:
            # 检查是否是Tavily API错误
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "unauthorized" in error_str or "403" in error_str:
                logger.warning(f"检测到Tavily API可能受限: {e}，尝试轮换Key...")
                if await server_manager.rotate_tavily_key():
                    logger.info("Key轮换成功，重试URL内容提取...")
                    # 递归重试一次
                    return await self.fetch_topics_from_url(url)

            logger.error(f"从URL提取主题失败: {e}", exc_info=True)
            return []

    async def execute_step(self, step: Dict[str, Any], available_tools: List[Tool],
                          previous_results: List[Dict[str, Any]], user_topic: str) -> Dict[str, Any]:
        """执行单个步骤

        Args:
            step: 步骤配置
            available_tools: 可用工具列表
            previous_results: 之前步骤的结果
            user_topic: 用户输入的主题

        Returns:
            步骤执行结果
        """
        logger.info(f"执行步骤: {step['id']} - {step['title']}")

        # 将工具转换为OpenAI格式
        openai_tools = [tool.to_openai_tool() for tool in available_tools] if available_tools else None

        system_prompt = f"""你是一个专业的小红书内容创作专家，专门研究「{user_topic}」相关的最新发展。请根据任务背景、之前步骤的执行结果和当前步骤要求选择并调用相应的工具。
        【研究主题】
        核心主题: {user_topic}
        研究目标: 收集、分析并撰写关于「{user_topic}」的专业内容，最终发布到小红书平台
        
        【小红书文案要求】
        🎯 吸引力要素：
        - 使用引人注目的标题，包含热门话题标签和表情符号
        - 开头要有强烈的钩子，激发用户好奇心和共鸣
        - 内容要实用且有价值，让用户有收藏和分享的冲动
        - 语言要轻松活泼，贴近年轻用户的表达习惯
        - 结尾要有互动引导，如提问、征集意见等
        - 适当使用流行梗和网络用语，但保持专业度
        
        【任务背景】
        目标: f'深度研究{user_topic}并生成高质量的社交媒体内容'
        要求: 确保内容专业准确、提供3-4张真实可访问的图片、格式符合小红书发布标准，最好不要有水印，避免侵权的威胁
        
        【当前步骤】
        步骤ID: {step['id']}
        步骤标题: {step['title']}
        """

        # 根据是否有前置结果添加不同的执行指导
        if previous_results:
            system_prompt += "\n【前序步骤执行结果】\n"
            for result in previous_results:
                if result.get('response'):
                    response_preview = result['response'][:1000]  # 限制长度
                    system_prompt += f"▸ {result['step_id']} - {result['step_title']}：\n"
                    system_prompt += f"{response_preview}...\n\n"

            system_prompt += """【执行指南】
                1. 仔细理解前序步骤已获得的信息和资源
                2. 基于已有结果，确定当前步骤需要调用的工具
                3. 充分利用前序步骤的数据，避免重复工作
                4. 如需多个工具协同，可同时调用
                5. 确保当前步骤输出能无缝衔接到下一步骤
                
                ⚠️ 重要提示：
                - 如果前序步骤已提供足够信息，直接整合利用，不要重复检索
                - 如果是内容创作步骤，基于前面的素材直接撰写
                - 如果是发布步骤，直接提取格式化内容进行发布
                """
        else:
            system_prompt += """【执行指南】
            1. 这是一个独立步骤，不依赖其他步骤结果
            2. 分析当前任务需求，选择合适的工具
            3. 为工具调用准备准确的参数
            4. 如需多个工具，可同时调用
            5. 完成所有要求的子任务
            
            ⚠️ 执行要点：
            - 严格按照步骤描述执行
            - 确保工具调用参数准确
            - 收集的信息要完整且相关度高
            """

        user_prompt = step['description']

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            all_tool_call_details = []
            max_iterations = 10
            iteration = 0
            publish_success = False  # 添加发布成功标志
            publish_error = None  # 保存发布失败的错误信息

            # 第一轮：初始工具调用
            response = self.llm_client.get_tool_call_response(messages, openai_tools)

            if not response.choices[0].message.tool_calls:
                logger.info("第一轮没有工具调用，直接返回")
                final_content = response.choices[0].message.content or ""
            else:
                # 进入循环处理工具调用
                while iteration < max_iterations:
                    iteration += 1
                    logger.info(f"处理第 {iteration} 轮")

                    message = response.choices[0].message

                    if message.tool_calls:
                        logger.info(f"第 {iteration} 轮发现 {len(message.tool_calls)} 个工具调用")

                        # 添加助手消息
                        assistant_msg = {
                            "role": "assistant",
                            "content": message.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments
                                    }
                                }
                                for tc in message.tool_calls
                            ]
                        }
                        messages.append(assistant_msg)

                        # 执行所有工具调用
                        for tool_call in message.tool_calls:
                            tool_name = tool_call.function.name
                            try:
                                arguments = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                            except json.JSONDecodeError:
                                arguments = {}

                            logger.info(f"执行工具: {tool_name} 参数: {arguments}")

                            # 🔍 特殊处理: 在发布前验证图片URL
                            if tool_name == "publish_content":
                                # 0. 检查标题长度限制 (小红书限制20字)
                                title_text = arguments.get("title", "")
                                if len(title_text) > 20:
                                    logger.warning(f"⚠️ 标题长度 ({len(title_text)}) 超过限制 (20)，正在尝试缩短...")
                                    try:
                                        shorten_title_prompt = f"""
                                        请将以下小红书标题缩短到 18 字以内。
                                        要求：
                                        1. 保持原意和吸引力
                                        2. 只输出缩短后的标题，不要包含任何解释
                                        3. 必须包含关键词
                                        
                                        原标题：
                                        {title_text}
                                        """
                                        shorten_messages = [{"role": "user", "content": shorten_title_prompt}]
                                        shorten_response = self.llm_client.chat(shorten_messages)
                                        shortened_title = shorten_response.choices[0].message.content.strip()
                                        
                                        if len(shortened_title) <= 20:
                                            arguments["title"] = shortened_title
                                            logger.info(f"✅ 标题已缩短至 {len(shortened_title)} 字: {shortened_title}")
                                        else:
                                            logger.warning(f"⚠️ 缩短后标题仍然过长 ({len(shortened_title)})，强制截断")
                                            arguments["title"] = shortened_title[:18] + "..."
                                    except Exception as e:
                                        logger.error(f"标题缩短失败: {e}")
                                        arguments["title"] = title_text[:18] + "..."

                                # 1. 检查内容长度限制 (用户设置为2000字)
                                content_text = arguments.get("content", "")
                                if len(content_text) > 2000:
                                    logger.warning(f"⚠️ 内容长度 ({len(content_text)}) 超过限制 (2000)，自动截断...")
                                    # 在1995字前找到最后一个换行符，保持段落完整
                                    truncated = content_text[:1995]
                                    last_newline = truncated.rfind('\n')
                                    if last_newline > 1800:
                                        truncated = truncated[:last_newline]
                                    arguments["content"] = truncated
                                    logger.info(f"✅ 内容已截断至 {len(truncated)} 字")

                                # 2. 验证图片URL
                                original_images = arguments.get("images") or []
                                if not isinstance(original_images, list):
                                    original_images = [original_images]
                                logger.info(f"🔍 开始验证 {len(original_images)} 个图片URL...")

                                valid_images = await self.validate_image_urls(original_images)

                                if len(valid_images) < len(original_images):
                                    logger.warning(f"⚠️ 部分图片URL无效: {len(original_images) - len(valid_images)} 个被过滤")

                                # 最少1张即可发布，最多取5张
                                TARGET_MAX_IMAGES = 5

                                if len(valid_images) == 0:
                                    tool_result = "错误: 所有图片URL均无效，无法发布。请使用tavily_search重新搜索图片（include_images=true），使用搜索结果中的真实图片URL，不要自己编造。避免 gtimg.com、sinaimg.cn、freepik.com 等有防盗链的网站。"
                                    logger.error("❌ 图片验证失败: 没有有效的图片URL")
                                else:
                                    # 有效图片足够，取前 TARGET_MAX_IMAGES 张
                                    selected_images = valid_images[:TARGET_MAX_IMAGES]
                                    arguments["images"] = selected_images
                                    logger.info(f"✅ 图片选择完成，使用 {len(selected_images)} 张有效图片（共验证通过 {len(valid_images)} 张）")

                                    # 执行发布工具
                                    tool_result = None
                                    for server in self.servers:
                                        tools = await server.list_tools()
                                        if any(tool.name == tool_name for tool in tools):
                                            try:
                                                tool_result = await server.execute_tool(tool_name, arguments)
                                                break
                                            except Exception as e:
                                                logger.error(f"执行工具 {tool_name} 出错: {e}")
                                                tool_result = f"Error: {str(e)}"

                                    if tool_result is None:
                                        tool_result = f"未找到工具 {tool_name}"
                            else:
                                # 其他工具正常执行
                                tool_result = None
                                for server in self.servers:
                                    tools = await server.list_tools()
                                    if any(tool.name == tool_name for tool in tools):
                                        try:
                                            tool_result = await server.execute_tool(tool_name, arguments)
                                            break
                                        except Exception as e:
                                            logger.error(f"执行工具 {tool_name} 出错: {e}")
                                            tool_result = f"Error: {str(e)}"

                                if tool_result is None:
                                    tool_result = f"未找到工具 {tool_name}"

                            # 检查是否是 Tavily 搜索工具的错误返回
                            if tool_result is not None and "tavily" in tool_name.lower():
                                result_str = str(tool_result).lower()
                                if ("this request exceeds your plan\'s set usage limit. please upgrade your plan or contact support@tavily.com" in result_str and "432" in result_str):
                                    logger.warning(f"检测到Tavily API受限: {tool_result}")
                                    # 抛出特殊异常，让外层处理轮换和重试
                                    raise TavilyQuotaExceeded("Tavily API配额已用尽，需要轮换Key")

                            # 检测是否是发布工具，并且是否成功
                            if tool_name == "publish_content":
                                # 检查结果是否表明成功
                                result_str = str(tool_result).lower()
                                if "success" in result_str or "成功" in result_str or "published" in result_str:
                                    publish_success = True
                                    logger.info("✅ 检测到发布成功，将在本轮结束后停止迭代")
                                else:
                                    # 保存详细的错误信息
                                    publish_error = str(tool_result)
                                    logger.error(f"❌ 发布失败: {publish_error}")

                            # 记录工具调用详情
                            tool_detail = {
                                "iteration": iteration,
                                "name": tool_name,
                                "arguments": arguments,
                                "result": str(tool_result)
                            }
                            all_tool_call_details.append(tool_detail)

                            # 限制工具返回结果的长度，防止上下文溢出 (413 Error)
                            tool_result_str = str(tool_result)
                            if len(tool_result_str) > 20000:
                                # 使用 LLM 进行智能总结
                                try:
                                    logger.info(f"工具 {tool_name} 返回结果过长 ({len(tool_result_str)}字符)，正在调用 LLM 进行总结...")
                                    # 异步调用总结方法
                                    summary = await self.summarize_content(tool_result_str)
                                    tool_result_str = summary
                                except Exception as e:
                                    logger.error(f"智能总结失败，回退到强制截断: {e}")
                                    tool_result_str = tool_result_str[:20000] + "...(content truncated)"

                            # 添加工具结果消息
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": tool_result_str
                            })

                    # 如果发布已成功，直接结束迭代
                    if publish_success:
                        logger.info("🎉 发布已成功，停止迭代")
                        # 使用一个简单的最终响应
                        final_content = "内容已成功发布到小红书平台"
                        break

                    # 调用get_final_response决定下一步
                    logger.info("调用get_final_response决定下一步动作...")
                    final_response = self.llm_client.get_final_response(messages, openai_tools)
                    final_message = final_response.choices[0].message

                    if final_message.tool_calls:
                        # 继续下一轮
                        logger.info(f"get_final_response返回了 {len(final_message.tool_calls)} 个工具调用，继续...")
                        response = final_response
                    else:
                        # 任务完成
                        logger.info(f"get_final_response返回最终答案。任务在 {iteration} 轮内完成。")
                        final_content = final_message.content or ""
                        break
                else:
                    # 达到最大迭代次数
                    logger.warning(f"达到最大迭代次数 ({max_iterations})。停止工具调用。")
                    final_content = final_message.content or "任务执行超出最大迭代次数限制"

            # 构建结果
            step_result = {
                "step_id": step['id'],
                "step_title": step['title'],
                "tool_calls": all_tool_call_details,
                "total_iterations": iteration,
                "response": final_content,
                "success": True,
                "publish_success": publish_success,  # 添加发布成功标志
                "publish_error": publish_error  # 添加发布错误信息
            }

            return step_result

        except TavilyQuotaExceeded:
            # 不捕获此异常，让它继续向上传播到 generate_and_publish 进行轮换重试
            raise
        except Exception as e:
            logger.error(f"执行步骤 {step['id']} 出错: {e}")
            return {
                "step_id": step['id'],
                "step_title": step['title'],
                "error": str(e),
                "success": False
            }

    async def generate_and_publish(self, topic: str, content_type: str = "general") -> Dict[str, Any]:
        """生成内容并发布到小红书

        Args:
            topic: 用户输入的主题
            content_type: 内容类型 ("general" 或 "paper_analysis")

        Returns:
            生成和发布结果
        """
        try:
            logger.info(f"开始生成关于「{topic}」的内容，类型：{content_type}...")

            # 优先使用全局服务器管理器
            if server_manager.is_initialized():
                logger.info("使用全局服务器管理器")
                self.servers = server_manager.get_servers()
                self.llm_client = server_manager.get_llm_client()
                available_tools = await server_manager.get_available_tools()
            else:
                logger.info("全局服务器未初始化,使用本地初始化")
                # 获取可用工具
                available_tools = await self.get_available_tools()

                if available_tools is None or len(available_tools) == 0:
                    # 初始化服务器
                    await self.initialize_servers()
                    available_tools = await self.get_available_tools()

            logger.info(f"总共可用工具数: {len(available_tools)}")

            # 获取研究计划
            research_plan = self.get_research_plan(topic, content_type)

            # 执行每个步骤
            results = []
            for step in research_plan:
                max_retries = 2  # 最多重试2次（轮换2次Key）
                retry_count = 0

                while retry_count <= max_retries:
                    try:
                        step_result = await self.execute_step(step, available_tools, results, topic)
                        results.append(step_result)

                        if not step_result.get('success'):
                            logger.error(f"步骤 {step['id']} 执行失败")
                            return {
                                'success': False,
                                'error': f"步骤 {step['id']} 执行失败: {step_result.get('error', '未知错误')}"
                            }

                        logger.info(f"步骤 {step['id']} 执行成功")
                        break  # 成功则跳出重试循环

                    except TavilyQuotaExceeded as e:
                        retry_count += 1
                        if retry_count <= max_retries:
                            logger.warning(f"步骤 {step['id']} Tavily配额用尽（第{retry_count}次），开始轮换Key并重试...")

                            # 轮换Key + 重启服务器
                            if await server_manager.rotate_tavily_key():
                                logger.info(f"✅ Key轮换成功，重新执行步骤 {step['id']}...")
                                # 更新本地引用
                                self.servers = server_manager.get_servers()
                                self.llm_client = server_manager.get_llm_client()
                                available_tools = await server_manager.get_available_tools()
                            else:
                                logger.error("❌ Key轮换失败，没有更多可用的Key")
                                return {
                                    'success': False,
                                    'error': f"步骤 {step['id']} 执行失败: Tavily API配额已用尽且无法轮换Key"
                                }
                        else:
                            logger.error(f"❌ 步骤 {step['id']} 已重试{max_retries}次，全部失败")
                            return {
                                'success': False,
                                'error': f"步骤 {step['id']} 执行失败: 已轮换所有Tavily Key但仍然失败"
                            }

            # 检查发布步骤（step3 或 step3_format）是否成功
            step3_result = next((r for r in results if r['step_id'] in ['step3', 'step3_format']), None)
            publish_success = step3_result.get('publish_success', False) if step3_result else False

            # 如果发布失败，返回失败结果，包含详细的错误信息
            if not publish_success:
                logger.error("内容发布失败")
                publish_error = step3_result.get('publish_error', '') if step3_result else ''

                # 构建详细的错误消息
                error_message = '内容生成完成，但发布到小红书失败。'
                if publish_error:
                    # 清理错误信息，使其更易读
                    error_detail = publish_error.strip()
                    # 如果错误信息太长，截取前500个字符
                    if len(error_detail) > 500:
                        error_detail = error_detail[:500] + '...'
                    error_message += f'\n\n错误详情：{error_detail}'
                else:
                    error_message += '\n请检查小红书MCP服务连接或稍后重试。'

                return {
                    'success': False,
                    'error': error_message
                }

            # 从 step3 的工具调用中提取实际发布的内容
            # step3_result 已经在上面获取了
            content_data = {
                'title': f'关于{topic}的精彩内容',
                'content': '',
                'tags': [topic],
                'images': []
            }

            # 尝试从 tool_calls 中提取 publish_content 的参数
            if step3_result and step3_result.get('tool_calls'):
                try:
                    # 查找 publish_content 工具调用
                    publish_call = next(
                        (tc for tc in step3_result['tool_calls'] if tc['name'] == 'publish_content'),
                        None
                    )

                    if publish_call and publish_call.get('arguments'):
                        # 从工具调用参数中提取实际发布的内容
                        args = publish_call['arguments']
                        content_data = {
                            'title': args.get('title', f'关于{topic}的精彩内容'),
                            'content': args.get('content', ''),
                            'tags': args.get('tags', [topic]),
                            'images': args.get('images', [])
                        }
                        logger.info(f"成功从 publish_content 参数中提取内容数据")
                    else:
                        logger.warning("未找到 publish_content 工具调用或参数为空")
                except Exception as e:
                    logger.error(f"从工具调用参数中提取内容失败: {e}")

            return {
                'success': True,
                'title': content_data.get('title', ''),
                'content': content_data.get('content', ''),
                'tags': content_data.get('tags', []),
                'images': content_data.get('images', []),
                'publish_status': '已成功发布',
                'full_results': results
            }

        except Exception as e:
            logger.error(f"生成和发布失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

        finally:
            # 只有在使用本地服务器时才清理资源
            if not server_manager.is_initialized():
                await self.cleanup_servers()

    async def cleanup_servers(self):
        """清理服务器连接"""
        for server in reversed(self.servers):
            try:
                await server.cleanup()
            except Exception as e:
                logger.warning(f"清理警告: {e}")

    def get_paper_analysis_plan(self, user_topic: str) -> List[Dict[str, Any]]:
        """生成论文分析专用工作流"""
        return [
            {
                "id": "step1_paper",
                "title": f"「{user_topic}」领域论文检索与分析",
                "description": (
                    f"1. 使用搜索工具搜索「{user_topic}」相关的最新学术论文\n"
                    f"2. 搜索策略：\n"
                    f"   - 使用关键词：\"site:arxiv.org {user_topic}\" 搜索arXiv论文\n"
                    f"   - 搜索 \"{user_topic} paper research study\" 获取相关研究\n"
                    f"   - 重点关注最近1-2年的高影响力论文\n"
                    f"3. 筛选标准：\n"
                    f"   - 优先选择高引用量、知名会议/期刊的论文\n"
                    f"   - 关注技术创新点和实际应用价值\n"
                    f"   - 收集2-3篇最具代表性的论文\n"
                    f"4. 信息收集：\n"
                    f"   - 论文标题、作者、发表时间\n"
                    f"   - 核心摘要和研究问题\n"
                    f"   - 主要创新点和贡献\n"
                    f"   - 实验结果和关键图表\n"
                    f"   - 论文全文链接\n"
                    f"   - **相关图片**: 尽可能多地收集论文相关的图片链接（10张以上），确保后续有足够的图片可用"
                ),
                "depends on": []
            },
            {
                "id": "step2_analysis",
                "title": "论文深度解读与内容生成",
                "description": (
                    "1. 按照以下标准格式生成论文分析内容：\n"
                    "   📚 **标题**: 论文核心价值的通俗化表达\n"
                    "   📝 **核心摘要**: 2-3句话概括论文要解决的问题和主要发现\n"
                    "   💡 **主要贡献**: 3个创新点（技术突破、方法创新、应用价值）\n"
                    "   🚀 **未来发展**: 技术改进方向、潜在应用场景、商业化前景\n"
                    "   🔮 **展望**: 个人观点、行业影响预期、后续研究方向\n"
                    "   📖 **论文链接**: 原始论文的完整链接\n"
                    "2. 语言要求：\n"
                    "   - **禁止AI味**: 严禁使用'主要包括以下几点'、'综上所述'等僵硬的连接词\n"
                    "   - **风格自然**: 像一个资深研究员在和同事分享，语言客观但有温度，允许有个人见解\n"
                    "   - 通俗易懂，避免专业术语堆砌\n"
                    "   - 适当使用emoji表情增加可读性\n"
                    "3. 内容质量：\n"
                    "   - 长度控制在800-1200字\n"
                    "   - 突出论文的创新价值和应用意义\n"
                    "   - 提供具体的技术细节和数据支撑"
                ),
                "depends on": ["step1_paper"]
            },
            {
                "id": "step3_format",
                "title": "小红书格式适配与发布",
                "description": (
                    "1. 将论文分析内容适配小红书格式：\n"
                    "   - 标题突出论文的核心价值，保留「论文分享」标识\n"
                    "   - 正文移除#标签，改为自然语言表达\n"
                    "   - 提取5个精准标签（学术性+科普性+热点性）\n"
                    "   - **图片要求**: 必须提供5-7张图片。包括：核心架构图、性能对比图、效果展示图、DEMO截图等\n"
                    "   - 为了确保有足够的图片，请在搜索阶段尽可能多地获取图片链接（10张以上）\n"
                    "2. 标签示例：#AI研究 #学术论文 #科技前沿 #知识分享 #人工智能\n"
                    "3. 内容要求：\n"
                    "   - 保持学术严谨性同时兼顾可读性\n"
                    "   - 突出研究的创新点和实用价值\n"
                    "   - 避免过于技术化的表述\n"
                    "4. 直接使用publish_content工具发布到小红书\n"
                    "5. 确保图片链接有效且与论文内容相关"
                ),
                "depends on": ["step1_paper", "step2_analysis"]
            }
        ]
