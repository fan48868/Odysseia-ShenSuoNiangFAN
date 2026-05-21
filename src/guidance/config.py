# -*- coding: utf-8 -*-

"""
存储 Guidance 模块相关的非敏感、硬编码的常量。
"""

# --- 用户进度状态 ---
# 用于 user_progress 表中的 status 字段
USER_STATUS_IN_PROGRESS = "in_progress"
USER_STATUS_COMPLETED = "completed"
USER_STATUS_CANCELLED = "cancelled"
USER_STATUS_PENDING_SELECTION = "pending_selection"

# --- 路径管理 ---
MAX_PATH_STEPS = 10 # 一条路径最多包含的频道数量

# --- 消息模板 ---
TEMPLATE_TYPES = {
    "welcome_message": {"label": "欢迎消息", "emoji": "👋", "description": "用户获得初始身份组后，收到的第一条私信，引导其选择兴趣标签。", "multiple": True},
    "prompt_message_stage_1": {"label": "一阶段提示消息", "emoji": "👇", "description": "用户选择标签后，在私信中更新的消息，显示第一阶段路径并提供出发按钮。", "multiple": True},
    "welcome_message_stage_2": {"label": "二阶段欢迎消息", "emoji": "✨", "description": "用户获得第二阶段身份组后，收到的私信，告知已解锁新内容。", "multiple": True},
    "completion_message_stage_1": {"label": "一阶段感谢消息", "emoji": "🎉", "description": "用户完成第一阶段所有步骤后收到的消息。", "multiple": True},
    "completion_message_stage_2": {"label": "二阶段感谢消息", "emoji": "💖", "description": "用户完成第二阶段所有步骤后收到的最终感谢消息。", "multiple": True}
}

# --- 引导流程消息 ---
GUIDANCE_COMPLETION_MESSAGE = "🎉 **恭喜！您已完成所有引导！**"