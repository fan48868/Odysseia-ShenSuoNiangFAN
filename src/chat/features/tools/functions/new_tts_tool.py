# -*- coding: utf-8 -*-
"""
新版 TTS 工具 — 基于 Gradio / Qwen3 的语音合成。

通过 Gradio Client 调用外部 TTS 服务（如 Qwen3），
并将生成的音频文件作为 Discord 附件发送到当前频道。
"""

import logging
import os
import tempfile
from typing import Any, Dict, Optional

import discord

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

# ---------- 默认配置（可通过 .env 覆盖） ----------
_DEFAULT_GRADIO_API_URL = "http://localhost:7860"


def _get_gradio_api_url() -> str:
    return (
        os.getenv("GRADIO_API_URL", _DEFAULT_GRADIO_API_URL).strip()
        or _DEFAULT_GRADIO_API_URL
    )


async def _generate_tts_audio_gradio(text: str, output_path: str) -> bool:
    """
    调用 Gradio TTS 服务生成音频文件。

    Args:
        text: 要朗读的文本。
        output_path: 输出音频文件路径。

    Returns:
        True 表示成功，False 表示失败。
    """
    try:
        from gradio_client import Client
    except ImportError:
        log.error("gradio_client 库未安装，请执行 pip install gradio_client")
        return False

    api_url = _get_gradio_api_url()

    try:
        # 使用 to_thread 在线程池中执行同步的 gradio_client 调用
        import asyncio

        def _call_gradio():
            client = Client(api_url)
            # 调用 TTS 接口，返回音频文件路径
            result = client.predict(
                text,
                api_name="/synthesize",
            )
            return result

        result = await asyncio.to_thread(_call_gradio)

        # gradio_client 返回的可能是文件路径
        if isinstance(result, str) and os.path.exists(result):
            # 复制到目标路径
            import shutil

            shutil.copy2(result, output_path)
            return True
        elif isinstance(result, (bytes, bytearray)):
            with open(output_path, "wb") as f:
                f.write(result)
            return True
        else:
            log.error("Gradio TTS 返回了未知格式的结果: %s", type(result))
            return False

    except Exception as exc:
        log.error("Gradio TTS 调用失败: %s", exc, exc_info=True)
        return False


@tool_metadata(
    name="语音合成 (Qwen3)",
    description="将文本转为语音并发送音频文件（新版 Qwen3 / Gradio 方案）。",
    emoji="🗣️",
    category="工具",
)
async def new_tts_tool(
    text: str,
    channel: Optional[discord.abc.Messageable] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    [工具说明]
    使用 Qwen3 / Gradio TTS 服务将文本转为语音，并将音频文件发送到当前频道。

    [调用规则]
    - 当用户明确要求"语音"、"朗读"、"念出来"、"tts"、"读给我听"时，调用此工具。
    - 只传入需要朗读的纯文本，不要夹带多余说明。
    - 文本过长（超过 2000 字）时会截断。

    Args:
        text (str): 需要转为语音的文本内容。
        channel: 当前消息频道（由框架自动注入）。

    Returns:
        包含执行状态的字典。
    """
    log.info(
        "--- [工具执行]: new_tts_tool (Gradio/Qwen3), text='%s...' ---", text[:80]
    )

    api_url = _get_gradio_api_url()
    if not api_url or api_url == _DEFAULT_GRADIO_API_URL:
        log.warning(
            "GRADIO_API_URL 未配置或为默认值，TTS 服务可能无法正常工作。"
        )

    if not text or not text.strip():
        return {"ok": False, "error": "文本内容不能为空。"}

    # 限制文本长度
    text = text.strip()
    if len(text) > 2000:
        text = text[:2000]
        log.warning("TTS 文本超过 2000 字，已截断。")

    tmp_file = None
    try:
        # 创建临时文件
        tmp_file = tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False, prefix="new_tts_"
        )
        tmp_path = tmp_file.name
        tmp_file.close()

        success = await _generate_tts_audio_gradio(text, tmp_path)
        if not success:
            return {"ok": False, "error": "语音合成失败，请检查 Gradio TTS 服务是否正常运行。"}

        # 发送到 Discord 频道
        if channel is not None:
            try:
                file_size = os.path.getsize(tmp_path)
                if file_size > 8 * 1024 * 1024:  # Discord 限制 8MB
                    return {
                        "ok": False,
                        "error": "生成的音频文件过大（超过 8MB），无法发送。",
                    }

                discord_file = discord.File(tmp_path, filename="tts_audio.mp3")
                await channel.send(file=discord_file)
                log.info("新版 TTS 音频已发送到频道。")
            except discord.HTTPException as exc:
                log.error("发送 TTS 音频失败: %s", exc)
                return {"ok": False, "error": f"发送音频失败: {exc}"}
            except Exception as exc:
                log.error("发送 TTS 音频时发生未知错误: %s", exc, exc_info=True)
                return {"ok": False, "error": f"发送音频时出错: {exc}"}
        else:
            log.warning("未提供 channel，TTS 音频已生成但未发送。")

        return {
            "ok": True,
            "message": "语音已生成并发送（Qwen3）。",
            "api_url": api_url,
            "text_length": len(text),
        }

    except Exception as exc:
        log.error("new_tts_tool 执行异常: %s", exc, exc_info=True)
        return {"ok": False, "error": f"语音合成异常: {exc}"}
    finally:
        # 清理临时文件
        if tmp_file and os.path.exists(tmp_file.name):
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass