"""配置事务与 JSON 原子写入。网络请求不能放在配置锁中。"""
import json
import os
import tempfile
import time
from decimal import Decimal, InvalidOperation

from filelock import FileLock

BASE = os.path.dirname(os.path.abspath(__file__))
LIST_FILE = os.path.join(BASE, "追番列表.json")
LIST_LOCK = os.path.join(BASE, ".list.lock")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def write_json(path, value):
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(4):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def validate_series(item):
    if not isinstance(item, dict):
        raise ValueError("追番项必须是对象")
    if type(item.get("mikan_id")) is not int or item["mikan_id"] <= 0:
        raise ValueError("mikan_id 必须是正整数")
    if not isinstance(item.get("name"), str) or not item["name"].strip():
        raise ValueError("番名不能为空")
    groups = item.get("subtitle_groups", [])
    if not isinstance(groups, list) or any(not isinstance(g, str) or not g.strip() for g in groups):
        raise ValueError("字幕组必须是字符串列表")
    if item.get("language", "简体") not in ("简体", "繁体", "简繁", "不限"):
        raise ValueError("不支持的语言")
    if type(item.get("embedded_only", True)) is not bool:
        raise ValueError("embedded_only 必须是布尔值")
    path = item.get("save_path", "")
    if not isinstance(path, str) or not path.startswith("/") or any(p == ".." for p in path.split("/")):
        raise ValueError("网盘路径必须以 / 开头，且不能含 ..")
    try:
        ep = Decimal(str(item.get("last_episode", "0")))
        if not ep.is_finite() or ep < 0:
            raise InvalidOperation
    except InvalidOperation:
        raise ValueError("已处理话数必须是非负数字") from None
    for key in ("notes", "cover_url"):
        if item.get(key) is not None and not isinstance(item[key], str):
            raise ValueError(f"{key} 必须是字符串")


def load_list():
    cfg = read_json(LIST_FILE, {"series": []})
    if not isinstance(cfg, dict) or not isinstance(cfg.get("series"), list):
        raise ValueError("追番列表格式错误")
    ids = set()
    for item in cfg["series"]:
        validate_series(item)
        if item["mikan_id"] in ids:
            raise ValueError("追番列表包含重复 mikan_id")
        ids.add(item["mikan_id"])
    return cfg


def mutate_list(callback):
    """读取、修改、保存使用同一把锁，避免并发写覆盖。"""
    with FileLock(LIST_LOCK, timeout=15):
        cfg = load_list()
        result = callback(cfg)
        write_json(LIST_FILE, cfg)
        return result
