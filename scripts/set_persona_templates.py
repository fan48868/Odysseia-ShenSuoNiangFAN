# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import re
import json
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 将 src 目录添加到 Python 路径中
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from guidance.utils.database import guidance_db_manager as db_manager
from config import GUILD_ID

def parse_markdown_templates(file_path):
    """
    解析 persona_templates.md 文件，提取所有消息模板。
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    templates = {}
    template_pattern = re.compile(r"###\s*\d+\.\s*.*?`(.+?)`\)(.+?)(?=\n###|\Z)", re.S)
    
    for match in template_pattern.finditer(content):
        template_name, block_content = match.groups()
        messages = []

        msg_split_pattern = r"(\*\s*消息\s*\d+:)"
        parts = re.split(msg_split_pattern, block_content)
        
        message_blocks = []
        if len(parts) > 1:
            for i in range(1, len(parts), 2):
                if i + 1 < len(parts):
                    full_msg = parts[i] + parts[i+1]
                    message_blocks.append(full_msg.strip())
        else:
            message_blocks.append(block_content.strip())

        for msg_content in message_blocks:
            msg_data = {}
            
            def extract_field(pattern, text):
                match = re.search(pattern, text, re.S)
                if match:
                    return match.group(1).strip()
                return None

            # 最终修正：在字段名两侧添加 \*\* 来匹配 Markdown 的粗体。
            msg_data['title'] = extract_field(r"\s*\*\s*\*\*Embed 标题:\*\*\s*`(.+?)`", msg_content)
            
            description_raw = extract_field(r"\s*\*\s*\*Embed 描述:\*\*\s*(.+?)(?=\n\s*\*|\Z)", msg_content)
            if description_raw:
                msg_data['description'] = description_raw.strip()

            msg_data['image_url'] = extract_field(r"\s*\*\s*\*Embed 大图 URL:\*\*\s*`(.+?)`", msg_content)
            msg_data['thumbnail_url'] = extract_field(r"\s*\*\s*\*Embed 缩略图 URL:\*\*\s*`(.+?)`", msg_content)
            msg_data['footer_text'] = extract_field(r"\s*\*\s*\*Embed 底部文字:\*\*\s*`(.+?)`", msg_content)
            
            msg_data = {k: v for k, v in msg_data.items() if v is not None}

            if msg_data:
                messages.append(msg_data)

        templates[template_name] = messages

    return templates

async def main():
    """主函数，用于连接数据库并更新所有模板。"""
    if not GUILD_ID:
        print("错误：请在 .env 文件中设置 GUILD_ID")
        return

    guild_id = int(GUILD_ID)

    pass
    
    print("正在解析 persona_templates.md 文件...")
    try:
        # 获取脚本所在目录的父目录，然后拼接 persona_templates.md
        script_dir = os.path.dirname(__file__)
        templates_file = os.path.join(script_dir, '..', 'docs', 'persona_templates.md')
        templates_data = parse_markdown_templates(templates_file)
        print(f"✅ 解析成功，找到 {len(templates_data)} 个模板。")
    except Exception as e:
        print(f"❌ 解析 markdown 文件时出错: {e}")
        return

    print("\n正在初始化数据库管理器...")
    
    # 检查是否提供了 --force 参数
    force_update = "--force" in sys.argv
    if force_update:
        print("⚠️  检测到 --force 参数，将强制覆盖所有现有模板。")
    else:
        print("✨  运行在安全模式下。仅初始化不存在的模板。使用 --force 参数可强制覆盖。")

    # 如果是强制更新，先删除该服务器的所有旧模板
    if force_update:
        print(f"正在为服务器 {guild_id} 删除所有旧模板...")
        try:
            # 调用新添加的、正确的方法
            deleted_count = await db_manager.delete_all_message_templates(guild_id)
            print(f"✅ {deleted_count} 个旧模板已删除。")
        except Exception as e:
            print(f"❌ 删除旧模板时出错: {e}")
            return # 如果删除失败，则停止执行

    for template_name, template_data in templates_data.items():
        try:
            # 在非强制模式下，我们仍然检查模板是否存在
            if not force_update:
                existing_template = await db_manager.get_message_template(guild_id, template_name)
                if existing_template:
                    print(f"ℹ️  模板 '{template_name}' 已存在，跳过。")
                    continue
            
            action = "覆盖" if force_update else "初始化"
            print(f"正在为服务器 {guild_id} {action}模板 '{template_name}'...")
            await db_manager.set_message_template(
                guild_id=guild_id,
                template_name=template_name,
                template_data=template_data
            )
            print(f"✅ 模板 '{template_name}' {action}成功。")
        except Exception as e:
            print(f"❌ 处理模板 '{template_name}' 时发生错误: {e}")

    print("\n所有模板更新完成。")

if __name__ == "__main__":
    asyncio.run(main())