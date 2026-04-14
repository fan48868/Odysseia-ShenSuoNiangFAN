# -*- coding: utf-8 -*-

import asyncio
import discord
import logging
import random
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import discord.abc

# 导入所需的服务
from src.chat.services.gemini_service import gemini_service
from src.chat.services.context_service_test import get_context_service  # 导入测试服务
from src.chat.features.world_book.services.world_book_service import world_book_service
from src.chat.features.affection.service.affection_service import affection_service
from src.chat.utils.database import chat_db_manager
from src.chat.config import chat_config
from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)

log = logging.getLogger(__name__)

# # 黑名单惩戒短语（随机替换用户发言）
# BLACKLIST_PUNISHMENT_PHRASES = [
#     "我是杂鱼",
#     "我是fw",
#     "我刚刚在放屁",
#     "我错了",
# ]


@dataclass
class GeneratedReply:
    response_text: str
    user_name: str
    user_content_for_memory: str


class ChatService:
    """
    负责编排整个AI聊天响应流程。
    """

    async def should_process_message(self, message: discord.Message) -> bool:
        """
        执行前置检查，判断消息是否应该被处理，以避免不必要的"输入中"状态。
        """
        author = message.author
        guild_id = message.guild.id if message.guild else 0

        # 1. 全局聊天开关检查
        if not await chat_settings_service.is_chat_globally_enabled(guild_id):
            log.info(f"服务器 {guild_id} 全局聊天已禁用，跳过前置检查。")
            return False

        # 2. 频道/分类设置检查
        effective_config = {}
        if isinstance(message.channel, discord.abc.GuildChannel):
            effective_config = await chat_settings_service.get_effective_channel_config(
                message.channel
            )

        if not effective_config.get("is_chat_enabled", True):
            log.info(
                f"频道 {message.channel.id} 聊天已禁用，跳过前置检查。"
            )
            return False

        # 3. 黑名单检查
        if await chat_db_manager.is_user_blacklisted(author.id, guild_id):
            log.info(
                f"用户 {author.id} 在服务器 {guild_id} 处于黑名单，已拒绝消息处理。"
            )
            # 可选：给用户一个提示回复（若上下文允许）
            # await message.reply("你已被加入黑名单，无法使用此功能。")
            return False   # 直接终止前置检查，不进入 AI 调用流程

        # 所有前置检查通过，允许处理消息
        return True
        # # 4. 黑名单检查   
        # if await chat_db_manager.is_user_blacklisted(author.id, guild_id):
        #     log.info(
        #         f"用户 {author.id} 在服务器 {guild_id} 被拉黑，已启用惩戒替换。"
        #     )

        # return True  #可能错误

    async def generate_reply_for_message(
        self,
        message: discord.Message,
        processed_data: Dict[str, Any],
        guild_name: str,
        location_name: str,
    ) -> Optional[GeneratedReply]:
        """
        处理聊天消息，生成并返回AI的最终回复。

        Args:
            message (discord.Message): 原始的 discord 消息对象。
            processed_data (Dict[str, Any]): 由 MessageProcessor 处理后的数据。

        Returns:
            GeneratedReply: AI 生成结果以及发送成功后执行副作用所需的最小上下文。
        """
        author = message.author
        guild_id = message.guild.id if message.guild else 0

        user_profile_data = await world_book_service.get_profile_by_discord_id(
            author.id,
            user_name=author.display_name,
        )
        personal_summary = None
        if user_profile_data:
            personal_summary = user_profile_data.get("personal_summary")

        user_content = processed_data["user_content"]
        replied_content = processed_data["replied_content"]
        image_data_list = processed_data["image_data_list"]
        video_data_list = processed_data.get("video_data_list", [])

        try:
            is_blacklisted = False
            if guild_id:
                is_blacklisted = await chat_db_manager.is_user_blacklisted(
                    author.id, guild_id
                )

            # if is_blacklisted:
            #     punishment_phrase = random.choice(BLACKLIST_PUNISHMENT_PHRASES)
            #     user_content = punishment_phrase
            #     replied_content = ""
            #     image_data_list = []
            #     video_data_list = []
            #     processed_data["user_content"] = user_content
            #     processed_data["replied_content"] = replied_content
            #     processed_data["image_data_list"] = image_data_list
            #     processed_data["video_data_list"] = video_data_list
            #     # log.info(
            #      #   f"用户 {author.id} 在服务器 {guild_id} 被拉黑，已替换发言为惩戒短语: {punishment_phrase}"
            #     #)

            # 2. --- 上下文与知识库检索 ---
            # 获取频道历史上下文
            # 使用新的测试上下文服务
            channel_context = (
                await get_context_service().get_formatted_channel_history_new(
                    message.channel.id,
                    author.id,
                    guild_id,
                    exclude_message_id=message.id,
                )
            )

            # RAG: 从世界书检索相关条目
            # --- RAG 查询优化 ---
            # 如果存在回复内容，则将其与用户当前消息合并，为RAG搜索提供更完整的上下文
            rag_query = user_content
            if replied_content:
                # replied_content 已包含 "> [回复 xxx]:" 等格式
                rag_query = f"{replied_content}\n{user_content}"

            # 在记录 RAG 查询日志前开始计时，用于超时熔断
            rag_start_time = asyncio.get_running_loop().time()
            rag_timeout_seconds = 10.0

            # log.info(f"为 RAG 搜索生成的查询: '{rag_query}'")

            # 统一的检索 query（文本+向量）在这里前置生成，供知识库检索与个人记忆检索复用
            retrieval_query_text: Optional[str] = None
            retrieval_query_embedding: Optional[List[float]] = None
            rag_timeout_fallback: bool = False

            async def _run_rag_with_shared_embedding():
                # 与 WorldBookService 内部的清理逻辑保持一致，确保 embedding 与实际检索 query 对齐
                from src.chat.services.regex_service import regex_service
                import re as _re

                clean_query = regex_service.clean_user_input(rag_query)
                summarized_query = _re.sub(r"<@!?&?\d+>\s*", "", clean_query).strip()

                # 仅在 RAG 可用时生成 embedding；生成失败则传空向量，阻止下游再次调用 embedding API
                embedding: Optional[List[float]] = None
                if summarized_query and gemini_service.is_available():
                    try:
                        embedding = await gemini_service.generate_embedding(
                            text=summarized_query, task_type="retrieval_query"
                        )
                    except Exception as e:
                        log.warning(
                            f"生成 retrieval_query embedding 失败: {e}", exc_info=True
                        )
                        embedding = None

                # 用空列表作为“已尝试但失败”的哨兵值：下游服务收到后会直接返回空结果，不再二次调 embedding
                if not embedding:
                    embedding = []

                entries = await world_book_service.find_entries(
                    latest_query=rag_query,  # 使用合并后的查询
                    user_id=author.id,
                    guild_id=guild_id,
                    user_name=author.display_name,
                    conversation_history=channel_context,
                    query_embedding=embedding,
                )
                return entries, summarized_query, embedding

            try:
                (
                    world_book_entries,
                    retrieval_query_text,
                    retrieval_query_embedding,
                ) = await asyncio.wait_for(
                    _run_rag_with_shared_embedding(),
                    timeout=rag_timeout_seconds,
                )
                rag_elapsed = asyncio.get_running_loop().time() - rag_start_time
                log.info(f"RAG 搜索完成，耗时 {rag_elapsed:.3f}s。")
            except asyncio.TimeoutError:
                rag_elapsed = asyncio.get_running_loop().time() - rag_start_time
                log.warning(
                    f"RAG 搜索触发熔断（超时阈值 {rag_timeout_seconds:.1f}s，实际耗时 {rag_elapsed:.3f}s），已强制终止并返回空结果。"
                )
                world_book_entries = []
                retrieval_query_text = None
                retrieval_query_embedding = None
                rag_timeout_fallback = True

            # --- 新增：集中获取所有上下文数据 ---
            affection_status = await affection_service.get_affection_status(author.id)

            # 3. --- 调用AI生成回复 ---
            # PromptService 内部会处理合并用户消息的逻辑，这里我们总是传递 final_content
            # 记录发送给AI的核心上下文
            # --- 获取当前设置的AI模型 ---
            current_model = await chat_settings_service.get_current_ai_model()
            log.info(f"当前使用的AI模型: {current_model}")

            # 工具开关已改为全局配置，保留兼容参数但不再按帖主区分。
            user_id_for_settings: Optional[str] = None
            log.info("本次消息将使用全局工具开关配置。")

            ai_response = await gemini_service.generate_response(
                author.id,
                guild_id,
                message=user_content,
                channel=message.channel,
                replied_message=replied_content,
                images=image_data_list if image_data_list else None,
                videos=video_data_list if video_data_list else None,
                user_name=author.display_name,
                channel_context=channel_context,
                world_book_entries=world_book_entries,
                personal_summary=personal_summary,
                affection_status=affection_status,
                user_profile_data=user_profile_data,
                guild_name=guild_name,
                location_name=location_name,
                model_name=current_model,  # 传递模型名称
                user_id_for_settings=user_id_for_settings,  # 兼容旧调用链保留
                blacklist_punishment_active=is_blacklisted,
                retrieval_query_text=retrieval_query_text,
                retrieval_query_embedding=retrieval_query_embedding,
                rag_timeout_fallback=rag_timeout_fallback,
            )

            if not ai_response:
                log.info(f"AI服务未返回回复，跳过用户 {author.id}。")
                return None

            # 4. --- 后处理与格式化 ---
            final_response = self._format_ai_response(ai_response)

            # --- 新增：为特定工具调用添加后缀 ---
            if (
                gemini_service.last_called_tools
                and "query_tutorial_knowledge_base" in gemini_service.last_called_tools
            ):
                final_response += chat_config.TUTORIAL_SEARCH_SUFFIX
                # 清空列表，避免影响下一次对话
                gemini_service.last_called_tools = []

            return GeneratedReply(
                response_text=final_response,
                user_name=author.display_name,
                user_content_for_memory=user_content,
            )

        except Exception as e:
            log.error(f"[ChatService] 处理聊天消息时出错: {e}", exc_info=True)
            return GeneratedReply(
                response_text="抱歉，处理你的消息时出现了问题，请稍后再试。",
                user_name=author.display_name,
                user_content_for_memory=processed_data.get("user_content", ""),
            )

    async def handle_chat_message(
        self,
        message: discord.Message,
        processed_data: Dict[str, Any],
        guild_name: str,
        location_name: str,
    ) -> Optional[str]:
        generated_reply = await self.generate_reply_for_message(
            message,
            processed_data,
            guild_name,
            location_name,
        )
        if generated_reply is None:
            return None
        return generated_reply.response_text

    def _format_ai_response(self, ai_response: str) -> str:
        """清理和格式化AI的原始回复。"""
        # 移除可能包含的自身名字前缀
        bot_name_prefix = "类脑娘:"
        if ai_response.startswith(bot_name_prefix):
            ai_response = ai_response[len(bot_name_prefix) :].lstrip()
        # 将多段回复的双换行符替换为单换行符
        return ai_response.replace("\n\n", "\n")

    async def _perform_post_response_tasks(
        self,
        author: discord.User,
        guild_id: int,
        query: str,
        rag_entries: list,
        response: str,
    ):
        """执行发送回复后的任务，如记录日志。"""
        # 好感度和奖励逻辑已前置，此处保留用于未来可能的其他后处理任务

        # 记录 RAG 诊断日志
        # self._log_rag_summary(author, query, rag_entries, response)
        pass

    def _log_rag_summary(
        self, author: discord.User, query: str, entries: list, response: str
    ):
        """生成并记录 RAG 诊断摘要日志。"""
        try:
            if entries:
                doc_details = []
                for entry in entries:
                    distance = entry.get("distance", "N/A")
                    distance_str = (
                        f"{distance:.4f}"
                        if isinstance(distance, (int, float))
                        else str(distance)
                    )
                    content = str(entry.get("content", "N/A")).replace("\n", "\n    ")
                    doc_details.append(
                        f"  - Doc ID: {entry.get('id', 'N/A')}, Distance: {distance_str}\n"
                        f"    Content: {content}"
                    )
                retrieved_docs_summary = "\n" + "\n".join(doc_details)
            else:
                retrieved_docs_summary = " N/A"

            summary_log_message = (
                f"\n--- RAG DIAGNOSTIC SUMMARY ---\n"
                f"User: {author} ({author.id})\n"
                f'Initial Query: "{query}"\n'
                f"Retrieved Docs:{retrieved_docs_summary}\n"
                f'Final AI Response: "{response}"\n'
                f"------------------------------"
            )
            # log.info(summary_log_message)
        except Exception as log_e:
            log.error(f"生成 RAG 诊断摘要日志时出错: {log_e}")


# 创建一个单例
chat_service = ChatService()
