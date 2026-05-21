# -*- coding: utf-8 -*-

import random
from games.services.ghost_card_service import GhostCardService, AIStrategy

def simulate_game(player_id: int, guild_id: int, ai_strategy: AIStrategy):
    """模拟一局抽鬼牌游戏"""
    service = GhostCardService()
    game_id = service.start_new_game(player_id, guild_id, ai_strategy) # 传递ai_strategy参数
    
    while True:
        game_state = service.get_game_state(game_id)
        if not game_state or game_state["game_over"]:
            return game_state["winner"], game_state["ai_strategy"]

        if game_state["current_turn"] == "player":
            # 玩家回合：从AI手牌中随机抽一张
            # 先检查是否已经凑齐王和8
            if "👑" in game_state["player_hand"] and "8️⃣" in game_state["player_hand"]:
                return "ai", game_state["ai_strategy"]  # 玩家凑齐王和8，AI赢
            
            if not game_state["ai_hand"]:
                return "ai", game_state["ai_strategy"] # AI手牌为空，玩家赢
            card_index = random.randint(0, len(game_state["ai_hand"]) - 1)
            success, _ = service.player_draw_card(game_id, card_index)
            if not success:
                # 玩家抽牌失败，通常不应该发生，除非逻辑有误
                return "error", game_state["ai_strategy"]
        else:
            # AI回合：从玩家手牌中随机抽一张
            # 先检查是否已经凑齐王和8
            if "👑" in game_state["ai_hand"] and "8️⃣" in game_state["ai_hand"]:
                return "player", game_state["ai_strategy"]  # AI凑齐王和8，玩家赢
            
            if not game_state["player_hand"]:
                return "player", game_state["ai_strategy"] # 玩家手牌为空，AI赢
            # AI的抽牌逻辑已经封装在ai_draw_card中
            success, _ = service.ai_draw_card(game_id)
            if not success:
                # AI抽牌失败，通常不应该发生，除非逻辑有误
                return "error", game_state["ai_strategy"]

def simulate_game_random_strategy(player_id: int, guild_id: int):
    """模拟一局抽鬼牌游戏，使用随机策略"""
    service = GhostCardService()
    # 不传递ai_strategy参数，让游戏服务自动随机选择策略
    game_id = service.start_new_game(player_id, guild_id)
    
    while True:
        game_state = service.get_game_state(game_id)
        if not game_state or game_state["game_over"]:
            return game_state["winner"], game_state["ai_strategy"]

        if game_state["current_turn"] == "player":
            # 玩家回合：从AI手牌中随机抽一张
            # 先检查是否已经凑齐王和8
            if "👑" in game_state["player_hand"] and "8️⃣" in game_state["player_hand"]:
                return "ai", game_state["ai_strategy"]  # 玩家凑齐王和8，AI赢
            
            if not game_state["ai_hand"]:
                return "ai", game_state["ai_strategy"] # AI手牌为空，玩家赢
            card_index = random.randint(0, len(game_state["ai_hand"]) - 1)
            success, _ = service.player_draw_card(game_id, card_index)
            if not success:
                # 玩家抽牌失败，通常不应该发生，除非逻辑有误
                return "error", game_state["ai_strategy"]
        else:
            # AI回合：从玩家手牌中随机抽一张
            # 先检查是否已经凑齐王和8
            if "👑" in game_state["ai_hand"] and "8️⃣" in game_state["ai_hand"]:
                return "player", game_state["ai_strategy"]  # AI凑齐王和8，玩家赢
            
            if not game_state["player_hand"]:
                return "player", game_state["ai_strategy"] # 玩家手牌为空，AI赢
            # AI的抽牌逻辑已经封装在ai_draw_card中
            success, _ = service.ai_draw_card(game_id)
            if not success:
                # AI抽牌失败，通常不应该发生，除非逻辑有误
                return "error", game_state["ai_strategy"]

def run_simulations(num_games: int, ai_strategy: AIStrategy):
    """运行多局模拟并统计胜率"""
    player_wins = 0
    ai_wins = 0
    draws = 0 # 抽鬼牌没有平局，但为了通用性保留
    errors = 0

    print(f"开始模拟 {num_games} 局游戏，AI策略: {ai_strategy.name}...")

    for i in range(num_games):
        player_id = 1000 + i # 模拟不同的玩家ID
        guild_id = 2000 # 模拟公会ID
        winner, _ = simulate_game(player_id, guild_id, ai_strategy) # 传递ai_strategy参数

        if winner == "player":
            player_wins += 1
        elif winner == "ai":
            ai_wins += 1
        elif winner == "draw":
            draws += 1
        else:
            errors += 1
        
        if (i + 1) % 100 == 0:
            print(f"已完成 {i + 1}/{num_games} 局模拟...")

    total_games = num_games - errors
    player_win_rate = (player_wins / total_games) * 100 if total_games > 0 else 0
    ai_win_rate = (ai_wins / total_games) * 100 if total_games > 0 else 0
    draw_rate = (draws / total_games) * 100 if total_games > 0 else 0

    print("\n--- 模拟结果 ---")
    print(f"总局数: {num_games}")
    print(f"有效局数: {total_games}")
    print(f"玩家胜利: {player_wins} ({player_win_rate:.2f}%)")
    print(f"AI胜利: {ai_wins} ({ai_win_rate:.2f}%)")
    print(f"平局: {draws} ({draw_rate:.2f}%)")
    if errors > 0:
        print(f"错误局数: {errors}")

