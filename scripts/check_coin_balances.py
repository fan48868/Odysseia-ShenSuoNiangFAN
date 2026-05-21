import os
import sys
import asyncio
import statistics
from datetime import datetime

# 将项目根目录添加到 sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)

from src.chat.utils.database import chat_db_manager


async def generate_detailed_report():
    """
    连接数据库，对用户类脑币进行详细统计分析，并生成报告文件。
    """
    print("--- 正在生成类脑币经济分析报告 ---")

    # 初始化数据库
    await chat_db_manager.init_async()

    query = "SELECT user_id, balance FROM user_coins ORDER BY balance DESC"

    try:
        results = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, fetch="all"
        )

        if not results:
            print("数据库中没有找到任何用户余额信息。")
            return

        # --- 数据处理与统计分析 ---
        all_balances = [row["balance"] for row in results]
        coin_holders = [b for b in all_balances if b > 0]

        if not coin_holders:
            print("所有用户的余额都为0，无法生成有效报告。")
            return

        total_users_with_coins = len(coin_holders)
        total_coins_in_circulation = sum(coin_holders)
        max_balance = max(coin_holders)
        min_balance = min(coin_holders)
        mean_balance = statistics.mean(coin_holders)
        median_balance = statistics.median(coin_holders)

        # 计算百分位数
        sorted_holders = sorted(coin_holders)
        p25 = sorted_holders[int(total_users_with_coins * 0.25)]
        p75 = sorted_holders[int(total_users_with_coins * 0.75)]
        p90 = sorted_holders[int(total_users_with_coins * 0.90)]
        p95 = sorted_holders[int(total_users_with_coins * 0.95)]

        # --- 生成 Markdown 报告 ---
        report_lines = []
        report_lines.append(f"# 类脑币经济分析报告 (万圣节活动)")
        report_lines.append(
            f"**生成时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        report_lines.append("## 核心经济指标")
        report_lines.append("| 指标 | 数值 |")
        report_lines.append("|:---|:---|")
        report_lines.append(f"| 持有类脑币的总用户数 | {total_users_with_coins} |")
        report_lines.append(f"| 类脑币总流通量 | {total_coins_in_circulation} |")
        report_lines.append(f"| 最高个人余额 | {max_balance} |")
        report_lines.append(f"| 最低个人余额 (非零) | {min_balance} |")
        report_lines.append(f"| **平均余额 (Mean)** | {mean_balance:.2f} |")
        report_lines.append(f"| **中位数余额 (Median)** | {median_balance} |")
        report_lines.append("\n")

        report_lines.append("## 用户财富分布 (百分位数)")
        report_lines.append("这有助于了解大部分用户的财富水平。")
        report_lines.append("| 百分位 | 余额 | 含义 |")
        report_lines.append("|:---|:---|:---|")
        report_lines.append(f"| 25% | {p25} | 25%的用户余额低于此数值 |")
        report_lines.append(
            f"| 50% (中位数) | {median_balance} | 50%的用户余额低于此数值 |"
        )
        report_lines.append(f"| 75% | {p75} | 75%的用户余额低于此数值 |")
        report_lines.append(f"| 90% | {p90} | 90%的用户余额低于此数值 |")
        report_lines.append(f"| 95% | {p95} | 95%的用户余额低于此数值 |")
        report_lines.append("\n")

        report_lines.append("## 定价策略建议")
        report_lines.append(
            f"- **普通/消耗品:** 定价可以参考 **25百分位数 ({p25}) 到中位数 ({median_balance})** 的范围，确保大多数活跃用户能买得起。"
        )
        report_lines.append(
            f"- **高级/稀有品:** 定价可以参考 **75百分位数 ({p75}) 到 90百分位数 ({p90})** 的范围，让少数富裕用户有消费目标。"
        )
        report_lines.append(
            f"- **奢侈/限定品:** 定价可以高于 **95百分位数 ({p95})**，作为顶级玩家的追求。"
        )
        report_lines.append("\n")

        report_lines.append("## Top 100 富豪榜")
        report_lines.append("| 排名 | 用户ID | 余额 |")
        report_lines.append("|:---|:---|:---|")
        for i, row in enumerate(results[:100]):
            rank = i + 1
            user_id = row["user_id"]
            balance = row["balance"]
            report_lines.append(f"| {rank} | `{user_id}` | {balance} |")

        # --- 写入文件 ---
        report_content = "\n".join(report_lines)

        # 确保 reports 目录存在
        reports_dir = os.path.join(ROOT_DIR, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        file_path = os.path.join(reports_dir, f"coin_balance_report_{timestamp}.md")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report_content)

        print(f"\n报告已成功生成！\n文件路径: {file_path}")

    except Exception as e:
        print(f"生成报告时发生错误: {e}")


if __name__ == "__main__":
    asyncio.run(generate_detailed_report())
