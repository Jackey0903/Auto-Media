
import logging
import asyncio
from typing import Dict, List, Optional
from core.paper_utils import PaperUtils
from core.xhs_llm_client import LLMClient
from core.server_manager import server_manager
from config.config_manager import ConfigManager

logger = logging.getLogger("paper_agent")

class PaperAgent:
    def __init__(self, config: Dict):
        self.config = config
        self.paper_utils = PaperUtils()
        self.llm_client = LLMClient(
            config.get('llm_api_key'),
            config.get('openai_base_url'),
            config.get('default_model')
        )

    async def run(self, topic: str = "CVPR", max_pages: int = 18):
        """运行一次完整的论文解读任务"""
        logger.info(f"🚀 PaperAgent 启动: 话题={topic}")

        # 1. 寻找高质量论文
        paper = self.find_target_paper(topic)
        if not paper:
            logger.error("❌ 未找到合适的论文，任务终止")
            return

        logger.info(f"📄 选中论文: {paper['title']}")

        # 2. 处理 PDF (全转图片)
        image_paths = self.paper_utils.convert_full_paper_to_images(paper['pdf_url'], max_pages=max_pages)
        if not image_paths:
            logger.error("❌ PDF 转换失败，任务终止")
            return
        
        logger.info(f"🖼️ 成功转换 {len(image_paths)} 张图片")

        # 3. 提取全文 (用于深度解读)
        full_text = self.paper_utils.extract_text_from_pdf(paper['pdf_url'])
        if not full_text:
            logger.warning("⚠️ 全文提取失败，将仅使用摘要生成")
            full_text = "（全文提取失败，请基于摘要和你的知识库进行解读）"

        # 4. 生成深度解读
        content = await self.generate_interpretation(paper, full_text)
        if not content:
            logger.error("❌ 内容生成失败，任务终止")
            return

        # 5. 发布到小红书
        await self.publish_to_xhs(paper, content, image_paths)

    def find_target_paper(self, topic: str) -> Optional[Dict]:
        """寻找符合要求的顶会论文"""
        # 尝试搜索，优先 CVPR/ICCV 等
        # 如果 topic 是具体的会议名，就搜该会议；否则搜 AI 通用
        query = f"{topic}" if "CVPR" in topic or "ICCV" in topic else f"{topic} AND (CVPR OR ICCV OR NeurIPS OR ICML)"
        
        papers = self.paper_utils.search_latest_papers(query=query, max_results=10)
        
        for p in papers:
            # 简单过滤：必须有 PDF
            if p.get('pdf_url'):
                return p
        
        return None

    async def generate_interpretation(self, paper: Dict, full_text: str) -> str:
        """生成无 AI 味的深度解读"""
        
        # 构造 Prompt (完全复用用户提供的 strictly human prompt)
        prompt = f"""
        你现在是一个在 AI 领域（特别是计算机视觉和多模态方向）深耕多年的资深研究员。你刚刚仔细读完了一篇非常精彩的顶会论文，正准备把你的思考和读后感发到社交平台上与同行交流。
        
        【基本信息】
        标题: {paper['title']}
        链接: {paper['arxiv_url']}
        摘要: {paper['summary']}
        
        【论文原文片段】
        {full_text[:15000]} 

        【核心任务】
        请基于以上信息（你可以结合自己的知识库补充背景），写一篇深度的、纯文字的、口语化的论文解读。字数控制在 800 字左右，确保能在小红书完整发布。
        
        【⚠️ 绝对禁止的格式（防 AI 味底线，必须严格遵守）】
        1. 绝对禁止使用任何 Emoji 表情符号（！一个都不能有！）。
        2. 绝对禁止使用任何形式的列表符号（如 1. 2. 3. 或 - 或 * ）。
        3. 绝对禁止使用生硬的结构化小标题（如“研究背景”、“核心方法”、“实验结果”、“总结”）。
        4. 禁用常见的 AI 机器味词汇：如“综上所述”、“值得一提的是”、“总而言之”、“本文提出”。
        
        【推荐的自然叙事流（请融合成连续的自然段落）】
        请用像是在技术交流群里发长文字那样的自然语气，把以下逻辑串联起来：
        - 抛出痛点：直接从这篇论文解决的最核心、最痛点的问题切入（例如：“一直以来，我们在做特征提取或者端到端模型对齐的时候，都会遇到一个很头疼的问题……”）。
        - 核心破局点：引出这篇论文是怎么巧妙破局的，它的 Key Idea 是什么（例如：“而这篇刚刚被收录的工作，换了一个完全不同的思路，他们发现……”）。
        - 实验证明：用一两句大白话概括它的实验表现，证明其有效性（例如：“看了一眼他们在几个主流 Benchmark 上的数据，确实把 SOTA 刷上去了一截。”）。
        - 个人启发：这篇工作对实际的业务落地、或者未来的网络架构设计有什么启发。
        
        【排版要求】
        - 只有纯文本。只使用逗号、句号、问号和书名号。
        - 采用自然分段，用空行来区隔段落，每段不要太长，保持阅读的呼吸感。
        - 语言要克制、专业、自然，像是一个有深厚学术功底的人在进行严密的逻辑推演。最后附上 arXiv 的原始链接。
        """

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm_client.one_chat(messages)
            return response
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}")
            return None

    async def publish_to_xhs(self, paper: Dict, content: str, image_paths: List[str]):
        """发布到小红书"""
        logger.info("准备发布到小红书...")
        
        # 确保 ServerManager 已初始化
        if not server_manager.is_initialized():
            await server_manager.initialize(self.config)

        # 构造标题 (简单直接)
        title = f"【论文精读】{paper['title'][:20]}... 深度解析"
        
        # 调用 MCP Tool
        try:
            tool_name = "publish_content"
            arguments = {
                "title": title,
                "content": content,
                "images": image_paths
            }
            
            # 这里需要一个能调用 MCP 工具的方法，复用 ServerManager 的 session 吗？
            # ServerManager 目前主要管理连接，没有直接暴露 call_tool
            # 我们需要手动从 session 调用，或者复用 ContentGenerator 的逻辑
            # 为了简单，我们手动调用 xhs server
            
            xhs_server = server_manager.get_server("xhs")
            if not xhs_server:
                logger.error("❌ XHS 服务未连接")
                return

            # 直接调用 (需要处理 async)
            result = await xhs_server.session.call_tool(tool_name, arguments)
            logger.info(f"✅ 发布结果: {result}")

        except Exception as e:
            logger.error(f"❌ 发布失败: {e}")
