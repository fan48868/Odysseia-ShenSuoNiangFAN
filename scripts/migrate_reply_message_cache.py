import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime_env import load_project_dotenv, resolve_project_root
from src.chat.services.context_service_test import CachedReplyMessage


log = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 data/reply_message_cache 下的旧格式缓存升级为完整消息快照格式。"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="reply_message_cache 目录路径，默认使用项目 data/reply_message_cache。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="迁移结果输出目录，默认使用项目 data/reply_message_cache_migrated。",
    )
    parser.add_argument(
        "--channel-id",
        type=int,
        default=None,
        help="只迁移单个频道对应的 reply_cache_<channel_id>.json。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使文件已经是新格式，也重新拉取并覆盖。",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="仅在 --in-place 时有效：不生成 .bak 备份文件。",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="直接覆盖原文件。默认关闭；关闭时会写入新的输出目录。",
    )
    return parser.parse_args()


def get_cache_dir(script_path: str, configured: Optional[Path]) -> Path:
    if configured is not None:
        return configured
    project_root = Path(resolve_project_root(script_path, parents=1))
    return project_root / "data" / "reply_message_cache"


def get_output_dir(script_path: str, configured: Optional[Path]) -> Path:
    if configured is not None:
        return configured
    project_root = Path(resolve_project_root(script_path, parents=1))
    return project_root / "data" / "reply_message_cache_migrated"


def get_token(script_path: str) -> str:
    dotenv_path = load_project_dotenv(script_path, parents=1)
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            f"DISCORD_TOKEN 未在 {dotenv_path} 中找到，无法执行消息补全迁移。"
        )
    return token


def iter_cache_files(cache_dir: Path, channel_id: Optional[int]) -> list[Path]:
    if channel_id is not None:
        target = cache_dir / f"reply_cache_{channel_id}.json"
        return [target] if target.is_file() else []
    return sorted(cache_dir.glob("reply_cache_*.json"))


def parse_channel_id(file_path: Path) -> Optional[int]:
    name = file_path.stem
    prefix = "reply_cache_"
    if not name.startswith(prefix):
        return None
    try:
        return int(name[len(prefix) :])
    except ValueError:
        return None


def load_json_list(file_path: Path) -> list[dict[str, Any]]:
    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, list) else []


def is_new_format(entry: dict[str, Any]) -> bool:
    return "content" in entry and "created_at_ts" in entry


def build_cached_reply_message(message: discord.Message) -> CachedReplyMessage:
    created_at = getattr(message, "created_at", None)
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_ts = created_at.timestamp()
    else:
        created_at_ts = 0.0

    reply_to_message_id = None
    reply_to_author_display_name = None
    reference = getattr(message, "reference", None)
    if reference and getattr(reference, "message_id", None):
        reply_to_message_id = int(reference.message_id)
        resolved = getattr(reference, "resolved", None)
        if resolved is None:
            resolved = getattr(reference, "cached_message", None)
        if resolved and getattr(resolved, "author", None):
            reply_to_author_display_name = resolved.author.display_name

    return CachedReplyMessage(
        message_id=int(message.id),
        author_display_name=(
            message.author.display_name
            if getattr(message, "author", None)
            else "未知用户"
        ),
        content=getattr(message, "content", "") or "",
        created_at_ts=created_at_ts,
        is_bot=bool(getattr(getattr(message, "author", None), "bot", False)),
        reply_to_message_id=reply_to_message_id,
        reply_to_author_display_name=reply_to_author_display_name,
        has_attachments=bool(getattr(message, "attachments", [])),
    )


async def enrich_reply_author(
    channel: Any,
    entry: CachedReplyMessage,
    resolved_entries: dict[int, CachedReplyMessage],
) -> CachedReplyMessage:
    if entry.reply_to_author_display_name or not entry.reply_to_message_id:
        return entry

    cached = resolved_entries.get(entry.reply_to_message_id)
    if cached and cached.author_display_name:
        entry.reply_to_author_display_name = cached.author_display_name
        return entry

    fetch_message = getattr(channel, "fetch_message", None)
    if fetch_message is None:
        return entry

    try:
        referenced = await fetch_message(entry.reply_to_message_id)
    except discord.NotFound:
        return entry
    except discord.Forbidden:
        return entry
    except Exception as exc:
        log.warning(
            "补全 reply_to_author_display_name 失败 | channel_id=%s | reply_to=%s | error=%s",
            getattr(channel, "id", None),
            entry.reply_to_message_id,
            exc,
        )
        return entry

    resolved = build_cached_reply_message(referenced)
    resolved_entries[resolved.message_id] = resolved
    entry.reply_to_author_display_name = resolved.author_display_name
    return entry


