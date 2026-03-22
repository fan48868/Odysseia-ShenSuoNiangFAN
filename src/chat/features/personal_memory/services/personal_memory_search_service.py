# -*- coding: utf-8 -*-

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.database.database import AsyncSessionLocal
from src.chat.services.gemini_service import gemini_service

log = logging.getLogger(__name__)


class PersonalMemorySearchService:
    """
    从 community.personal_memory_chunks 中召回个人记忆条目：
    - 先根据 query 取最相关 Top10
    - 再随机补 5 条（排除 Top10，避免重复）
    返回总计最多 15 条，用于 Prompt 注入。
    """

    def __init__(self):
        self.top_k_relevant = 10
        self.top_k_random = 5

    @staticmethod
    def _vector_to_pg_str(vec: List[float]) -> str:
        # 使用无空格格式，和项目里 incremental_rag_service 一致，兼容性更好
        return "[" + ",".join(str(float(x)) for x in vec) + "]"

    async def search_with_status(
        self,
        discord_id: int | str,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
    ) -> tuple[List[Dict[str, Any]], bool]:
        """
        返回 (hits, search_succeeded)。

        search_succeeded=True 表示个人记忆向量检索链路完整执行成功；
        即便命中结果为空，也属于成功。
        """
        if not query_text or not query_text.strip():
            return [], False

        if not gemini_service.is_available():
            log.info(
                "RAG/embedding 未启用（未配置 GOOGLE_API_KEYS_LIST），跳过个人记忆向量检索。discord_id=%s",
                discord_id,
            )
            return [], False

        # 支持外部预先生成 query embedding（同一轮对话复用，避免重复调用 embedding API）
        if query_embedding is None:
            try:
                query_embedding = await gemini_service.generate_embedding(
                    text=query_text, task_type="retrieval_query"
                )
            except Exception as e:
                log.error(
                    "为个人记忆检索生成 query embedding 失败。discord_id=%s, err=%s",
                    discord_id,
                    e,
                    exc_info=True,
                )
                return [], False

        if not query_embedding or not isinstance(query_embedding, list):
            log.info(
                "个人记忆向量检索缺少有效 query embedding，视为未成功。discord_id=%s",
                discord_id,
            )
            return [], False

        query_vector_str = self._vector_to_pg_str(query_embedding)
        discord_id_str = str(discord_id)

        # 1) TopK relevant
        relevant_sql = text(
            """
            SELECT
                id,
                memory_type,
                memory_text,
                (embedding <=> :query_vector) as distance
            FROM community.personal_memory_chunks
            WHERE discord_id = :discord_id
              AND memory_type = 'long_term'
            ORDER BY distance ASC
            LIMIT :top_k;
            """
        )

        # 2) Random K (exclude top ids)
        # NOTE: excluded id 列表来自 DB 返回，属于可信数据，这里直接拼接进 SQL 以避免 expanding 参数兼容性问题。
        random_sql_template = """
            SELECT
                id,
                memory_type,
                memory_text
            FROM community.personal_memory_chunks
            WHERE discord_id = :discord_id
              AND memory_type = 'long_term'
            {excluded_clause}
            ORDER BY random()
            LIMIT :random_k;
        """

        async with AsyncSessionLocal() as session:
            relevant_res = await session.execute(
                relevant_sql,
                {
                    "query_vector": query_vector_str,
                    "discord_id": discord_id_str,
                    "top_k": self.top_k_relevant,
                },
            )
            relevant_rows = [dict(row._mapping) for row in relevant_res.fetchall()]
            relevant_ids = [
                int(r["id"]) for r in relevant_rows if r.get("id") is not None
            ]

            excluded_clause = ""
            if relevant_ids:
                excluded_clause = (
                    "AND id NOT IN (" + ",".join(map(str, relevant_ids)) + ")"
                )

            random_sql = text(random_sql_template.format(excluded_clause=excluded_clause))
            random_res = await session.execute(
                random_sql,
                {"discord_id": discord_id_str, "random_k": self.top_k_random},
            )
            random_rows = [dict(row._mapping) for row in random_res.fetchall()]

        hits: List[Dict[str, Any]] = []
        for idx, r in enumerate(relevant_rows, start=1):
            dist = r.get("distance")
            hits.append(
                {
                    "id": r.get("id"),
                    "memory_type": r.get("memory_type") or "unknown",
                    "memory_text": r.get("memory_text") or "",
                    "distance": float(dist) if dist is not None else None,
                    "source": "relevant",
                    "rank": idx,
                }
            )

        for r in random_rows:
            hits.append(
                {
                    "id": r.get("id"),
                    "memory_type": r.get("memory_type") or "unknown",
                    "memory_text": r.get("memory_text") or "",
                    "distance": None,
                    "source": "random",
                    "rank": None,
                }
            )

        return hits, True

    async def search(
        self,
        discord_id: int | str,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        hits, _ = await self.search_with_status(
            discord_id=discord_id,
            query_text=query_text,
            query_embedding=query_embedding,
        )
        return hits


personal_memory_search_service = PersonalMemorySearchService()
