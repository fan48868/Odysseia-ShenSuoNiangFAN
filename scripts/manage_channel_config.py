#!/usr/bin/env python3
"""
管理狮子娘频道配置的脚本（交互式版本）

用法：
    # 列出服务器的所有频道配置
    python scripts/manage_channel_config.py --list <GUILD_ID>

    # 修改特定频道的配置（交互式）
    python scripts/manage_channel_config.py <GUILD_ID> <CHANNEL_ID>
"""

import argparse
import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.utils.database import ChatDatabaseManager

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_TOKEN")


class ChannelConfigBot(discord.Client):
    def __init__(self, mode, guild_id, channel_id=None, **options):
        super().__init__(**options)
        self.mode = mode
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id) if channel_id else None
        self.db_manager: ChatDatabaseManager | None = None

    async def on_ready(self):
        print(f"\n以 {self.user} 的身份登录成功！")
        print("-" * 50)

        self.db_manager = ChatDatabaseManager()
        await self.db_manager.init_async()

        try:
            if self.mode == "list":
                await self.list_guild_configs()
            elif self.mode == "modify":
                await self.run_interactive_config()
        finally:
            await self.close()

    def get_guild(self):
        return discord.utils.get(self.guilds, id=self.guild_id)

    async def list_guild_configs(self):
        assert self.db_manager is not None, "Database manager not initialized"

        guild = self.get_guild()
        if not guild:
            print(f"错误：找不到服务器 ID {self.guild_id}")
            print("可用的服务器：")
            for g in self.guilds:
                print(f"  - {g.name} (ID: {g.id})")
            return

        print(f"\n服务器: {guild.name} (ID: {guild.id})")
        print("=" * 60)

        global_config = await self.db_manager.get_global_chat_config(self.guild_id)
        print("\n【全局配置】")
        if global_config:
            print(f"  聊天启用: {'✓' if global_config['chat_enabled'] else '✗'}")
        else:
            print("  使用默认配置（聊天启用）")

        channel_configs = await self.db_manager.get_all_channel_configs_for_guild(
            self.guild_id
        )

        if not channel_configs:
            print("\n【频道/分类配置】")
            print("  未设置任何特定频道配置")
            print("\n" + "=" * 60)
            return

        print("\n【频道/分类配置】")
        for config in channel_configs:
            entity_id = config["entity_id"]
            entity_type = config["entity_type"]

            if entity_type == "channel":
                channel = guild.get_channel(entity_id)
                name = channel.name if channel else f"未知频道 (ID: {entity_id})"
                prefix = "#"
            else:
                category = discord.utils.get(guild.categories, id=entity_id)
                name = category.name if category else f"未知分类 (ID: {entity_id})"
                prefix = "📁 "

            active_cache = config["active_chat_cache_enabled"]
            active_cache_text = (
                "✓"
                if active_cache is True
                else "✗"
                if active_cache is False
                else "（继承）"
            )

            print(f"\n  [{entity_type.upper()}] {prefix}{name} (ID: {entity_id})")
            print(
                f"    聊天: {'✓' if config['is_chat_enabled'] else '✗' if config['is_chat_enabled'] is not None else '（继承）'}"
            )
            if entity_type == "channel":
                print(f"    主动聊天缓存: {active_cache_text}")

        print("\n" + "=" * 60)
        print(f"共 {len(channel_configs)} 个配置项")

    async def run_interactive_config(self):
        assert self.db_manager is not None, "Database manager not initialized"

        if self.channel_id is None:
            print("错误：未指定频道 ID")
            return

        guild = self.get_guild()
        if not guild:
            print(f"错误：找不到服务器 ID {self.guild_id}")
            print("可用的服务器：")
            for g in self.guilds:
                print(f"  - {g.name} (ID: {g.id})")
            return

        channel = guild.get_channel(self.channel_id)
        if not channel:
            print(f"错误：在服务器中找不到频道 ID {self.channel_id}")
            print("\n可用的频道：")
            for ch in guild.text_channels:
                print(f"  - #{ch.name} (ID: {ch.id})")
            return

        print(f"\n当前频道: #{channel.name} (ID: {channel.id})")
        print(f"所属服务器: {guild.name} (ID: {guild.id})")
        print("=" * 50)

        while True:
            await self.show_current_config(channel)

            print("\n【配置选项】")
            print("1. 启用/禁用聊天功能")
            print("2. 设置主动聊天缓存")
            print("0. 退出")

            choice = input("\n请选择操作 (0-2): ").strip()

            if choice == "0":
                print("\n退出配置...")
                break
            if choice == "1":
                await self.update_channel_config(channel, "is_chat_enabled")
                continue
            if choice == "2":
                await self.update_channel_config(channel, "active_chat_cache_enabled")
                continue

            print("无效的选择，请重新输入。")

    async def show_current_config(self, channel):
        assert self.db_manager is not None, "Database manager not initialized"

        current_config = await self.db_manager.get_channel_config(
            self.guild_id, channel.id
        )

        print("\n【当前配置】")
        if not current_config:
            print("  未设置特定配置，使用默认设置")
            return

        is_enabled = current_config["is_chat_enabled"]
        print(
            f"  聊天功能: {'启用' if is_enabled else '禁用' if is_enabled is not None else '（继承）'}"
        )

        active_cache = current_config["active_chat_cache_enabled"]
        print(
            "  主动聊天缓存: "
            + (
                "启用"
                if active_cache is True
                else "禁用"
                if active_cache is False
                else "（继承）"
            )
        )

    async def update_channel_config(self, channel, field_name: str):
        assert self.db_manager is not None, "Database manager not initialized"

        field_label = (
            "聊天功能" if field_name == "is_chat_enabled" else "主动聊天缓存"
        )
        print(f"\n【设置{field_label}】")
        print("1. 启用")
        print("2. 禁用")
        print("3. 继承（清除设置）")

        choice = input("请选择 (1-3): ").strip()
        if choice not in {"1", "2", "3"}:
            print("无效的选择")
            return

        new_value = True if choice == "1" else False if choice == "2" else None
        current_config = await self.db_manager.get_channel_config(
            self.guild_id, channel.id
        )

        is_chat_enabled = (
            new_value
            if field_name == "is_chat_enabled"
            else current_config["is_chat_enabled"]
            if current_config
            else None
        )
        active_chat_cache_enabled = (
            new_value
            if field_name == "active_chat_cache_enabled"
            else current_config["active_chat_cache_enabled"]
            if current_config
            else None
        )

        await self.db_manager.update_channel_config(
            guild_id=self.guild_id,
            entity_id=channel.id,
            entity_type="channel",
            is_chat_enabled=is_chat_enabled,
            active_chat_cache_enabled=active_chat_cache_enabled,
        )

        print(f"\n✓ 频道 #{channel.name} 的{field_label}已更新")


