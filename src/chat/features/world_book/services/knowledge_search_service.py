# -*- coding: utf-8 -*-
import asyncio
import logging
import re
from typing import Any, List, Dict, Optional, Tuple

from sqlalchemy import text
from src.database.database import AsyncSessionLocal
from src.chat.services.gemini_service import gemini_service

log = logging.getLogger(__name__)


class KnowledgeSearchService:
    """
    提供对社区成员和通用知识的混合搜索功能。
    使用 ParadeDB 的 BM25 和向量搜索能力。
    """

    def __init__(self):
        log.info("KnowledgeSearchService 已初始化")
        # 在未来可以从配置中加载参数
        self.config = {
            "TOP_K_VECTOR": 30,  # 增加召回量，防止指令词(如"开始xx模式")导致核心词排名下降
            "TOP_K_FTS": 30,     # 增加召回量，提高关键字匹配的容错率
            "RRF_K": 60,
            "HYBRID_SEARCH_POOL_K": 10, # SQL 侧保留适度候选池，兼顾最终配比与查询开销
            "FINAL_RESULT_LIMIT": 5, # 最终注入给上层的结果数量
            "MAX_MEMBER_RESULTS": 2, # 最终结果里，成员名片类结果最多保留 2 条
            "TARGET_KNOWLEDGE_RESULTS": 3, # 最终结果里，优先保证 3 条知识条目
            "VECTOR_DISTANCE_THRESHOLD": 0.5, # 向量搜索距离阈值，过滤不相关结果
            "KEYWORD_WEIGHT": 3.0, # 关键字搜索权重，提高精确匹配的重要性
            "EMBEDDING_TIMEOUT_SECONDS": 7.0, # embedding 生成超时保护
            "HYBRID_SEARCH_TIMEOUT_SECONDS": 9.0, # 数据库混合检索超时保护
        }

    def _clean_fts_query(self, query: str) -> str:
        """
        清理全文搜索查询，移除可能导致 paradedb 解析错误的特殊字符。
        只保留字母、数字、中日韩统一表意文字和空格。
        """
        # 正则表达式匹配所有非（字母、数字、CJK字符、空格）的字符
        cleaned_query = re.sub(r"[^\w\s\u4e00-\u9fff]", "", query)
        log.debug(f"原始 FTS 查询: '{query}' -> 清理后: '{cleaned_query}'")
        return cleaned_query

    async def _hybrid_search_chunks(
        self, session, query_text: str, query_vector: List[float]
    ) -> List[Dict[str, Any]]:
        """
        在 community.member_chunks 和 general_knowledge.knowledge_chunks
        两个表中执行混合搜索，并返回融合排序后的 chunk 结果。
        """
        # SQL 查询同时搜索两个 chunks 表
        sql_query = text(
            """
            WITH vector_candidates AS (
                -- 社区成员向量搜索
                (SELECT
                    'community' as source_table,
                    id as chunk_id,
                    profile_id as parent_id,
                    chunk_text,
                    (embedding <=> :query_vector) as distance
                FROM community.member_chunks
                WHERE (embedding <=> :query_vector) < :max_distance
                ORDER BY distance ASC
                LIMIT :top_k_vector)
                UNION ALL
                -- 通用知识向量搜索
                (SELECT
                    'general_knowledge' as source_table,
                    id as chunk_id,
                    document_id as parent_id,
                    chunk_text,
                    (embedding <=> :query_vector) as distance
                FROM general_knowledge.knowledge_chunks
                WHERE (embedding <=> :query_vector) < :max_distance
                ORDER BY distance ASC
                LIMIT :top_k_vector)
            ),
            semantic_search AS (
                -- 对合并后的结果进行全局排名
                SELECT 
                    *,
                    ROW_NUMBER() OVER (ORDER BY distance ASC) as rank
                FROM vector_candidates
            ),
            keyword_candidates AS (
                -- 社区成员 BM25 搜索 (使用 paradedb.score)
                (SELECT
                    'community' as source_table,
                    id as chunk_id,
                    profile_id as parent_id,
                    chunk_text,
                    paradedb.score(id) as bm25_score
                FROM community.member_chunks
                WHERE chunk_text @@@ :query_text
                LIMIT :top_k_fts)
                UNION ALL
                -- 通用知识 BM25 搜索 (使用 paradedb.score)
                (SELECT
                    'general_knowledge' as source_table,
                    id as chunk_id,
                    document_id as parent_id,
                    chunk_text,
                    paradedb.score(id) as bm25_score
                FROM general_knowledge.knowledge_chunks
                WHERE chunk_text @@@ :query_text
                LIMIT :top_k_fts)
            ),
            keyword_search AS (
                -- 对合并后的结果进行全局排名
                SELECT 
                    *,
                    ROW_NUMBER() OVER (ORDER BY bm25_score DESC) as rank
                FROM keyword_candidates
            ),
            -- 使用 RRF (Reciprocal Rank Fusion) 融合排名
            fused_ranks AS (
                SELECT
                    COALESCE(s.chunk_id, k.chunk_id) as chunk_id,
                    COALESCE(s.parent_id, k.parent_id) as document_id,
                    COALESCE(s.source_table, k.source_table) as source_table,
                    COALESCE(s.chunk_text, k.chunk_text) as chunk_text,
                    (COALESCE(1.0 / (:rrf_k + s.rank), 0.0) + COALESCE(:keyword_weight / (:rrf_k + k.rank), 0.0)) as rrf_score
                FROM semantic_search s
                FULL OUTER JOIN keyword_search k ON s.chunk_id = k.chunk_id AND s.source_table = k.source_table
            )
            SELECT *
            FROM fused_ranks
            ORDER BY rrf_score DESC
            LIMIT :final_k;
            """
        )
        result = await session.execute(
            sql_query,
            {
                "query_text": query_text,
                "query_vector": str(query_vector),
                "top_k_vector": self.config["TOP_K_VECTOR"],
                "top_k_fts": self.config["TOP_K_FTS"],
                "rrf_k": self.config["RRF_K"],
                "final_k": self.config["HYBRID_SEARCH_POOL_K"],
                "max_distance": self.config["VECTOR_DISTANCE_THRESHOLD"],
                "keyword_weight": self.config["KEYWORD_WEIGHT"],
            },
        )
        # SQLAlchemy 2.x 的 Row 对象需要通过 ._mapping 转换为字典
        return [dict(row._mapping) for row in result.fetchall()]

    def _format_result(
        self,
        *,
        parent_id: Any,
        chunk_text: str,
        source_table: str,
        distance: float,
        is_user_card: bool = False,
    ) -> Dict[str, Any]:
        metadata = {"source_table": source_table}
        if is_user_card:
            metadata["is_user_card"] = True

        return {
            "id": parent_id,
            "content": chunk_text,
            "distance": distance,
            "metadata": metadata,
        }

    def _select_final_results(
        self,
        search_results: List[Dict[str, Any]],
        user_card_chunk: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        对混合检索结果做最终收口：
        1. 发起者名片强制置顶。
        2. 社区成员结果最多 2 条（包含发起者自己的名片）。
        3. 优先保留 3 条知识条目。
        4. 按 source_table + parent_id 去重，避免同一名片/知识条目重复出现。
        """
        final_limit = self.config["FINAL_RESULT_LIMIT"]
        member_limit = self.config["MAX_MEMBER_RESULTS"]
        target_knowledge = min(self.config["TARGET_KNOWLEDGE_RESULTS"], final_limit)

        final_results: List[Dict[str, Any]] = []
        deferred_candidates: List[Tuple[str, Dict[str, Any]]] = []
        seen_entries: set[Tuple[str, str]] = set()

        member_count = 0
        knowledge_count = 0

        if user_card_chunk:
            user_parent_id = user_card_chunk.get("parent_id")
            if user_parent_id is not None:
                seen_entries.add(("community", str(user_parent_id)))
                final_results.append(
                    self._format_result(
                        parent_id=user_parent_id,
                        chunk_text=user_card_chunk.get("chunk_text", ""),
                        source_table="community",
                        distance=0.0,
                        is_user_card=True,
                    )
                )
                member_count = 1

        unique_candidates: List[Tuple[str, Dict[str, Any]]] = []
        for res in search_results:
            source_table = str(res.get("source_table") or "")
            document_id = res.get("document_id")
            if not source_table or document_id is None:
                continue

            dedupe_key = (source_table, str(document_id))
            if dedupe_key in seen_entries:
                continue
            seen_entries.add(dedupe_key)

            rrf_score = float(res.get("rrf_score") or 0.0)
            unique_candidates.append(
                (
                    source_table,
                    self._format_result(
                        parent_id=document_id,
                        chunk_text=res.get("chunk_text", ""),
                        source_table=source_table,
                        distance=1.0 - rrf_score,
                    ),
                )
            )

        for source_table, result in unique_candidates:
            if len(final_results) >= final_limit:
                break

            if source_table == "community":
                if member_count >= member_limit:
                    continue
                final_results.append(result)
                member_count += 1
                continue

            if knowledge_count < target_knowledge:
                final_results.append(result)
                knowledge_count += 1
            else:
                deferred_candidates.append((source_table, result))

        if len(final_results) < final_limit:
            for source_table, result in deferred_candidates:
                if len(final_results) >= final_limit:
                    break
                if source_table == "community" and member_count >= member_limit:
                    continue
                if source_table == "community":
                    member_count += 1
                else:
                    knowledge_count += 1
                final_results.append(result)

        return final_results

    async def search(
        self,
        query: str,
        user_id: Optional[int] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行完整的 RAG 混合搜索流程。
        1. 生成查询嵌入（支持外部传入 query_embedding，传入则不再调用 embedding API）。
        2. 在 chunks 表中进行混合搜索。
        3. (暂定)直接返回 chunks 内容，因为我们的场景下，chunk 可能就是全部。
        """
        # log.info(f"收到知识库混合搜索请求: '{query}'")

        # 1) 生成/复用查询向量
        if query_embedding is None:
            try:
                query_embedding = await asyncio.wait_for(
                    gemini_service.generate_embedding(text=query, task_type="retrieval_query"),
                    timeout=self.config["EMBEDDING_TIMEOUT_SECONDS"],
                )
                if not query_embedding or not isinstance(query_embedding, list):
                    raise ValueError(
                        f"Embedding 生成失败，返回为空或类型错误: {type(query_embedding)}"
                    )
            except asyncio.TimeoutError:
                log.warning(
                    f"为查询生成 embedding 超时（>{self.config['EMBEDDING_TIMEOUT_SECONDS']:.1f}s），触发熔断并返回空结果。"
                )
                return []
            except Exception as e:
                log.error(f"为查询生成 embedding 时出错: {e}", exc_info=True)
                return []
        else:
            if not isinstance(query_embedding, list) or not query_embedding:
                return []

        search_results = []
        try:
            # 清理用于全文搜索的查询文本
            cleaned_fts_query = self._clean_fts_query(query)

            user_card_chunk = None
            async with AsyncSessionLocal() as session:
                # 如果提供了 user_id，尝试获取该用户的名片并强制置顶
                if user_id:
                    try:
                        user_card_query = text("""
                            SELECT 
                                c.id as chunk_id,
                                c.profile_id as parent_id,
                                c.chunk_text,
                                'community' as source_table
                            FROM community.member_chunks c
                            JOIN community.member_profiles p ON c.profile_id = p.id
                            WHERE p.discord_id = :user_id OR p.source_metadata->>'uploaded_by' = :user_id
                            LIMIT 1
                        """)
                        result = await session.execute(user_card_query, {"user_id": str(user_id)})
                        row = result.fetchone()
                        if row:
                            user_card_chunk = dict(row._mapping)
                            log.info(f"成功获取并置顶用户 {user_id} 的名片")
                    except Exception as e:
                        log.error(f"获取用户 {user_id} 名片失败: {e}")

                search_results = await asyncio.wait_for(
                    self._hybrid_search_chunks(
                        session, cleaned_fts_query, query_embedding
                    ),
                    timeout=self.config["HYBRID_SEARCH_TIMEOUT_SECONDS"],
                )
                # log.info(f"混合搜索 RRF 结果: {search_results}")

        except asyncio.TimeoutError:
            log.warning(
                f"数据库混合搜索超时（>{self.config['HYBRID_SEARCH_TIMEOUT_SECONDS']:.1f}s），触发熔断并返回空结果。"
            )
            return []
        except Exception as e:
            log.error(f"在数据库中执行混合搜索时出错: {e}", exc_info=True)
            return []

        if not search_results and not user_card_chunk:
            log.info(f"知识库混合搜索未找到的相关文档。")
            return []

        return self._select_final_results(search_results, user_card_chunk)


# 创建服务的单例
knowledge_search_service = KnowledgeSearchService()
