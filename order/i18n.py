"""中文消息分离层：从 i18n.json 加载，代码通过 t(key) 查询。"""
import json
from pathlib import Path

_PATH = Path(__file__).parent / "i18n.json"
_messages = None


def _load():
    global _messages
    if _messages is None:
        with open(_PATH, "r", encoding="utf-8") as f:
            _messages = json.load(f)
    return _messages


def t(key: str, **kwargs) -> str:
    """查询消息，支持点分隔键和 {var} 占位。"""
    data = _load()
    parts = key.split(".")
    val = data
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            val = None
        if val is None:
            return key  # fallback
    if kwargs:
        val = val.format(**kwargs)
    return val
