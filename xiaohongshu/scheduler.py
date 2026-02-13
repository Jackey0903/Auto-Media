import os
import time
import logging
import asyncio
import schedule
from dotenv import load_dotenv

from core.content_generator import ContentGenerator
from core.server_manager import server_manager
from config.config_manager import ConfigManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scheduler.log", encoding='utf-8')
    ]
)
logger = logging.getLogger("scheduler")

# 加载环境变量
load_dotenv()

def get_config_from_env() -> dict:
    """从环境变量获取配置，优先级高于配置文件"""
    config_manager = ConfigManager()
    config = config_manager.load_config(for_display=False)
    
    # 环境变量覆盖
    env_mapping = {
        "LLM_API_KEY": "llm_api_key",
        "OPENAI_BASE_URL": "openai_base_url",
        "DEFAULT_MODEL": "default_model",
        "TAVILY_API_KEY": "tavily_api_key",
        "JINA_API_KEY": "jina_api_key",
        "XHS_MCP_URL": "xhs_mcp_url"
    }
    
    for env_key, config_key in env_mapping.items():
        if os.getenv(env_key):
            config[config_key] = os.getenv(env_key)
            
    # 确保必需配置存在
    if not config.get("xhs_mcp_url"):
        config["xhs_mcp_url"] = "http://localhost:18060/mcp"
        
    return config

async def run_generation_task():
    """执行生成任务"""
    setup_start_time = time.time()
    logger.info("开始执行定时任务...")
    
    try:
        # 获取配置
        config = get_config_from_env()
        
        # 初始化服务器
        if not server_manager.is_initialized():
            await server_manager.initialize(config)
            
        # 初始化生成器
        # 初始化生成器
        generator = ContentGenerator(config)
        
        # 1. 获取热点话题 (默认为 AI 领域)
        topics = await generator.fetch_trending_topics(domain="AI")
        
        if not topics:
            logger.warning("未获取到热点话题，跳过通过")
            return
            
        # 选择第一个话题
        selected_topic = topics[0]
        topic_title = selected_topic.get("title", "未知话题")
        logger.info(f"选中话题: {topic_title}")
        
        # 2. 生成并发布
        # 默认使用 general 模式，也可以根据需要调整
        result = await generator.generate_and_publish(topic_title, content_type="general")
        
        logger.info(f"任务完成! 结果: {result}")
        
    except Exception as e:
        logger.error(f"任务执行失败: {e}", exc_info=True)
    finally:
        # 任务结束后清理资源
        # 对于使用 asyncio.run 的调度方式，必须清理资源，因为每次运行都会创建新的事件循环
        # 如果不清理，server_manager 中保存的 client 对象会绑定到已关闭的旧循环上，导致 RuntimeError
        logger.info("清理资源...")
        await server_manager.cleanup()
        logger.info("资源清理完成")

def job():
    """同步包装异步任务"""
    asyncio.run(run_generation_task())

def main():
    logger.info("启动自动发布调度器...")
    logger.info("计划每 1 分钟执行一次 (测试模式)")
    
    # 立即运行一次（可选，用于测试）
    if os.getenv("RUN_ON_STARTUP", "false").lower() == "true":
        logger.info("启动时立即执行一次任务")
        job()
    
    # 设置定时任务
    schedule.every(1).minutes.do(job)
    
    # 保持运行
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
