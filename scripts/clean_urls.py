#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
简易脚本用于清理channel_message.md文件中的URL
去除png和jpg后面的查询参数和片段标识符
"""

import re
import sys
from pathlib import Path

def clean_image_urls_in_content(content: str, file_type: str) -> str:
    """
    清理文本内容中的图片URL，去除png和jpg后面的查询参数和片段标识符。
    根据文件类型使用不同的正则表达式模式。
    
    Args:
        content (str): 文件内容
        file_type (str): 文件类型，例如 "md" 或 "py"
        
    Returns:
        str: 清理后的文件内容
    """
    # 定义正则表达式模式来匹配URL
    if file_type == "md":
        # 匹配Markdown文件中的缩略图和大图URL行，包括查询参数
        url_pattern = r'(\*   \*\*Embed (?:缩略图|大图) URL:\*\* `)(https?://[^`]+?\.(png|jpg)(?:\?[^`]*)?)(`)'
    elif file_type == "py":
        # 匹配Python文件中字典值中的URL，例如 "KEY": "https://..."
        url_pattern = r'("https?://[^"]+?\.(png|jpg)(?:\?[^"]*)?)(?=")'
    else:
        return content # 不支持的文件类型，直接返回原内容

    # 替换函数
    def clean_url(match):
        if file_type == "md":
            prefix = match.group(1)  # 前缀部分
            url = match.group(2)     # URL部分
            suffix = match.group(4)  # 后缀部分
        elif file_type == "py":
            url = match.group(1)     # URL部分
            prefix = ""
            suffix = ""
        
        # 去除.png或.jpg后面的查询参数
        if '.png?' in url:
            clean_url = url.split('.png?')[0] + '.png'
        elif '.jpg?' in url:
            clean_url = url.split('.jpg?')[0] + '.jpg'
        else:
            clean_url = url  # 如果没有查询参数，保持原样
        
        return f"{prefix}{clean_url}{suffix}"
    
    # 执行替换
    cleaned_content = re.sub(url_pattern, clean_url, content)
    return cleaned_content

def process_file(file_path: Path) -> bool:
    """
    处理单个文件，清理其中的图片URL。
    
    Args:
        file_path (Path): 文件路径
        
    Returns:
        bool: 如果成功清理或无需清理则返回True，否则返回False
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"错误：文件 '{file_path}' 不存在")
        return False
    except Exception as e:
        print(f"读取文件 '{file_path}' 时出错：{e}")
        return False

    file_type = file_path.suffix[1:] # 获取文件扩展名，例如 "md", "py"
    cleaned_content = clean_image_urls_in_content(content, file_type)
    
    if cleaned_content != content:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_content)
            print(f"成功清理了文件 '{file_path}' 中的URL")
            return True
        except Exception as e:
            print(f"写入文件 '{file_path}' 时出错：{e}")
            return False
    else:
        print(f"文件 '{file_path}' 中没有需要清理的URL")
        return True

def main():
    """主函数"""
    # 需要处理的文件列表
    files_to_process = [
        Path("channel_message.md"),
        Path("src/games/config/text_config.py")
    ]
    
    all_success = True
    for file_path in files_to_process:
        if not file_path.exists():
            print(f"错误：文件 '{file_path}' 不存在，跳过处理。")
            all_success = False
            continue
        
        success = process_file(file_path)
        if not success:
            all_success = False
            
    if all_success:
        print("\n所有指定文件的URL清理完成")
    else:
        print("\n部分文件URL清理失败")
        sys.exit(1)

if __name__ == "__main__":
    main()