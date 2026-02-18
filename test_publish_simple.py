import asyncio
import base64
import os
import time
from datetime import timedelta
from core.xhs_llm_client import Server
from config.config_manager import ConfigManager

async def test_publish():
    print("🚀 开始最小路径发布测试 (仅连接 XHS)...")
    
    # 初始化配置
    config_manager = ConfigManager()
    config = config_manager.load_config()
    
    # 手动构建 XHS 服务器配置
    # 优先使用环境变量
    xhs_url = os.environ.get('XHS_MCP_URL')
    if not xhs_url:
        xhs_url = config.get('xhs_mcp_url', 'http://mcp-server:18060/mcp')
        
    xhs_config = {
        "type": "streamable_http",
        "url": xhs_url
    }
    
    xhs_server = Server("xhs", xhs_config)
    try:
        print("🔌 连接小红书 MCP 服务器...")
        await xhs_server.initialize()
        print("✅ 已连接到小红书 MCP 服务器")
    except Exception as e:
        print(f"❌ 连接小红书 MCP 服务器失败: {e}")
        return
    
    # 1. 检查登录状态
    print("\n🔍 正在检查登录状态...")
    try:
        login_status = await xhs_server.execute_tool("check_login_status", {})
        print(f"登录状态结果: {login_status}")
        
        is_logged_in = False
        if hasattr(login_status, "content") and isinstance(login_status.content, list):
             for item in login_status.content:
                 if hasattr(item, "text") and "已登录" in item.text:
                     is_logged_in = True
                     break
        elif isinstance(login_status, dict) and login_status.get("logged_in"):
            is_logged_in = True
        elif isinstance(login_status, str) and "true" in login_status.lower():
             is_logged_in = True
             
        if not is_logged_in:
            print("\n⚠️ 未登录！正在获取登录二维码...")
            qr_res = await xhs_server.execute_tool("get_login_qrcode", {})
            
            if hasattr(qr_res, "content") and isinstance(qr_res.content, list):
                # Handle CallToolResult
                for item in qr_res.content:
                    if hasattr(item, "type") and item.type == "image":
                        qr_code_base64 = item.data
                        break
                    elif hasattr(item, "type") and item.type == "text" and "base64" in item.text:
                         # Fallback if it's text
                         qr_code_base64 = item.text.split("base64,")[-1]

            elif isinstance(qr_res, dict):
                qr_code_base64 = qr_res.get("qr_code", "") or qr_res.get("qrcode", "")
            elif isinstance(qr_res, str):
                if "base64" in qr_res:
                    qr_code_base64 = qr_res.split("base64,")[-1]
                else:
                    qr_code_base64 = qr_res

            if qr_code_base64:
                # 保存二维码图片到映射目录，方便用户查看
                # /app/config 映射到了宿主机的 xiaohongshu/config
                # /app/pages 映射到了... 等等
                # 保存到 static 吧
                save_path = "/app/static/login_qrcode.png"
                try:
                    img_data = base64.b64decode(qr_code_base64)
                    with open(save_path, "wb") as f:
                        f.write(img_data)
                    print(f"\n✅ 二维码已保存为 {save_path}。")
                    print("请把这个文件复制出来或者直接查看，然后用小红书APP扫描登录。")
                    print("⚠️ 扫码登录成功后，请再次运行此脚本！")
                except Exception as e:
                    print(f"保存二维码失败: {e}")
            else:
                print(f"❌ 获取二维码失败，返回内容: {qr_res}")
            
            # 退出，等待下次运行
            await xhs_server.cleanup()
            return
        else:
            print("✅ 已检测到登录状态")

    except Exception as e:
        print(f"❌ 登录检查/获取二维码失败: {e}")
        await xhs_server.cleanup()
        return

    # 2. 发布测试内容
    print("\n🚀 开始发布测试内容...")
    test_content = {
        "title": "API测试发布-最小路径验证",
        "content": "这是一条通过最小路径脚本自动发布的测试内容，用于验证系统连通性。#测试 #自动化",
        "images": [
            "https://picx.zhimg.com/v2-7d9ed84cd9d4440c80d2324207cd3637_1440w.jpg"
        ],
        "tags": ["测试", "自动化验证"]
    }

    try:
        print(f"正在发布: {test_content['title']}")
        result = await xhs_server.execute_tool("publish_content", test_content)
        print(f"\n✅ 发布结果: {result}")
    except Exception as e:
        print(f"\n❌ 发布失败: {e}")
    
    await xhs_server.cleanup()

if __name__ == "__main__":
    asyncio.run(test_publish())
