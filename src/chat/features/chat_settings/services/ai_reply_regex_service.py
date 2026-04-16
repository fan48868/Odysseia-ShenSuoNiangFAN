# -*- coding: utf-8 -*-

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src import config

log = logging.getLogger(__name__)

AI_REPLY_REGEX_RULE_DIRNAME = "chat_regex_rules"


class RegexRuleStoreError(ValueError):
    """AI 回复正则规则存储相关错误。"""


class RegexRuleNotFoundError(RegexRuleStoreError):
    """规则不存在。"""


@dataclass(frozen=True)
class RegexRule:
    rule_id: str
    name: str
    order: int
    enabled: bool
    pattern: str
    replacement: str
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RegexRule":
        required_fields = (
            "rule_id",
            "name",
            "order",
            "enabled",
            "pattern",
            "replacement",
            "created_at",
            "updated_at",
        )
        missing_fields = [field for field in required_fields if field not in payload]
        if missing_fields:
            raise ValueError(f"正则规则缺少字段: {', '.join(missing_fields)}")

        return cls(
            rule_id=str(payload["rule_id"]).strip(),
            name=str(payload["name"]).strip(),
            order=int(payload["order"]),
            enabled=bool(payload["enabled"]),
            pattern=str(payload["pattern"]),
            replacement=str(payload["replacement"]),
            created_at=str(payload["created_at"]).strip(),
            updated_at=str(payload["updated_at"]).strip(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "order": self.order,
            "enabled": self.enabled,
            "pattern": self.pattern,
            "replacement": self.replacement,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class RegexRuleStore:
    """基于 /data/chat_regex_rules 的 AI 回复正则规则存储。"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or config.DATA_DIR
        self.rules_dir = os.path.join(self.base_dir, AI_REPLY_REGEX_RULE_DIRNAME)

    def list_rules(self) -> List[RegexRule]:
        if not os.path.isdir(self.rules_dir):
            return []

        rules: List[RegexRule] = []
        for file_name in os.listdir(self.rules_dir):
            if not file_name.endswith(".json"):
                continue
            file_path = os.path.join(self.rules_dir, file_name)
            try:
                rules.append(self._read_rule_file(file_path))
            except Exception as exc:
                log.warning("读取 AI 回复正则规则失败: %s", exc, exc_info=True)

        return sorted(
            rules,
            key=lambda item: (item.order, item.created_at, item.rule_id),
        )

    def get_rule(self, rule_id: str) -> RegexRule:
        file_path = self._get_rule_file_path(rule_id)
        if not os.path.exists(file_path):
            raise RegexRuleNotFoundError("规则不存在或已被删除。")
        return self._read_rule_file(file_path)

    def save_rule(self, rule: RegexRule) -> None:
        os.makedirs(self.rules_dir, exist_ok=True)
        file_path = self._get_rule_file_path(rule.rule_id)
        with open(file_path, "w", encoding="utf-8") as fp:
            json.dump(rule.to_dict(), fp, ensure_ascii=False, indent=2)
            fp.write("\n")

    def delete_rule(self, rule_id: str) -> None:
        file_path = self._get_rule_file_path(rule_id)
        if not os.path.exists(file_path):
            raise RegexRuleNotFoundError("规则不存在或已被删除。")
        os.remove(file_path)

    @staticmethod
    def _normalize_rule_id(rule_id: str) -> str:
        normalized_id = str(rule_id or "").strip()
        if (
            not normalized_id
            or "/" in normalized_id
            or "\\" in normalized_id
            or normalized_id.endswith(".json")
        ):
            raise RegexRuleNotFoundError("规则不存在或已被删除。")
        return normalized_id

    def _get_rule_file_path(self, rule_id: str) -> str:
        normalized_id = self._normalize_rule_id(rule_id)
        return os.path.join(self.rules_dir, f"{normalized_id}.json")

    @staticmethod
    def _read_rule_file(file_path: str) -> RegexRule:
        with open(file_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        if not isinstance(payload, dict):
            raise ValueError(f"规则文件格式错误: {file_path}")
        return RegexRule.from_dict(payload)


class AIReplyRegexService:
    """管理 AI 回复正则规则，并将其应用到最终输出文本。"""

    def __init__(self, store: Optional[RegexRuleStore] = None):
        self.store = store or RegexRuleStore()

    def list_rules(self) -> List[RegexRule]:
        return self.store.list_rules()

    def get_rule(self, rule_id: str) -> RegexRule:
        return self.store.get_rule(rule_id)

    def create_rule_from_mapping(self, payload: Dict[str, Any]) -> RegexRule:
        normalized = self._normalize_rule_payload(payload)
        now = self._now_iso()
        rule = RegexRule(
            rule_id=uuid.uuid4().hex,
            name=normalized["name"],
            order=normalized["order"],
            enabled=normalized["enabled"],
            pattern=normalized["pattern"],
            replacement=normalized["replacement"],
            created_at=now,
            updated_at=now,
        )
        self.store.save_rule(rule)
        return rule

    def update_rule_from_mapping(self, rule_id: str, payload: Dict[str, Any]) -> RegexRule:
        current = self.store.get_rule(rule_id)
        normalized = self._normalize_rule_payload(payload)
        updated = RegexRule(
            rule_id=current.rule_id,
            name=normalized["name"],
            order=normalized["order"],
            enabled=normalized["enabled"],
            pattern=normalized["pattern"],
            replacement=normalized["replacement"],
            created_at=current.created_at,
            updated_at=self._now_iso(),
        )
        self.store.save_rule(updated)
        return updated

    def delete_rule(self, rule_id: str) -> None:
        self.store.delete_rule(rule_id)

    def apply_rules_to_text(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text

        transformed_text = text
        for rule in self.store.list_rules():
            if not rule.enabled:
                continue
            try:
                transformed_text = re.sub(
                    rule.pattern,
                    rule.replacement,
                    transformed_text,
                )
            except re.error as exc:
                log.warning(
                    "应用 AI 回复正则规则失败，已跳过 | rule_id=%s | name=%s | error=%s",
                    rule.rule_id,
                    rule.name,
                    exc,
                )
        return transformed_text

    @classmethod
    def _normalize_rule_payload(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name", "") or "").strip()
        if not name:
            raise RegexRuleStoreError("名称不能为空。")

        raw_order = str(payload.get("order", "") or "").strip()
        if not raw_order:
            raise RegexRuleStoreError("顺序不能为空。")
        try:
            order = int(raw_order)
        except ValueError as exc:
            raise RegexRuleStoreError("顺序必须是整数。") from exc

        enabled = cls._normalize_enabled(payload.get("enabled"))
        pattern = str(payload.get("pattern", "") or "")
        if not pattern:
            raise RegexRuleStoreError("查找正则表达式不能为空。")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RegexRuleStoreError(f"查找正则表达式无效: {exc}") from exc

        replacement = str(payload.get("replacement", "") or "")

        return {
            "name": name,
            "order": order,
            "enabled": enabled,
            "pattern": pattern,
            "replacement": replacement,
        }

    @staticmethod
    def _normalize_enabled(raw_value: Any) -> bool:
        if isinstance(raw_value, bool):
            return raw_value

        normalized = str(raw_value or "").strip().lower()
        truthy_values = {"true", "1", "yes", "y", "on", "开", "开启", "是", "启用"}
        falsy_values = {"false", "0", "no", "n", "off", "关", "关闭", "否", "禁用"}

        if normalized in truthy_values:
            return True
        if normalized in falsy_values:
            return False
        raise RegexRuleStoreError("是否启用只能填写 true/false、1/0、开/关、是/否。")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")


ai_reply_regex_service = AIReplyRegexService()
