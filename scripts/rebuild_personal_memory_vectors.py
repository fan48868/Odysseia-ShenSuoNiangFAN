# -*- coding: utf-8 -*-
"""
回填/重建个人记忆向量（personal_summary -> community.personal_memory_chunks）
用法示例：
  # 处理所有 personal_summary 非空用户
  python scripts/rebuild_personal_memory_vectors.py
  
  # 只处理一个用户（即使 summary 为空也会执行：为空则删除向量）
  python scripts/rebuild_personal_memory_vectors.py --user-id 123456789012345678
  
  # 限制处理数量（用于小规模验证）
  python scripts/rebuild_personal_memory_vectors.py --limit 20
  
  # 调试：随机抽样 5 个用户
  python scripts/rebuild_personal_memory_vectors.py --debug
  
  # 只统计（不写入 DB；仍会解析 summary，但不会生成 embedding 也不会写入）
  python scripts/rebuild_personal_memory_vectors.py --dry-run
"""

import asyncio
import logging
import os
import sys
import argparse
from pathlib import Path
from typing import List, Tuple, Optional

from dotenv import load_dotenv
from sqlalchemy import text, func
from sqlalchemy.future import select

# --- Path and Module Configuration ---
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 在导入服务前加载环境变量，确保 GOOGLE_API_KEYS_LIST 等可用
load_dotenv()

# --- Project Imports ---
from src.database.database import AsyncSessionLocal
from src.database.models import CommunityMemberProfile
from src.chat.services.gemini_service import gemini_service
from src.chat.features.personal_memory.services.personal_memory_vector_service import (
    personal_memory_vector_service,
)

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


async def _check_target_table_exists() -> bool:
    """检查 personal_memory_chunks 表是否存在。"""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text("SELECT to_regclass('community.personal_memory_chunks');")
        )
        table_name = res.scalar()
        if not table_name:
            log.error(
                "表 community.personal_memory_chunks 不存在。\n"
                "请先在你的部署环境中执行数据库迁移（alembic upgrade head），或手动应用迁移脚本：\n"
                "  alembic/versions/9c6f2a1b3d4e_add_personal_memory_chunks_table.py"
            )
            return False
        return True


async def _load_targets(
    user_id: Optional[str],
    limit: Optional[int],
    debug: bool,
) -> List[Tuple[str, Optional[str]]]:
    """
    返回待处理用户列表：[(discord_id, personal_summary), ...]
    """
    async with AsyncSessionLocal() as session:
        if user_id:
            stmt = select(
                CommunityMemberProfile.discord_id,
                CommunityMemberProfile.personal_summary,
            ).where(CommunityMemberProfile.discord_id == str(user_id))
        else:
            stmt = (
                select(
                    CommunityMemberProfile.discord_id,
                    CommunityMemberProfile.personal_summary,
                )
                .where(CommunityMemberProfile.personal_summary.isnot(None))
                .where(CommunityMemberProfile.personal_summary != "")
            )

            if debug:
                stmt = stmt.order_by(func.random()).limit(5)
            elif limit and limit > 0:
                stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        rows = result.all()
        return [(str(r[0]), r[1]) for r in rows]


async def main():
    parser = argparse.ArgumentParser(description="回填/重建个人记忆向量（personal_summary -> personal_memory_chunks）")
    parser.add_argument("--user-id", type=str, default=None, help="只处理指定 Discord ID（字符串或数字均可）")
    parser.add_argument("--limit", type=int, default=None, help="限制处理的用户数量（仅在未指定 --user-id 时生效）")
    parser.add_argument("--debug", action="store_true", help="随机抽样 5 个用户（仅在未指定 --user-id 时生效）")
    parser.add_argument("--dry-run", action="store_true", help="只统计解析结果，不写入向量表（也不会生成 embedding）")
    args = parser.parse_args()

    log.info("--- Personal Memory Vector Rebuild Script Started ---")

    # 1) 基础检查：表是否存在
    if not await _check_target_table_exists():
        return

    # 2) 加载目标用户
    targets = await _load_targets(args.user_id, args.limit, args.debug)
    if not targets:
        if args.user_id:
            log.warning("未找到指定用户档案：discord_id=%s", args.user_id)
        else:
            log.info("未找到任何 personal_summary 非空的用户，无需回填。")
        return

    log.info("本次将处理 %s 个用户。", len(targets))

    # 3) dry-run：只解析 summary，不生成 embedding、不写入
    if args.dry_run:
        total_items = 0
        for discord_id, summary in targets:
            items = personal_memory_vector_service.parse_personal_summary(summary or "")
            total_items += len(items)
            log.info("dry-run: discord_id=%s, parsed_items=%s", discord_id, len(items))
        log.info("dry-run 完成：users=%s, total_parsed_items=%s", len(targets), total_items)
        return

    # 4) embedding 可用性检查（真正写入前）
    if not gemini_service.is_available():
        log.error(
            "Gemini embedding 不可用（未配置 GOOGLE_API_KEYS_LIST 或 key_rotation_service 初始化失败）。\n"
            "请先在 .env 中设置 GOOGLE_API_KEYS_LIST，然后重试。"
        )
        return

    # 5) 逐用户重建
    processed_users = 0
    total_inserted = 0

    for idx, (discord_id, summary) in enumerate(targets, start=1):
        try:
            log.info("[%s/%s] 开始处理 discord_id=%s", idx, len(targets), discord_id)

            inserted = await personal_memory_vector_service.rebuild_vectors_for_user(
                discord_id=discord_id,
                personal_summary=summary,
            )
            total_inserted += int(inserted or 0)
            processed_users += 1

            log.info(
                "[%s/%s] 完成 discord_id=%s, inserted=%s",
                idx,
                len(targets),
                discord_id,
                inserted,
            )
        except Exception as e:
            log.error(
                "[%s/%s] 处理 discord_id=%s 失败：%s",
                idx,
                len(targets),
                discord_id,
                e,
                exc_info=True,
            )

    log.info(
        "--- 完成：processed_users=%s, total_inserted=%s ---",
        processed_users,
        total_inserted,
    )


if __name__ == "__main__":
    asyncio.run(main())