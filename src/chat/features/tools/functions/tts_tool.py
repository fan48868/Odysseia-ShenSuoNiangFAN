# -*- coding: utf-8 -*-
"""
旧版 TTS 工具 — 基于 EdgeTTS 的语音合成。

使用 Microsoft Edge TTS 免费接口将文本转为语音，
并将生成的音频文件作为 Discord 附件发送到当前频道。
"""

import asyncio
import logging
import os
import tempfile
from typing import Any, Dict, Optional

import discord

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

# ---------- 默认配置（可通过 .env 覆盖） ----------
_DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
_DEFAULT_RATE = "+0%"
_DEFAULT_VOLUME = "+0%"


def _get_voice() -> str:
    return os.getenv("EDGE_TTS_VOICE", _DEFAULT_VOICE).strip() or _DEFAULT_VOICE


def _get_rate() -> str:
    return os.getenv("EDGE_TTS_RATE", _DEFAULT_RATE).strip() or _DEFAULT_RATE


def _get_volume() -> str:
    return os.getenv("EDGE_TTS_VOLUME", _DEFAULT_VOLUME).strip() or _DEFAULT_VOLUME


async def _generate_tts_audio(text: str, output_path: str) -> bool:
    """
    调用 edge-tts 生成 mp3 音频文件。

    Args:
        text: 要朗读的文本。
        output_path: 输出 mp3 文件路径。

    Returns:
        True 表示成功，False 表示失败。
    """
    try:
        import edge_tts
    except ImportError:
        log.error("edge-tts 库未安装，请执行 pip install edge-tts")
        return False

    voice = _get_voice()
    rate = _get_rate()
    volume = _get_volume()

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        await communicate.save(output_path)
        return True
    except Exception as exc:
        log.error("EdgeTTS 生成音频失败: %s", exc, exc_info=True)
        return False


@tool_metadata(
    name="语音合成 (EdgeTTS)",
    description="将文本转为语音并发送音频文件（旧版 EdgeTTS 方案）。",
    emoji="🔊",
    category="工具",
)
async def tts_tool(
    text: str,
    channel: Optional[discord.abc.Messageable] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    [工具说明]
    使用 EdgeTTS 将文本转为语音，并将音频文件发送到当前频道。

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
    log.info("--- [工具执行]: tts_tool (EdgeTTS), text='%s...' ---", text[:80])

    if not text or not text.strip():
        return {"ok": False, "error": "文本内容不能为空。"}

    # 限制文本长度，防止超长音频
    text = text.strip()
    if len(text) > 2000:
        text = text[:2000]
        log.warning("TTS 文本超过 2000 字，已截断。")

    tmp_file = None
    try:
        # 创建临时文件
        tmp_file = tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False, prefix="tts_"
        )
        tmp_path = tmp_file.name
        tmp_file.close()

        success = await _generate_tts_audio(text, tmp_path)
        if not success:
            return {"ok": False, "error": "语音合成失败，请稍后再试。"}

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
                log.info("TTS 音频已发送到频道。")
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
            "message": "语音已生成并发送。",
            "voice": _get_voice(),
            "text_length": len(text),
        }

    except Exception as exc:
        log.error("tts_tool 执行异常: %s", exc, exc_info=True)
        return {"ok": False, "error": f"语音合成异常: {exc}"}
    finally:
        # 清理临时文件
        if tmp_file and os.path.exists(tmp_file.name):
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass