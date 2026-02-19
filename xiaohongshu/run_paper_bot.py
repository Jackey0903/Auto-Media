
import asyncio
import argparse
import logging
import os
import sys
from dotenv import load_dotenv

# 添加项目根目录到 sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.paper_agent import PaperAgent
from config.config_manager import ConfigManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/paper_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_paper_bot")

async def main():
    parser = argparse.ArgumentParser(description="AI Top Conference Paper Bot")
    parser.add_argument("--topic", type=str, default="CVPR", help="Target topic or conference (e.g. CVPR, ICCV, NeurIPS)")
    parser.add_argument("--max-pages", type=int, default=18, help="Max pages to convert to images (XHS limit is 18)")
    parser.add_argument("--interval", type=int, default=0, help="Run interval in hours (0 for one-off)")
    
    parser.add_argument("--login", action="store_true", help="Login to Xiaohongshu via QR Code")
    
    args = parser.parse_args()
    
    # 加载配置
    load_dotenv()
    config_manager = ConfigManager()
    config = config_manager.load_config(for_display=False)
    
    # 补充环境变量
    if os.getenv("LLM_API_KEY"):
        config["llm_api_key"] = os.getenv("LLM_API_KEY")
    if os.getenv("OPENAI_BASE_URL"):
        config["openai_base_url"] = os.getenv("OPENAI_BASE_URL")
    if os.getenv("DEFAULT_MODEL"):
        config["default_model"] = os.getenv("DEFAULT_MODEL")
    if os.getenv("XHS_MCP_URL"):
        config["xhs_mcp_url"] = os.getenv("XHS_MCP_URL")
    else:
        config["xhs_mcp_url"] = "http://localhost:18060/mcp"

    agent = PaperAgent(config)
    
    # 登录模式
    if args.login:
        logger.info("🔐 Starting Login Mode...")
        from core.server_manager import server_manager
        
        # 初始化连接
        await server_manager.initialize(config)
        xhs_server = server_manager.get_server_by_name("xhs")
        if not xhs_server:
            logger.error("❌ Failed to connect to XHS MCP server")
            return

        # 获取二维码
        try:
            logger.info("Requesting QR Code...")
            result = await xhs_server.session.call_tool("get_login_qrcode", {})
            # result.content typical structure: [TextContent(text='{"qr_data": "base64...", "timeout": 180}')]
            # Verify structure first
            logger.info(f"QR Code Result: {result}")
            logger.info("✅ QR Code requested. Please check the server logs or container output if not displayed here.")
            # Since we can't easily display the image in CLI, we'll ask user to trust the process if it's running
            # Actually, to be useful, we should save the QR code to a file
            
        except Exception as e:
            logger.error(f"❌ Login failed: {e}")
        return

    logger.info(f"🤖 Paper Bot Started. Target: {args.topic}")
    
    if args.interval > 0:
        logger.info(f"⏱️ Running in loop mode. Interval: {args.interval} hours")
        while True:
            try:
                await agent.run(topic=args.topic, max_pages=args.max_pages)
            except Exception as e:
                logger.error(f"Task failed: {e}")
            
            logger.info(f"Sleeping for {args.interval} hours...")
            await asyncio.sleep(args.interval * 3600)
    else:
        logger.info("🚀 Running single task...")
        await agent.run(topic=args.topic, max_pages=args.max_pages)
        logger.info("✅ Task finished.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Exting...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
