# -*- coding: utf-8 -*-

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src import config

log = logging.getLogger(__name__)

CUSTOM_MODEL_PRESET_DIRNAME = "custom_model_presets"
MAX_CUSTOM_MODEL_PRESETS = 25


class CustomModelPresetStoreError(ValueError):
    """custom 模型预设存储相关错误。"""


class CustomModelPresetDuplicateNameError(CustomModelPresetStoreError):
    """预设名重复。"""


class CustomModelPresetLimitError(CustomModelPresetStoreError):
    """预设数量超出上限。"""


class CustomModelPresetNotFoundError(CustomModelPresetStoreError):
    """预设不存在。"""


@dataclass(frozen=True)
class CustomModelPreset:
    preset_id: str
    name: str
    custom_model_url: str
    custom_model_api_key: str
    custom_model_name: str
    custom_model_enable_vision: str
    custom_model_enable_video_input: str
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, payload: Dict[str, str]) -> "CustomModelPreset":
        required_fields = (
            "preset_id",
            "name",
            "custom_model_url",
            "custom_model_api_key",
            "custom_model_name",
            "custom_model_enable_vision",
            "custom_model_enable_video_input",
            "created_at",
            "updated_at",
        )
        missing_fields = [field for field in required_fields if field not in payload]
        if missing_fields:
            raise ValueError(
                f"custom 模型预设缺少字段: {', '.join(missing_fields)}"
            )

        return cls(
            preset_id=str(payload["preset_id"]).strip(),
            name=str(payload["name"]).strip(),
            custom_model_url=str(payload["custom_model_url"]).strip(),
            custom_model_api_key=str(payload["custom_model_api_key"]).strip(),
            custom_model_name=str(payload["custom_model_name"]).strip(),
            custom_model_enable_vision=str(
                payload["custom_model_enable_vision"]
            ).strip(),
            custom_model_enable_video_input=str(
                payload["custom_model_enable_video_input"]
            ).strip(),
            created_at=str(payload["created_at"]).strip(),
            updated_at=str(payload["updated_at"]).strip(),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "preset_id": self.preset_id,
            "name": self.name,
            "custom_model_url": self.custom_model_url,
            "custom_model_api_key": self.custom_model_api_key,
            "custom_model_name": self.custom_model_name,
            "custom_model_enable_vision": self.custom_model_enable_vision,
            "custom_model_enable_video_input": self.custom_model_enable_video_input,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CustomModelPresetStore:
    """基于 /data/custom_model_presets 的 custom 模型预设存储。"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or config.DATA_DIR
        self.presets_dir = os.path.join(self.base_dir, CUSTOM_MODEL_PRESET_DIRNAME)

    def list_presets(self) -> List[CustomModelPreset]:
        if not os.path.isdir(self.presets_dir):
            return []

        presets: List[CustomModelPreset] = []
        for file_name in os.listdir(self.presets_dir):
            if not file_name.endswith(".json"):
                continue
            file_path = os.path.join(self.presets_dir, file_name)
            try:
                presets.append(self._read_preset_file(file_path))
            except Exception as exc:
                log.warning("读取 custom 模型预设失败: %s", exc, exc_info=True)

        return sorted(presets, key=lambda item: (item.created_at, item.preset_id))

    def get_preset(self, preset_id: str) -> CustomModelPreset:
        file_path = self._get_preset_file_path(preset_id)
        if not os.path.exists(file_path):
            raise CustomModelPresetNotFoundError("预设不存在或已被删除。")
        return self._read_preset_file(file_path)

    def create_preset(self, *, name: str, settings: Dict[str, str]) -> CustomModelPreset:
        normalized_name = self._normalize_preset_name(name)
        presets = self.list_presets()
        if len(presets) >= MAX_CUSTOM_MODEL_PRESETS:
            raise CustomModelPresetLimitError(
                f"最多可以保存 {MAX_CUSTOM_MODEL_PRESETS} 个预设。"
            )
        self._ensure_name_is_unique(normalized_name)

        now = self._now_iso()
        preset = CustomModelPreset(
            preset_id=uuid.uuid4().hex,
            name=normalized_name,
            custom_model_url=str(settings.get("custom_model_url", "")).strip(),
            custom_model_api_key=str(settings.get("custom_model_api_key", "")).strip(),
            custom_model_name=str(settings.get("custom_model_name", "")).strip(),
            custom_model_enable_vision=str(
                settings.get("custom_model_enable_vision", "")
            ).strip(),
            custom_model_enable_video_input=str(
                settings.get("custom_model_enable_video_input", "")
            ).strip(),
            created_at=now,
            updated_at=now,
        )
        self._write_preset_file(preset)
        return preset

    def rename_preset(self, preset_id: str, *, name: str) -> CustomModelPreset:
        current = self.get_preset(preset_id)
        normalized_name = self._normalize_preset_name(name)
        self._ensure_name_is_unique(normalized_name, exclude_preset_id=current.preset_id)

        updated = CustomModelPreset(
            preset_id=current.preset_id,
            name=normalized_name,
            custom_model_url=current.custom_model_url,
            custom_model_api_key=current.custom_model_api_key,
            custom_model_name=current.custom_model_name,
            custom_model_enable_vision=current.custom_model_enable_vision,
            custom_model_enable_video_input=current.custom_model_enable_video_input,
            created_at=current.created_at,
            updated_at=self._now_iso(),
        )
        self._write_preset_file(updated)
        return updated

    def update_preset_settings(
        self, preset_id: str, *, settings: Dict[str, str]
    ) -> CustomModelPreset:
        current = self.get_preset(preset_id)
        updated = CustomModelPreset(
            preset_id=current.preset_id,
            name=current.name,
            custom_model_url=str(settings.get("custom_model_url", "")).strip(),
            custom_model_api_key=str(settings.get("custom_model_api_key", "")).strip(),
            custom_model_name=str(settings.get("custom_model_name", "")).strip(),
            custom_model_enable_vision=str(
                settings.get("custom_model_enable_vision", "")
            ).strip(),
            custom_model_enable_video_input=str(
                settings.get("custom_model_enable_video_input", "")
            ).strip(),
            created_at=current.created_at,
            updated_at=self._now_iso(),
        )
        self._write_preset_file(updated)
        return updated

    def delete_preset(self, preset_id: str) -> None:
        file_path = self._get_preset_file_path(preset_id)
        if not os.path.exists(file_path):
            raise CustomModelPresetNotFoundError("预设不存在或已被删除。")
        os.remove(file_path)

    def _ensure_name_is_unique(
        self, preset_name: str, *, exclude_preset_id: Optional[str] = None
    ) -> None:
        for preset in self.list_presets():
            if exclude_preset_id and preset.preset_id == exclude_preset_id:
                continue
            if preset.name.strip() == preset_name:
                raise CustomModelPresetDuplicateNameError(
                    f"预设名 `{preset_name}` 已存在，请使用其他名称。"
                )

    @staticmethod
    def _normalize_preset_name(name: str) -> str:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise CustomModelPresetStoreError("预设名称不能为空。")
        return normalized_name

    @staticmethod
    def _normalize_preset_id(preset_id: str) -> str:
        normalized_id = str(preset_id or "").strip()
        if (
            not normalized_id
            or "/" in normalized_id
            or "\\" in normalized_id
            or normalized_id.endswith(".json")
        ):
            raise CustomModelPresetNotFoundError("预设不存在或已被删除。")
        return normalized_id

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def _get_preset_file_path(self, preset_id: str) -> str:
        normalized_id = self._normalize_preset_id(preset_id)
        return os.path.join(self.presets_dir, f"{normalized_id}.json")

    def _write_preset_file(self, preset: CustomModelPreset) -> None:
        os.makedirs(self.presets_dir, exist_ok=True)
        file_path = self._get_preset_file_path(preset.preset_id)
        with open(file_path, "w", encoding="utf-8") as fp:
            json.dump(preset.to_dict(), fp, ensure_ascii=False, indent=2)
            fp.write("\n")

    @staticmethod
    def _read_preset_file(file_path: str) -> CustomModelPreset:
        with open(file_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        if not isinstance(payload, dict):
            raise ValueError(f"预设文件格式错误: {file_path}")
        return CustomModelPreset.from_dict(payload)