async def migrate_file(
    client: discord.Client,
    file_path: Path,
    *,
    output_dir: Path,
    in_place: bool,
    force: bool,
    backup: bool,
) -> tuple[str, int]:
    channel_id = parse_channel_id(file_path)
    if channel_id is None:
        return ("skipped", 0)

    raw_entries = load_json_list(file_path)
    if not raw_entries:
        return ("empty", 0)

    if not force and all(is_new_format(entry) for entry in raw_entries):
        return ("already_new", len(raw_entries))

    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception as exc:
            log.warning("获取频道失败 | channel_id=%s | error=%s", channel_id, exc)
            return ("channel_error", 0)

    fetch_message = getattr(channel, "fetch_message", None)
    if fetch_message is None:
        log.warning("频道不支持 fetch_message，跳过 | channel_id=%s", channel_id)
        return ("unsupported_channel", 0)

    resolved_entries: dict[int, CachedReplyMessage] = {}
    migrated_entries: list[CachedReplyMessage] = []

    for raw_entry in raw_entries:
        message_id = raw_entry.get("message_id")
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            continue

        try:
            message = await fetch_message(message_id)
        except discord.NotFound:
            # 老数据已经删掉时，尽量保留已有字段，并补默认值。
            fallback = CachedReplyMessage.from_dict(raw_entry)
            if fallback is None:
                fallback = CachedReplyMessage(
                    message_id=message_id,
                    author_display_name=str(raw_entry.get("author_display_name") or "未知用户"),
                    content=str(raw_entry.get("content") or ""),
                    created_at_ts=float(raw_entry.get("created_at_ts", 0.0) or 0.0),
                    is_bot=bool(raw_entry.get("is_bot", False)),
                    reply_to_message_id=raw_entry.get("reply_to_message_id"),
                    reply_to_author_display_name=raw_entry.get(
                        "reply_to_author_display_name"
                    ),
                    has_attachments=bool(raw_entry.get("has_attachments", False)),
                )
            migrated_entries.append(fallback)
            resolved_entries[fallback.message_id] = fallback
            continue
        except discord.Forbidden:
            log.warning("没有权限读取消息，跳过 | channel_id=%s | message_id=%s", channel_id, message_id)
            continue
        except Exception as exc:
            log.warning(
                "获取消息失败 | channel_id=%s | message_id=%s | error=%s",
                channel_id,
                message_id,
                exc,
            )
            continue

        cached = build_cached_reply_message(message)
        migrated_entries.append(cached)
        resolved_entries[cached.message_id] = cached

    for entry in migrated_entries:
        await enrich_reply_author(channel, entry, resolved_entries)

    migrated_entries.sort(key=lambda entry: (entry.created_at_ts, entry.message_id))
    data_to_write = [entry.to_dict() for entry in migrated_entries]

    destination = file_path if in_place else output_dir / file_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)

    if in_place and backup:
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, backup_path)

    with destination.open("w", encoding="utf-8") as file:
        json.dump(data_to_write, file, ensure_ascii=False)

    return ("migrated", len(migrated_entries))


async def main() -> None:
    setup_logging()
    args = parse_args()
    cache_dir = get_cache_dir(__file__, args.cache_dir)
    output_dir = get_output_dir(__file__, args.output_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not args.in_place:
        output_dir.mkdir(parents=True, exist_ok=True)

    files = iter_cache_files(cache_dir, args.channel_id)
    if not files:
        log.info("未找到需要处理的 reply_message_cache 文件。")
        return

    token = get_token(__file__)

    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info("机器人已登录为 %s，开始迁移 reply_message_cache。", client.user)
        summary: dict[str, int] = {}

        for file_path in files:
            status, count = await migrate_file(
                client,
                file_path,
                output_dir=output_dir,
                in_place=args.in_place,
                force=args.force,
                backup=not args.no_backup,
            )
            summary[status] = summary.get(status, 0) + 1
            log.info(
                "处理完成 | file=%s | status=%s | entries=%s | mode=%s",
                file_path.name,
                status,
                count,
                "in_place" if args.in_place else f"copy_to:{output_dir}",
            )

        log.info("迁移摘要: %s", summary)
        await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
