import os
import sys
import logging

# 配置日志记录
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)

# 将项目根目录添加到 Python 路径中
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.services.event_service import event_service
from src.chat.services.prompt_service import prompt_service
from src.chat.config import prompts as default_prompts


def run_test():
    """
    执行派系化标签覆盖功能的验证测试。
    """
    print("--- 验证测试开始 ---")

    # --- 场景一：无获胜派系 ---
    print("\n--- 场景一：无获胜派系 ---")
    print("预期结果：SYSTEM_PROMPT 应为默认版本。")
    event_service.winning_faction_id = None  # 确保没有获胜派系
    system_prompt_default = prompt_service.get_prompt("SYSTEM_PROMPT")

    if "<万圣节" not in system_prompt_default:
        print("✅ 验证通过：SYSTEM_PROMPT 是默认版本。")
    else:
        print("❌ 验证失败：SYSTEM_PROMPT 不应包含活动特定内容。")
    # print("\n" + system_prompt_default[:500] + "...") # 打印部分内容以供检查

    # --- 场景二：教会派系获胜 ---
    print("\n--- 场景二：教会派系获胜 ---")
    print("预期结果：SYSTEM_PROMPT 中的 <core_identity> 应被替换为教会版本。")
    event_service.set_winning_faction("church")
    system_prompt_church = prompt_service.get_prompt("SYSTEM_PROMPT")

    if "【万圣节·教会派系】" in system_prompt_church:
        print("✅ 验证通过：已成功加载教会派系人设。")
    else:
        print("❌ 验证失败：未能加载教会派系人设。")
    print("\n" + system_prompt_church)

    # --- 场景三：吸血鬼派系获胜 ---
    print("\n--- 场景三：吸血鬼派系获胜 ---")
    print("预期结果：SYSTEM_PROMPT 中的 <core_identity> 应被替换为吸血鬼版本。")
    event_service.set_winning_faction("vampire")
    system_prompt_vampire = prompt_service.get_prompt("SYSTEM_PROMPT")

    if "【万圣节·吸血鬼派系】" in system_prompt_vampire:
        print("✅ 验证通过：已成功加载吸血鬼派系人设。")
    else:
        print("❌ 验证失败：未能加载吸血鬼派系人设。")
    print("\n" + system_prompt_vampire)

    print("\n--- 验证测试结束 ---")


if __name__ == "__main__":
    run_test()