async def main():
    parser = argparse.ArgumentParser(
        description="管理狮子娘频道配置",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scripts/manage_channel_config.py --list 123456789
  python scripts/manage_channel_config.py 123456789 987654321
        """,
    )

    parser.add_argument("--list", metavar="GUILD_ID", help="列出指定服务器的所有频道配置")
    parser.add_argument("guild_id", nargs="?", metavar="GUILD_ID", help="服务器ID（修改模式必需）")
    parser.add_argument("channel_id", nargs="?", metavar="CHANNEL_ID", help="频道ID（修改模式可选）")

    args = parser.parse_args()

    if args.list:
        guild_id = args.list
        channel_id = None
        mode = "list"
    else:
        if not args.guild_id:
            print("错误：修改模式需要 GUILD_ID")
            print("用法: python scripts/manage_channel_config.py <GUILD_ID> [CHANNEL_ID]")
            print("       python scripts/manage_channel_config.py --list <GUILD_ID>")
            sys.exit(1)
        guild_id = args.guild_id
        channel_id = args.channel_id
        mode = "modify"

    if not BOT_TOKEN:
        print("错误：在 .env 文件中未找到 DISCORD_TOKEN")
        print("请确保 .env 文件在项目根目录，并且包含 'DISCORD_TOKEN=你的令牌'")
        sys.exit(1)

    intents = discord.Intents.default()
    intents.guilds = True
    intents.message_content = True

    client = ChannelConfigBot(
        mode=mode, guild_id=guild_id, channel_id=channel_id, intents=intents
    )

    try:
        await client.start(BOT_TOKEN)
    except discord.LoginFailure:
        print("错误：机器人令牌无效，请检查令牌是否正确")
    except KeyboardInterrupt:
        print("\n操作已取消")


if __name__ == "__main__":
    asyncio.run(main())
