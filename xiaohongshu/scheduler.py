import os
import time
import logging
import asyncio
import argparse

import schedule
from dotenv import load_dotenv

from core.content_generator import ContentGenerator
from core.server_manager import server_manager
from config.config_manager import ConfigManager


LOG_PATH = os.path.join("logs", "scheduler.log")
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")

load_dotenv()


def get_config_from_env() -> dict:
    """从环境变量获取配置，优先级高于配置文件"""
    config_manager = ConfigManager()
    config = config_manager.load_config(for_display=False)

    env_mapping = {
        "LLM_API_KEY": "llm_api_key",
        "OPENAI_BASE_URL": "openai_base_url",
        "DEFAULT_MODEL": "default_model",
        "TAVILY_API_KEY": "tavily_api_key",
        "JINA_API_KEY": "jina_api_key",
        "XHS_MCP_URL": "xhs_mcp_url",
    }

    for env_key, config_key in env_mapping.items():
        env_val = os.getenv(env_key)
        if env_val:
            config[config_key] = env_val

    if not config.get("xhs_mcp_url"):
        config["xhs_mcp_url"] = "http://localhost:18060/mcp"

    return config


def get_scheduler_settings() -> dict:
    """读取调度参数 (CLI > ENV > Default)"""
    parser = argparse.ArgumentParser(description="XiaoHongShu Auto Publisher")
    parser.add_argument("--mode", choices=["general", "paper_analysis", "zhihu"], 
                        help="Content generation mode", default=None)
    parser.add_argument("--interval", type=int, help="Interval in hours", default=None)
    parser.add_argument("--at", help="Daily run time (e.g. 10:30)", default=None)
    parser.add_argument("--run-now", action="store_true", help="Run immediately on start")
    
    args, unknown = parser.parse_known_args()

    # 1. CLI Arguments
    if args.mode:
        content_type = args.mode
    else:
        content_type = os.getenv("AUTO_PUBLISH_CONTENT_TYPE", "general").strip() or "general"

    if args.interval:
        interval_hours = args.interval
    else:
        interval_hours = int(os.getenv("AUTO_PUBLISH_INTERVAL_HOURS", "1"))

    if args.at:
        daily_at = args.at
    else:
        daily_at = os.getenv("AUTO_PUBLISH_DAILY_AT", "").strip()

    if args.run_now:
        run_on_start = True
    else:
        run_on_start = os.getenv("AUTO_PUBLISH_RUN_ON_START", "true").lower() in {"1", "true", "yes", "y"}

    domain = os.getenv("AUTO_PUBLISH_DOMAIN", "AI").strip() or "AI"

    # Validation
    valid_modes = {"general", "paper_analysis", "zhihu"}
    if content_type not in valid_modes:
        logger.warning(f"Mode {content_type} invalid, falling back to general")
        content_type = "general"

    if interval_hours < 1:
        logger.warning("Interval must be >= 1, falling back to 1")
        interval_hours = 1

    return {
        "interval_hours": interval_hours,
        "daily_at": daily_at,
        "run_on_start": run_on_start,
        "domain": domain,
        "content_type": content_type,
    }


async def run_generation_task() -> None:
    """执行一次自动生成并发布任务"""
    start_ts = time.time()
    logger.info("开始执行自动发布任务...")

    settings = get_scheduler_settings()
    domain = settings["domain"]
    content_type = settings["content_type"]

    try:
        config = get_config_from_env()

        if not config.get("llm_api_key"):
            logger.error("缺少 llm_api_key，跳过本轮任务")
            return

        if not server_manager.is_initialized():
            await server_manager.initialize(config)

        generator = ContentGenerator(config)
        topics = await generator.fetch_trending_topics(domain=domain)

        if not topics:
            logger.warning("未获取到热点话题，跳过本轮任务")
            return

        selected_topic = topics[0]
        topic_title = selected_topic.get("title", "未知话题")
        logger.info(f"选中话题: {topic_title}")

        result = await generator.generate_and_publish(topic_title, content_type=content_type)
        if result.get("success"):
            logger.info(f"任务完成并发布成功: {result.get('title', topic_title)}")
        else:
            logger.error(f"任务执行完成但发布失败: {result.get('error', '未知错误')}")

    except Exception as e:
        logger.error(f"任务执行失败: {e}", exc_info=True)
    finally:
        logger.info("清理 MCP 资源...")
        await server_manager.cleanup()
        elapsed = time.time() - start_ts
        logger.info(f"资源清理完成，本轮耗时: {elapsed:.1f}s")


def job() -> None:
    """同步包装异步任务"""
    try:
        asyncio.run(run_generation_task())
    except Exception as e:
        logger.error(f"任务执行异常: {e}", exc_info=True)
    except BaseException as e:
        logger.error(f"任务被中断: {e}")


def setup_schedule() -> None:
    """初始化调度任务"""
    settings = get_scheduler_settings()
    interval_hours = settings["interval_hours"]
    daily_at = settings["daily_at"]

    if daily_at:
        schedule.every().day.at(daily_at).do(job)
        logger.info(f"已配置每日定时发布: {daily_at}")
    else:
        schedule.every(interval_hours).hours.do(job)
        logger.info(f"已配置间隔发布: 每 {interval_hours} 小时")


def main() -> None:
    logger.info("启动自动发布调度器...")
    setup_schedule()

    settings = get_scheduler_settings()
    if settings["run_on_start"]:
        logger.info("启动即执行一次任务")
        job()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
