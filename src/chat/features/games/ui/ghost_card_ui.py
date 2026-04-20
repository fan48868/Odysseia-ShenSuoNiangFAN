# -*- coding: utf-8 -*-

import discord
from typing import List, Optional, Tuple
from src.chat.features.games.services.ghost_card_service import ghost_card_service
from src.chat.features.games.config.text_config import text_config


class GhostCardUI:
    """抽鬼牌游戏UI组件"""

    CARD_TO_EMOJI = {
        "A": "🇦",
        "2": "2️⃣",
        "3": "3️⃣",
        "4": "4️⃣",
        "5": "5️⃣",
        "6": "6️⃣",
        "7": "7️⃣",
        "8": "8️⃣",
        "9": "9️⃣",
        "10": "🔟",
        "J": "🇯",
        "Q": "🇶",
        "K": "🇰",
        "🃏": "🃏",
    }

    @staticmethod
    def get_strategy_opening(strategy) -> Tuple[str, str]:
        """根据AI策略获取开局文本和缩略图URL"""
        opening_text = text_config.opening.ai_strategy_text.get(
            strategy.name, "游戏开始"
        )
        thumbnail_url = text_config.opening.ai_strategy_thumbnail.get(strategy.name, "")
        return opening_text, thumbnail_url

    @staticmethod
    def create_game_embed(game_id: str) -> discord.Embed:
        """创建游戏状态嵌入消息"""
        game = ghost_card_service.get_game_state(game_id)
        if not game:
            return discord.Embed(
                title=text_config.errors.title, color=discord.Color.red()
            )

        # 根据策略获取开局文本和缩略图
        opening_text, thumbnail_url = GhostCardUI.get_strategy_opening(
            game["ai_strategy"]
        )

        embed = discord.Embed(title=opening_text, color=discord.Color.gold())

        # 设置缩略图
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        # 添加游戏信息

        # 添加手牌信息
        embed.add_field(
            name=text_config.game_ui.player_hand,
            value=" ".join(
                [
                    GhostCardUI.CARD_TO_EMOJI.get(card, card)
                    for card in game["player_hand"]
                ]
            )
            if game["player_hand"]
            else "无",
            inline=False,  # 改为False以获得更好的显示效果
        )

        embed.add_field(
            name=text_config.game_ui.ai_hand,
            value=f"{len(game['ai_hand'])} {text_config.game_ui.cards_count}",
            inline=True,
        )

        # 添加游戏说明
        if not game["game_over"] and game["current_turn"] != "player":
            embed.add_field(
                name="状态", value=text_config.game_ui.waiting_ai, inline=False
            )

        return embed

    @staticmethod
    def create_reaction_embed(game_id: str, reaction_text: str) -> discord.Embed:
        """创建狮子娘反应嵌入消息"""
        game = ghost_card_service.get_game_state(game_id)
        if not game:
            return discord.Embed(
                title=text_config.errors.title, color=discord.Color.red()
            )

        # 获取反应图片
        embed = discord.Embed(description=reaction_text, color=discord.Color.blue())

        return embed

    @staticmethod
    def create_card_buttons(game_id: str) -> List[discord.ui.Button]:
        """创建抽牌按钮"""
        game = ghost_card_service.get_game_state(game_id)
        if not game or game["game_over"] or game["current_turn"] != "player":
            return []

        buttons = []
        # 玩家现在是从AI手牌中抽牌，所以按钮对应AI手牌
        for i, card in enumerate(game["ai_hand"]):
            # 显示所有AI手牌的按钮
            button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label=f"抽第{i + 1}张牌",
                custom_id=f"ghost_draw_{game_id}_{i}",
            )
            buttons.append(button)

        return buttons

    @staticmethod
    def create_control_buttons(game_id: str) -> List[discord.ui.Button]:
        """创建控制按钮（现在返回空列表）"""
        return []

    @staticmethod
    def create_game_over_embed(
        game_id: str, custom_message: Optional[str] = None
    ) -> discord.Embed:
        """创建游戏结束嵌入消息"""
        game = ghost_card_service.get_game_state(game_id)
        if not game or not game["game_over"]:
            return discord.Embed(title="❌ 游戏未结束", color=discord.Color.red())

        embed = discord.Embed(
            title=text_config.game_ui.game_over_title, color=discord.Color.purple()
        )

        bet_amount = game.get("bet_amount", 0)
        winnings = game.get("winnings", 0)

        if custom_message:
            embed.description = custom_message
            if game["winner"] == "player":
                embed.color = discord.Color.green()
            else:
                embed.color = discord.Color.red()
        elif game["winner"] == "player":
            win_text = "你的手牌已全部出完，恭喜获胜！"
            if bet_amount > 0:
                win_text += f"\n\n你赢得了 **{winnings}** 类脑币！"
            embed.description = win_text
            embed.color = discord.Color.green()
        else:  # AI获胜
            embed.title = text_config.game_ui.ai_win_title
            lose_text = "你最后持有了 🃏，真遗憾，你输了！"
            if bet_amount > 0:
                lose_text += f"\n\n你失去了 **{bet_amount}** 类脑币。"
            embed.description = lose_text
            embed.color = discord.Color.red()
            embed.set_thumbnail(url=text_config.static_urls.AI_WIN_THUMBNAIL)

        return embed

    @staticmethod
    def create_ai_draw_embed(
        game_id: str,
        draw_message: str,
        reaction_text: Optional[str] = None,
        reaction_image_url: Optional[str] = None,
    ) -> discord.Embed:
        """创建AI抽牌嵌入消息，并包含反应"""
        game = ghost_card_service.get_game_state(game_id)
        if not game:
            return discord.Embed(
                title=text_config.errors.title, color=discord.Color.red()
            )

        description = f"*{draw_message}*"
        if reaction_text:
            description += f"\n\n**{reaction_text}**"

        embed = discord.Embed(description=description, color=discord.Color.orange())

        # 如果有反应图片，则使用反应图片，否则使用策略图片
        if reaction_image_url:
            embed.set_thumbnail(url=reaction_image_url)
        else:
            _, thumbnail_url = GhostCardUI.get_strategy_opening(game["ai_strategy"])
            embed.set_thumbnail(url=thumbnail_url)

        return embed