def run_random_strategy_simulations(num_games: int):
    """运行多局随机策略模拟并统计胜率"""
    player_wins = 0
    ai_wins = 0
    draws = 0 # 抽鬼牌没有平局，但为了通用性保留
    errors = 0
    
    # 统计每种策略的使用情况
    strategy_stats = {
        AIStrategy.LOW: {"used": 0, "player_wins": 0, "ai_wins": 0},
        AIStrategy.MEDIUM: {"used": 0, "player_wins": 0, "ai_wins": 0},
        AIStrategy.HIGH: {"used": 0, "player_wins": 0, "ai_wins": 0},
        AIStrategy.SUPER: {"used": 0, "player_wins": 0, "ai_wins": 0}
    }

    print(f"开始模拟 {num_games} 局游戏，使用随机AI策略...")

    for i in range(num_games):
        player_id = 1000 + i # 模拟不同的玩家ID
        guild_id = 2000 # 模拟公会ID
        winner, strategy = simulate_game_random_strategy(player_id, guild_id)
        
        # 统计策略使用情况
        if strategy in strategy_stats:
            strategy_stats[strategy]["used"] += 1
            
            if winner == "player":
                strategy_stats[strategy]["player_wins"] += 1
            elif winner == "ai":
                strategy_stats[strategy]["ai_wins"] += 1

        if winner == "player":
            player_wins += 1
        elif winner == "ai":
            ai_wins += 1
        elif winner == "draw":
            draws += 1
        else:
            errors += 1
        
        if (i + 1) % 1000 == 0:
            print(f"已完成 {i + 1}/{num_games} 局模拟...")

    total_games = num_games - errors
    player_win_rate = (player_wins / total_games) * 100 if total_games > 0 else 0
    ai_win_rate = (ai_wins / total_games) * 100 if total_games > 0 else 0
    draw_rate = (draws / total_games) * 100 if total_games > 0 else 0

    print("\n--- 随机策略模拟结果 ---")
    print(f"总局数: {num_games}")
    print(f"有效局数: {total_games}")
    print(f"玩家胜利: {player_wins} ({player_win_rate:.2f}%)")
    print(f"AI胜利: {ai_wins} ({ai_win_rate:.2f}%)")
    print(f"平局: {draws} ({draw_rate:.2f}%)")
    if errors > 0:
        print(f"错误局数: {errors}")
    
    print("\n--- 各策略详细统计 ---")
    for strategy, stats in strategy_stats.items():
        used = stats["used"]
        if used > 0:
            player_wins_for_strategy = stats["player_wins"]
            ai_wins_for_strategy = stats["ai_wins"]
            total_for_strategy = player_wins_for_strategy + ai_wins_for_strategy
            player_win_rate_for_strategy = (player_wins_for_strategy / total_for_strategy) * 100 if total_for_strategy > 0 else 0
            ai_win_rate_for_strategy = (ai_wins_for_strategy / total_for_strategy) * 100 if total_for_strategy > 0 else 0
            
            print(f"{strategy.value}策略:")
            print(f"  使用次数: {used} ({(used/total_games)*100:.2f}%)")
            print(f"  玩家胜利: {player_wins_for_strategy} ({player_win_rate_for_strategy:.2f}%)")
            print(f"  AI胜利: {ai_wins_for_strategy} ({ai_win_rate_for_strategy:.2f}%)")
        else:
            print(f"{strategy.value}策略: 未使用")

if __name__ == "__main__":
    # 可以根据需要修改模拟的局数和AI策略
    num_simulations = 1000
    
    # 模拟LOW策略
    run_simulations(num_simulations, AIStrategy.LOW)
    print("-" * 30)
    # 模拟MEDIUM策略
    run_simulations(num_simulations, AIStrategy.MEDIUM)
    print("-" * 30)
    # 模拟HIGH策略
    run_simulations(num_simulations, AIStrategy.HIGH)
    print("-" * 30)
    # 模拟SUPER策略
    run_simulations(num_simulations, AIStrategy.SUPER)
    print("=" * 50)
    
    # 模拟10000局随机策略
    print("开始10000局随机策略模拟...")
    run_random_strategy_simulations(10000)