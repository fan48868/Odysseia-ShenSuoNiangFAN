import asyncio
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai import types

# --- 配置 ---
API_KEY = ""

# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# !!! 关键修改：使用文档中推荐的 "URL 预设" 模式 !!!
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
PROXY_BASE_URL = ""

MODEL_NAME = "gemini-2.5-flash"  # 使用一个常见的模型以提高成功率
EMBEDDING_MODEL_NAME = "gemini-embedding-001" # 使用最新的嵌入模型

# --- 初始化客户端 ---
try:
    client = genai.Client(
        api_key=API_KEY,
        http_options={
            "base_url": PROXY_BASE_URL
        }
    )
    print("✅ genai.Client 初始化成功！")
    print(f"   - API Key: {API_KEY[:10]}...{API_KEY[-4:]}")
    print(f"   - Base URL: {PROXY_BASE_URL}")
except Exception as e:
    print(f"❌ genai.Client 初始化失败: {e}")
    client = None

# 创建线程池执行器
executor = ThreadPoolExecutor(max_workers=5)

# --- 测试函数 ---

async def test_generate_content():
    """
    测试文本生成功能 (generate_content)。
    """
    if not client:
        print("客户端未初始化，跳过 generate_content 测试。")
        return

    print("\n--- [测试 1/2] 正在测试 generate_content... ---")
    try:
        # 准备测试内容
        test_content = types.Content(
            role="user",
            parts=[types.Part(text="你好，Gemini！请介绍一下你自己。")]
        )
        
        # 准备生成配置
        gen_config = types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=1024
        )

        print("正在发送请求...")
        loop = asyncio.get_event_loop()
        
        print(f"正在使用模型: {MODEL_NAME}")
        
        response = await loop.run_in_executor(
            executor,
            lambda: client.models.generate_content(
                model=MODEL_NAME,
                contents=[test_content],
                config=gen_config
            )
        )

        print("✅ 请求成功！")
        print("--- Gemini 的回复 ---")
        print(response.text)
        print("--------------------")

    except Exception as e:
        print(f"❌ generate_content 测试失败！")
        print(f"   错误类型: {type(e).__name__}")
        print(f"   错误详情: {e}")

async def test_embedding():
    """
    测试文本嵌入功能 (embed_content)。
    """
    if not client:
        print("客户端未初始化，跳过 embed_content 测试。")
        return

    print("\n--- [测试 2/2] 正在测试 embed_content... ---")
    try:
        test_text = "这是一个用于测试嵌入的示例文本。"
        print(f"正在为文本生成嵌入: '{test_text}'")
        
        # 准备嵌入配置
        embed_config = types.EmbedContentConfig(
            task_type="retrieval_document",
            title="测试文档"
        )

        loop = asyncio.get_event_loop()
        
        print(f"正在使用模型: {EMBEDDING_MODEL_NAME}")
        
        response = await loop.run_in_executor(
            executor,
            lambda: client.models.embed_content(
                model=EMBEDDING_MODEL_NAME,
                contents=[types.Part(text=test_text)],
                config=embed_config
            )
        )

        print("✅ 请求成功！")
        # 打印嵌入向量的前几个维度以确认成功
        if response and response.embeddings:
            embedding_preview = response.embeddings[0].values[:5]
            print(f"   - 成功生成嵌入向量，维度: {len(response.embeddings[0].values)}")
            print(f"   - 向量预览: {embedding_preview}...")
        else:
            print("   - 响应中没有嵌入数据")
            
    except Exception as e:
        print(f"❌ embed_content 测试失败！")
        print(f"   错误类型: {type(e).__name__}")
        print(f"   错误详情: {e}")


async def main():
    """
    主函数，按顺序运行所有测试。
    """
    print("="*70)
    print("开始通过 Cloudflare Worker 测试 Gemini API 连接 (v3 - URL 预设模式)")
    print("="*70)
    print(f"使用库: google.genai (与 gemini_service.py 相同)")
    print(f"配置方式: http_options={{'base_url': '{PROXY_BASE_URL}'}}")
    print(f"URL 预设模式: {PROXY_BASE_URL}")
    print("="*70)
    
    await test_generate_content()
    await test_embedding()
    
    print("\n" + "="*70)
    print("所有测试已完成。")
    print("="*70)


if __name__ == "__main__":
    # 使用 asyncio.run() 来执行异步的 main 函数
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n测试被用户中断。")
    finally:
        executor.shutdown()
