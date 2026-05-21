import logging
from typing import List, Dict, Any

from src.chat.services.event_service import event_service
from src.chat.utils.database import ChatDatabaseManager, chat_db_manager

log = logging.getLogger(__name__)


class FactionService:
    """
    处理与活动派系积分相关的所有业务逻辑。
    """

    def __init__(self, db_manager: ChatDatabaseManager):
        """
        初始化 FactionService。

        Args:
            db_manager: ChatDatabaseManager 的实例。
        """
        self.db = db_manager
        self.event_service = event_service

    async def add_points_to_faction(
        self, user_id: int, item_id: str, points_to_add: int, faction_id: str
    ) -> bool:
        """
        为一个派系增加点数，并记录贡献日志。
        """
        active_event = self.event_service.get_active_event()
        if not active_event:
            log.error("当前没有激活的活动，无法增加点数。")
            return False

        event_id = active_event["event_id"]

        try:
            # 使用事务来确保数据一致性
            # 1. 更新或插入派系分数
            update_query = """
                INSERT INTO event_faction_points (event_id, faction_id, total_points)
                VALUES (?, ?, ?)
                ON CONFLICT(event_id, faction_id) DO UPDATE SET
                    total_points = total_points + excluded.total_points;
            """
            await self.db._execute(
                self.db._db_transaction,
                update_query,
                (event_id, faction_id, points_to_add),
                commit=True,
            )

            # 2. 记录贡献日志
            log_query = """
                INSERT INTO event_contribution_log (user_id, event_id, faction_id, item_id, points_contributed)
                VALUES (?, ?, ?, ?, ?);
            """
            await self.db._execute(
                self.db._db_transaction,
                log_query,
                (user_id, event_id, faction_id, item_id, points_to_add),
                commit=True,
            )

            log.info(f"成功为派系 '{faction_id}' 增加了 {points_to_add} 点分数。")
            return True

        except Exception as e:
            log.error(f"为派系增加点数时发生错误: {e}", exc_info=True)
            return False

    async def get_faction_leaderboard(self) -> List[Dict[str, Any]]:
        """
        获取当前激活活动的派系点数排行榜。
        """
        active_event = self.event_service.get_active_event()
        if not active_event:
            return []

        event_id = active_event["event_id"]

        query = """
            SELECT faction_id, total_points
            FROM event_faction_points
            WHERE event_id = ?
            ORDER BY total_points DESC;
        """
        rows = await self.db._execute(
            self.db._db_transaction, query, (event_id,), fetch="all"
        )

        return [
            {"faction_id": row["faction_id"], "total_points": row["total_points"]}
            for row in rows
        ]

    async def determine_winner_and_end_event(self):
        """
        决定获胜派系并通知 EventService。
        """
        log.info("正在执行活动结算逻辑...")
        leaderboard = await self.get_faction_leaderboard()

        if not leaderboard:
            log.warning("排行榜为空，无法决定获胜派系。")
            return

        winner = leaderboard[0]
        winning_faction_id = winner["faction_id"]

        log.info(f"获胜派系是: {winning_faction_id}，总点数: {winner['total_points']}")

        self.event_service.set_winning_faction(winning_faction_id)


# --- 单例实例 ---
# 现在 FactionService 依赖于 ChatDatabaseManager，
# 我们可以创建一个默认实例供其他模块使用。
faction_service = FactionService(chat_db_manager)
