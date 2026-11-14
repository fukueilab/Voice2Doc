"""保存済みプロンプトのバックアップと復元

バックアップは「ダウンロード」フォルダへ JSON で書き出す。復元は「追加＋同名は上書き」(merge) と
「全て置き換え」(replace) を選べる。削除できない初期プロンプトは、置き換えでも消さずに残す。"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Optional

from . import prompt_sets

BACKUP_APP = "Voice2Doc"
BACKUP_KIND = "prompts"
FORMAT_VERSION = 1
MAX_BACKUP_BYTES = 5 * 1024 * 1024
MAX_NAME_LENGTH = 200
RESTORE_MODES = ("merge", "replace")


class PromptBackupError(ValueError):
    """バックアップファイルが不正なときの例外。key は i18n のメッセージキー"""

    def __init__(self, key: str, **values):
        super().__init__(key)
        self.key = key
        self.values = values


def normalize_prompt(value) -> dict:
    """保存済みプロンプト1件を現在の形式（dict）にそろえる。本文だけの文字列も受け付ける"""
    if isinstance(value, str):
        value = {"content": value}
    return {
        "content": value.get("content", ""),
        "description": value.get("description", "") or "",
        "output_dir": value.get("output_dir", "") or "",
        "include_transcript": bool(value.get("include_transcript", False)),
        "hidden": bool(value.get("hidden", False)),
    }


def backup_filename(now: Optional[datetime] = None) -> str:
    """保存名の stem（拡張子なし）: Voice2Doc-prompts-YYYYMMDD-HHMMSS"""
    return f"{BACKUP_APP}-prompts-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"


def build_backup(saved_prompts: Optional[dict], app_version: str, now: Optional[datetime] = None) -> bytes:
    """保存済みプロンプトすべてをバックアップ用の JSON（UTF-8）にする"""
    data = {
        "app": BACKUP_APP,
        "kind": BACKUP_KIND,
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "exported_at": (now or datetime.now()).astimezone().isoformat(timespec="seconds"),
        "prompts": {name: normalize_prompt(value) for name, value in (saved_prompts or {}).items()},
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def parse_backup(raw: bytes | str) -> dict:
    """バックアップファイルの内容を検証し、{名前: プロンプト} を返す。不正なら PromptBackupError"""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_BACKUP_BYTES:
        raise PromptBackupError("backup.too_large", size=MAX_BACKUP_BYTES // (1024 * 1024))
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PromptBackupError("backup.invalid_json")
    if not isinstance(data, dict) or data.get("kind") != BACKUP_KIND or not isinstance(data.get("prompts"), dict):
        raise PromptBackupError("backup.not_a_backup")
    prompts = {}
    for name, value in data["prompts"].items():
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH:
            raise PromptBackupError("backup.invalid_name")
        if isinstance(value, dict):
            texts_ok = all(isinstance(value.get(key, ""), str) for key in ("content", "description", "output_dir"))
            # "false" のような文字列を真と読まないよう、はい/いいえの項目は真偽値だけを受け付ける
            flags_ok = all(isinstance(value.get(key, False), bool) for key in ("include_transcript", "hidden"))
            if not (texts_ok and flags_ok):
                raise PromptBackupError("backup.invalid_prompt", name=name)
        elif not isinstance(value, str):
            raise PromptBackupError("backup.invalid_prompt", name=name)
        prompts[name.strip()] = normalize_prompt(value)
    if not prompts:
        raise PromptBackupError("backup.empty")
    return prompts


def preview(current: Optional[dict], prompts: dict) -> dict:
    """復元の確認ダイアログ用: 件数、上書きされる名前、置き換えでも残る初期プロンプト"""
    current = current or {}
    builtin = set(prompt_sets.builtin_names())
    return {
        "count": len(prompts),
        "names": list(prompts),
        "conflicts": [name for name in prompts if name in current],
        "builtin_kept": [name for name in current if name in builtin and name not in prompts],
    }


def apply_restore(config, prompts: dict, mode: str) -> None:
    """復元を設定に反映する（保存は呼び出し側）。アクティブなプロンプトが消えたら既定に戻す"""
    if mode not in RESTORE_MODES:
        raise PromptBackupError("backup.invalid_mode", mode=mode)
    current = dict(config.saved_prompts or {})
    if mode == "replace":
        builtin = set(prompt_sets.builtin_names())
        # 初期プロンプトは削除できないので残す
        current = {name: value for name, value in current.items() if name in builtin}
    for name, value in prompts.items():
        current[name] = copy.deepcopy(value)
    config.saved_prompts = current

    active = current.get(config.active_prompt_name)
    if active is None:
        config.active_prompt_name = prompt_sets.default_active_prompt_name(config.ui_language)
        if config.active_prompt_name not in current and current:
            config.active_prompt_name = next(iter(current))
        active = current.get(config.active_prompt_name)
    if active is not None:
        config.prompt = active.get("content", "") if isinstance(active, dict) else active
