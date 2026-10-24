# -*- coding: utf-8 -*-
"""
键位配置文件管理
支持保存/加载不同游戏的键位映射配置（JSON 格式）
"""
import json
import os
from typing import Dict, Tuple, Any

import config


# 默认 PUBG Mobile 键位配置
DEFAULT_PROFILE = {
    "name": "PUBG Mobile 默认",
    "game": "PUBG Mobile",
    "joystick": {
        "center_x": 300,
        "center_y": 1800,
        "radius": 150,
        "keys": {"up": "w", "down": "s", "left": "a", "right": "d"},
    },
    "look": {
        "center_x": 800,
        "center_y": 1200,
        "sensitivity": 1.5,
    },
    "fire": {"key": "mouse_left", "pos": [960, 1600]},
    "scope": {"key": "mouse_right", "pos": [960, 1000]},
    "keymap": {
        "space": {"pos": [960, 2000], "label": "跳跃"},
        "c": {"pos": [150, 2000], "label": "下蹲"},
        "z": {"pos": [300, 2100], "label": "趴下"},
        "r": {"pos": [960, 1800], "label": "换弹"},
        "f": {"pos": [540, 1200], "label": "互动"},
        "g": {"pos": [960, 1400], "label": "切武器"},
        "m": {"pos": [100, 100], "label": "地图"},
        "tab": {"pos": [1000, 100], "label": "背包"},
        "t": {"pos": [100, 2000], "label": "语音"},
    },
    "auto_click": {
        "enabled": False,
        "button": "left",
        "interval": 0.05,
        "hold_time": 0.02,
    },
    "recoil": {
        "enabled": False,
        "strength": 3.0,
        "interval": 0.03,
    },
    "macros": {},
    "macro_keys": {},
}


def list_profiles() -> list:
    """列出所有已保存的配置文件（不含自动保存的 _autosave）"""
    config.ensure_dirs()
    profiles = []
    for f in os.listdir(config.PROFILES_DIR):
        if f.endswith(".json"):
            name = f[:-5]
            if name != "_autosave":
                profiles.append(name)
    return profiles


AUTOSAVE_NAME = "_autosave"


def load_autosave() -> Dict[str, Any]:
    """加载自动保存的配置，不存在则返回 None"""
    path = os.path.join(config.PROFILES_DIR, f"{AUTOSAVE_NAME}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_autosave(data: Dict[str, Any]):
    """自动保存配置"""
    config.ensure_dirs()
    data["name"] = AUTOSAVE_NAME
    path = os.path.join(config.PROFILES_DIR, f"{AUTOSAVE_NAME}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_profile(name: str) -> Dict[str, Any]:
    """加载指定名称的配置"""
    path = os.path.join(config.PROFILES_DIR, f"{name}.json")
    if not os.path.exists(path):
        return dict(DEFAULT_PROFILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_PROFILE)


def save_profile(name: str, data: Dict[str, Any]):
    """保存配置"""
    config.ensure_dirs()
    data["name"] = name
    path = os.path.join(config.PROFILES_DIR, f"{name}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def delete_profile(name: str) -> bool:
    """删除配置"""
    path = os.path.join(config.PROFILES_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def apply_profile(mapper, profile: Dict[str, Any]):
    """将配置应用到 InputMapper"""
    # 摇杆
    js = profile.get("joystick", {})
    config.JOYSTICK_CENTER_X = js.get("center_x", config.JOYSTICK_CENTER_X)
    config.JOYSTICK_CENTER_Y = js.get("center_y", config.JOYSTICK_CENTER_Y)
    config.JOYSTICK_RADIUS = js.get("radius", config.JOYSTICK_RADIUS)

    # 视角
    lk = profile.get("look", {})
    config.LOOK_CENTER_X = lk.get("center_x", config.LOOK_CENTER_X)
    config.LOOK_CENTER_Y = lk.get("center_y", config.LOOK_CENTER_Y)
    config.MOUSE_SENSITIVITY = lk.get("sensitivity", config.MOUSE_SENSITIVITY)

    # 开火/开镜
    fire = profile.get("fire", {})
    config.FIRE_KEY = fire.get("key", config.FIRE_KEY)
    config.FIRE_POS = tuple(fire.get("pos", config.FIRE_POS))

    scope = profile.get("scope", {})
    config.SCOPE_KEY = scope.get("key", config.SCOPE_KEY)
    config.SCOPE_POS = tuple(scope.get("pos", config.SCOPE_POS))

    # 重建按键映射
    mapper.key_map.clear()
    for key, info in profile.get("keymap", {}).items():
        pos = info.get("pos")
        if pos and len(pos) == 2:
            mapper.key_map[key.lower()] = (int(pos[0]), int(pos[1]))

    # 连点器
    ac = profile.get("auto_click", {})
    config.AUTO_CLICK_ENABLED = ac.get("enabled", config.AUTO_CLICK_ENABLED)
    config.AUTO_CLICK_BUTTON = ac.get("button", config.AUTO_CLICK_BUTTON)
    config.AUTO_CLICK_INTERVAL = ac.get("interval", config.AUTO_CLICK_INTERVAL)
    config.AUTO_CLICK_HOLD_TIME = ac.get("hold_time", config.AUTO_CLICK_HOLD_TIME)

    # 压枪
    rc = profile.get("recoil", {})
    config.RECOIL_CONTROL_ENABLED = rc.get("enabled", config.RECOIL_CONTROL_ENABLED)
    config.RECOIL_STRENGTH = rc.get("strength", config.RECOIL_STRENGTH)
    config.RECOIL_INTERVAL = rc.get("interval", config.RECOIL_INTERVAL)

    # 宏
    mapper.macros = dict(profile.get("macros", {}))
    mapper.macro_keys = dict(profile.get("macro_keys", {}))
