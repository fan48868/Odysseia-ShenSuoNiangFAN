# 可复用活动框架配置指南

本文档将指导你如何配置一个新的节日或主题活动。本框架旨在通过简单的配置文件来快速创建和部署新活动，无需修改任何代码。

## 目录
1. [基本概念](#1-基本概念)
2. [文件夹结构](#2-文件夹结构)
3. [配置文件详解](#3-配置文件详解)
    - [3.1 `manifest.json`](#31-manifestjson)
    - [3.2 `factions.json`](#32-factionsjson)
    - [3.3 `items.json`](#33-itemsjson)
    - [3.4 `prompts.json`](#34-promptsjson)
4. [配置新活动的完整步骤](#4-配置新活动的完整步骤)
5. [注意事项](#5-注意事项)

---

### 1. 基本概念

本活动框架的核心是一个“派系竞争”系统。用户通过在活动商店购买特殊商品来为自己支持的派系增加“点数”。活动结束后，点数最高的派系获胜，机器人的行为（主要是对话提示词）可能会根据获胜派系发生改变。

---

### 2. 文件夹结构

所有活动都存储在 `src/chat/events/` 目录下。每个活动都是一个独立的子文件夹，文件夹的名称即为该活动的唯一 **`event_id`**。

```
src/
└── chat/
    └── events/
        ├── halloween_2025/      <-- event_id
        │   ├── manifest.json
        │   ├── factions.json
        │   ├── items.json
        │   └── prompts.json
        └── christmas_2025/      <-- 另一个活动的 event_id
            ├── ...
```

---

### 3. 配置文件详解

每个活动文件夹内包含四个核心的 JSON 配置文件。

#### 3.1 `manifest.json`

这是活动的核心配置文件，定义了活动的基本信息和生命周期。

- **`event_id`**: (字符串) 活动的唯一标识符，**必须与文件夹名称完全一致**。
- **`event_name`**: (字符串) 活动的显示名称，会出现在UI中。
- **`description`**: (字符串) 活动的简短描述。
- **`is_active`**: (布尔值) `true` 表示该活动当前处于激活状态，机器人会加载它。`false` 则会忽略它。**在任何时候，都应该只有一个活动的 `is_active` 为 `true`**。
- **`start_date`**: (字符串) 活动开始时间，采用 ISO 8601 格式 (UTC时间)。例如: `"2025-10-20T00:00:00Z"`。
- **`end_date`**: (字符串) 活动结束时间，结算逻辑将在此时间后触发。
- **`entry_panel`**: (对象) 定义了在类脑商店中活动入口的显示样式。
  - **`title`**: (字符串) 入口面板的标题。
  - **`description`**: (字符串) 入口面板的描述文本。
  - **`image_url`**: (字符串, 可选) 在入口面板上显示的图片链接。

#### 3.2 `factions.json`

定义活动中的所有派系。

这是一个对象数组，每个对象代表一个派系。
- **`faction_id`**: (字符串) 派系的唯一ID，例如 `"werewolf"`。
- **`faction_name`**: (字符串) 派系的显示名称，例如 `"狼人"`.
- **`description`**: (字符串) 派系的简短描述。

#### 3.3 `items.json`

定义活动商店中可供购买的专属商品。

这是一个对象数组，每个对象代表一个商品。
- **`item_id`**: (字符串/整数) 商品的唯一ID。
- **`item_name`**: (字符串) 商品的显示名称。
- **`description`**: (字符串) 商品的描述。
- **`price`**: (整数) 商品的价格（单位：类脑币）。
- **`faction_id`**: (字符串) 购买此商品将为哪个派系增加点数。**必须与 `factions.json` 中的一个 `faction_id` 对应**。
- **`points`**: (整数) 购买此商品后，为对应派系增加的点数。

#### 3.4 `prompts.json`

定义活动期间需要覆盖的默认提示词，以及获胜派系的专属提示词。

- **`overrides`**: (对象) 一个键值对，其中 `key` 是要覆盖的原始提示词的名称（例如 `SYSTEM_PROMPT`），`value` 是活动期间使用的新提示词内容。
- **`winner_prompts`**: (对象) 一个特殊的配置，用于定义获胜派系的专属提示词。
  - **key**: 派系ID (必须与 `factions.json` 中的 `faction_id` 对应)。
  - **value**: (对象) 一个键值对，结构与 `overrides` 相同，但只在该派系获胜后生效。

---

### 4. 配置新活动的完整步骤

1.  在 `src/chat/events/` 目录下，创建一个新的文件夹，文件夹名称将作为你的 `event_id` (例如 `new_year_2026`)。
2.  将现有活动（如 `halloween_2025`）中的四个 JSON 文件复制到你的新活动文件夹中。
3.  **修改 `manifest.json`**:
    -   更新 `event_id` 以匹配文件夹名称。
    -   设置新的 `event_name` 和 `description`。
    -   设置正确的 `start_date` 和 `end_date`。
    -   将 `is_active` 设置为 `true`。（**请确保其他所有活动的 `is_active` 都为 `false`**）。
    -   自定义 `entry_panel` 的内容。
4.  **修改 `factions.json`**: 根据你的新活动主题，定义全新的派系列表。
5.  **修改 `items.json`**: 设计你的活动商品，确保每个商品的 `faction_id` 都能在 `factions.json` 中找到。
6.  **修改 `prompts.json`**:
    -   在 `overrides` 中设置活动期间通用的提示词。
    -   在 `winner_prompts` 中，为**每一个**你在 `factions.json` 中定义的派系设置获胜后的专属提示词。
7.  **重启机器人**: 重启后，`EventService` 会自动加载并激活你的新活动配置。

---

### 5. 注意事项

- **时间的准确性**: 所有日期和时间都使用 UTC 标准。请确保转换到你的本地时区进行正确设置。
- **ID 的一致性**: 确保 `manifest.json` 中的 `event_id`、`items.json` 中的 `faction_id` 以及 `prompts.json` 中 `winner_prompts` 的键，都与 `factions.json` 中定义的ID严格对应。
- **单一激活**: 永远只保持一个活动的 `is_active` 为 `true`，否则系统可能无法正确加载活动。