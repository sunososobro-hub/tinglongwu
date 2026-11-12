r"""Per-user config / log locations and the tiny KEY=VALUE config file shared with the menus (stdlib only).

config : Linux ${XDG_CONFIG_HOME:-~/.config}/tinglongwu/config
         macOS ~/Library/Application Support/tinglongwu/config
         Windows %APPDATA%\tinglongwu\config
log    : Linux ${XDG_STATE_HOME:-~/.local/state}/tinglongwu/ ; macOS ~/Library/Logs/tinglongwu/ ;
         Windows %LOCALAPPDATA%\tinglongwu\
Env TLWU_CONFIG_DIR / TLWU_LOG_DIR override (tests). Config keys: auto_update=0|1, ui_lang=en|ru|zh-TW.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def config_dir() -> Path:
    if os.environ.get("TLWU_CONFIG_DIR"):
        return Path(os.environ["TLWU_CONFIG_DIR"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or _home() / "AppData" / "Roaming") / "tinglongwu"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "tinglongwu"
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config") / "tinglongwu"


def log_dir() -> Path:
    if os.environ.get("TLWU_LOG_DIR"):
        return Path(os.environ["TLWU_LOG_DIR"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or _home() / "AppData" / "Local") / "tinglongwu"
    if sys.platform == "darwin":
        return _home() / "Library" / "Logs" / "tinglongwu"
    return Path(os.environ.get("XDG_STATE_HOME") or _home() / ".local" / "state") / "tinglongwu"


def config_path() -> Path:
    return config_dir() / "config"


def read_config() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in config_path().read_text("utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def write_config_value(key: str, value: str) -> None:
    cfg = read_config()
    cfg[key] = value
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{k}={v}\n" for k, v in cfg.items()), "utf-8")
