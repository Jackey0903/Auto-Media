import os
import logging
import datetime
import arxiv
import httpx
import shutil
from typing import List, Dict, Optional, Any
from pdf2image import convert_from_path, convert_from_bytes
import fitz  # PyMuPDF

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PaperUtils:
    def __init__(self, download_dir: str = "cache/papers", image_dir: str = "cache/paper_images", tavily_api_key: str = None):
        """初始化论文工具类"""
        self.download_dir = download_dir
        self.image_dir = image_dir
        self.tavily_api_key = tavily_api_key
        
        # 确保目录存在
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.image_dir, exist_ok=True)
        
        # Arxiv Client - 增加延迟以避免 429
        self.client = arxiv.Client(
            page_size=5,  # 减少每页数量
            delay_seconds=10.0, # 增加延迟到 10s
            num_retries=5 # 增加重试次数
        )

    def search_latest_papers(self, query: str = "cat:cs.AI", max_results: int = 5) -> List[Dict[str, Any]]:
        """搜索最新的ArXiv论文 (带重试和Fallback)"""
        logger.info(f"正在搜索ArXiv论文: {query}")
        
        # 构造搜索查询
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending
        )

        papers = []
        try:
            results = self.client.results(search)
            for r in results:
                paper_info = {
                    "title": r.title,
                    "summary": r.summary.replace("\n", " "),
                    "published": r.published.strftime("%Y-%m-%d"),
                    "authors": [a.name for a in r.authors],
                    "pdf_url": r.pdf_url,
                    "arxiv_url": r.entry_id,
                    "categories": r.categories,
                    "comment": r.comment or "" # 获取评论信息（通常包含会议名称）
                }
                papers.append(paper_info)
                logger.info(f"找到论文: {r.title} ({r.published})")
        
        except Exception as e:
            logger.error(f"搜索ArXiv失败: {e}")
            if "429" in str(e):
                logger.warning("ArXiv API 速率限制 (429)，尝试使用备用查询或降级...")
                # 这里可以实现更复杂的 Fallback，比如切换到 Tavily 或者休眠更久
                # 暂时返回空，由上层处理
            pass
        
        if not papers and "cat:" in query:
             # 如果特定分类搜索失败，尝试通用搜索
             logger.info("特定分类搜索无结果，尝试通用搜索...")
             try:
                 fallback_query = query.split(":")[1] if ":" in query else "AI"
                 return self.search_latest_papers(query=fallback_query, max_results=max_results)
             except:
                 pass

        # 优先级排序：优先显示顶会论文 (CVPR, ICCV, NeurIPS, ECCV, AAAI, ICML)
        def get_priority(paper):
            text = (paper['title'] + " " + paper.get('comment', '')).upper()
            if any(conf in text for conf in ["CVPR", "ICCV", "NEURIPS", "ECCV", "AAAI", "ICML"]):
                return 2
            if "CS.CV" in paper['categories'] or "CS.AI" in paper['categories']:
                return 1
            return 0

        # 获取详细信息以检查 comment 字段（通常包含会议信息）
        # 注意：arxiv.Result 对象直接有 comment 属性，不需要额外 API 调用
        for p in papers:
            # 重新获取原始 result 对象比较麻烦，这里简单处理
            # 在构建 paper_info 时加入 comment
            pass

        papers.sort(key=get_priority, reverse=True)
        
        return papers

    def download_and_process_paper(self, pdf_url: str, paper_id: str = None) -> List[str]:
        """下载PDF并转换为图片"""
        if not paper_id:
            paper_id = pdf_url.split('/')[-1]
            if '.pdf' in paper_id:
                paper_id = paper_id.replace('.pdf', '')
        
        pdf_path = os.path.join(self.download_dir, f"{paper_id}.pdf")
        image_output_dir = os.path.join(self.image_dir, paper_id)
        os.makedirs(image_output_dir, exist_ok=True)
        
        image_paths = []

        # 1. 下载PDF
        if not os.path.exists(pdf_path):
            logger.info(f"正在下载PDF: {pdf_url}")
            try:
                with httpx.Client() as client:
                    resp = client.get(pdf_url, timeout=30.0, follow_redirects=True)
                    if resp.status_code == 200:
                        with open(pdf_path, 'wb') as f:
                            f.write(resp.content)
                    else:
                        logger.error(f"下载失败: {resp.status_code}")
                        return []
            except Exception as e:
                logger.error(f"下载异常: {e}")
                return []
        else:
            logger.info(f"PDF已存在: {pdf_path}")

        # 2. PDF转图片 (首页 + 关键图表)
        try:
            logger.info(f"正在转换PDF为图片: {pdf_path}")
            
            # 使用pdf2image转换前2页（通常包含标题、摘要、架构图）
            # 注意: 需要安装 poppler-utils
            images = convert_from_path(pdf_path, first_page=1, last_page=2, dpi=200)
            
            for i, image in enumerate(images):
                img_filename = f"page_{i+1}.jpg"
                img_path = os.path.join(image_output_dir, img_filename)
                image.save(img_path, "JPEG")
                image_paths.append(img_path)
                logger.info(f"保存页面图片: {img_path}")
            
            # 尝试提取PDF中的图片 (使用PyMuPDF)
            # 这是一个更高级的功能，可以提取嵌入的图片
            doc = fitz.open(pdf_path)
            # 只处理前5页，避免提取太多无关图标
            for page_num in range(min(5, len(doc))):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    
                    # 过滤太小的图片（如图标、公式部分）
                    if len(image_bytes) < 100 * 1024: # 小于100KB跳过
                        continue
                        
                    img_filename = f"extracted_p{page_num+1}_{img_index}.{image_ext}"
                    img_path = os.path.join(image_output_dir, img_filename)
                    
                    with open(img_path, "wb") as f:
                        f.write(image_bytes)
                    
                    image_paths.append(img_path)
                    logger.info(f"提取内嵌图片: {img_path}")
            
            doc.close()

        except Exception as e:
            logger.error(f"处理PDF失败: {e}")
            pass

        return image_paths

    
    def extract_text_from_pdf(self, pdf_url: str, max_chars: int = 20000) -> str:
        """从PDF中提取全文 (用于LLM深度阅读)"""
        paper_id = pdf_url.split('/')[-1].replace('.pdf', '')
        pdf_path = os.path.join(self.download_dir, f"{paper_id}.pdf")
        
        # 确保已下载
        if not os.path.exists(pdf_path):
             self.download_and_process_paper(pdf_url, paper_id)
        
        if not os.path.exists(pdf_path):
            return ""

        try:
            logger.info(f"正在提取PDF文本: {pdf_path}")
            doc = fitz.open(pdf_path)
            full_text = ""
            
            for page in doc:
                full_text += page.get_text()
                if len(full_text) > max_chars:
                    full_text = full_text[:max_chars] + "\n...[Content Truncated]..."
                    break
            
            doc.close()
            return full_text
            
        except Exception as e:
            logger.error(f"提取PDF文本失败: {e}")
            return ""

    def convert_full_paper_to_images(self, pdf_url: str, max_pages: int = 18) -> List[str]:
        """下载PDF并将每一页转换为图片 (用于全图发布)"""
        # 1. 下载或获取PDF路径
        paper_id = pdf_url.split('/')[-1].replace('.pdf', '')
        pdf_path = os.path.join(self.download_dir, f"{paper_id}.pdf")
        
        if not os.path.exists(pdf_path):
             self.download_and_process_paper(pdf_url, paper_id) # 复用下载逻辑
        
        if not os.path.exists(pdf_path):
            logger.error(f"PDF文件未找到: {pdf_path}")
            return []

        image_output_dir = os.path.join(self.image_dir, paper_id, "full_pages")
        os.makedirs(image_output_dir, exist_ok=True)
        
        logger.info(f"正在全页转换PDF: {pdf_path} (Max {max_pages} pages)")
        image_paths = []
        
        try:
            # 转换所有页面
            images = convert_from_path(pdf_path, dpi=200)
            
            for i, image in enumerate(images):
                if i >= max_pages:
                    break
                    
                img_filename = f"full_page_{i+1}.jpg"
                img_path = os.path.join(image_output_dir, img_filename)
                image.save(img_path, "JPEG")
                image_paths.append(img_path)
                logger.info(f"保存全页图片: {img_path}")
                
            return image_paths
            
        except Exception as e:
            logger.error(f"全页转换失败: {e}")
            return []

# 测试代码
if __name__ == "__main__":
    utils = PaperUtils()
    papers = utils.search_latest_papers(query="Generative AI", max_results=1)
    if papers:
        print(f"Found paper: {papers[0]['title']}")
        images = utils.download_and_process_paper(papers[0]['pdf_url'])
        print(f"Generated images: {images}")
