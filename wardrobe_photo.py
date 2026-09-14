# -*- coding: utf-8 -*-
"""生图通道桥接：把衣柜的裁决结果交给作者的每日穿搭照片链路。

作者侧原本用 _daily_outfit_candidate_profiles() 的硬编码候选（5 场景 × 6 套），
与我们衣柜里的真实衣物无关。本模块给出"今天穿什么"的权威答案，
由 proactive_message._select_daily_outfit_profile 优先采用；取不到就返回 {}，
作者原有逻辑照旧跑。

设计约束：

1. **只读**：不改配置、不调模型、不写缓存；
2. **失败即回落**：任何异常都返回 {}，照片链路不能因为我们挂掉而中断；
3. **可关**：wardrobe_photo_source = wardrobe（默认）| builtin（关闭接管）。
"""

from __future__ import annotations

from typing import Any, Mapping

from .wardrobe import select_wardrobe_outfit

WARDROBE_PHOTO_SOURCE_WARDROBE = "wardrobe"
WARDROBE_PHOTO_SOURCE_BUILTIN = "builtin"

# 与 OUTFIT_PHOTO_FIELDS 对齐；footwear 是我们新增的字段，
# 作者侧的白名单也要同步（见 proactive_message._normalize_daily_outfit_profile）。
PHOTO_PROFILE_FIELDS = (
    "palette",
    "silhouette",
    "top",
    "outer",
    "bottom",
    "footwear",
    "accessory",
)
PHOTO_PROFILE_LIMITS: dict[str, int] = {
    "palette": 120,
    "silhouette": 120,
    "top": 160,
    "outer": 160,
    "bottom": 140,
    "footwear": 140,
    "accessory": 140,
}


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _call(host: Any, name: str, default: Any = None) -> Any:
    getter = getattr(host, name, None)
    if not callable(getter):
        return default
    try:
        return getter()
    except Exception:
        return default


def wardrobe_photo_source(host: Any) -> str:
    """Return wardrobe (接管) or builtin (沿用作者候选表)."""

    getter = getattr(host, "_wardrobe_setting", None)
    raw = ""
    if callable(getter):
        try:
            raw = str(getter("wardrobe_photo_source", WARDROBE_PHOTO_SOURCE_WARDROBE) or "")
        except Exception:
            raw = ""
    return WARDROBE_PHOTO_SOURCE_BUILTIN if raw.strip().casefold() == WARDROBE_PHOTO_SOURCE_BUILTIN else WARDROBE_PHOTO_SOURCE_WARDROBE


def resolve_daily_outfit_profile(host: Any, *, date_key: str = "") -> dict[str, str]:
    """Today's photo outfit profile from the wardrobe; {} means "let the author decide"."""

    try:
        if wardrobe_photo_source(host) != WARDROBE_PHOTO_SOURCE_WARDROBE:
            return {}
        items = _call(host, "_wardrobe_owned_items", []) or []
        outfits = _call(host, "_wardrobe_owned_outfits", []) or []
        if not items and not outfits:
            return {}
        scene = _text(_call(host, "_wardrobe_current_scene", ""), 32)
        seed = _text(date_key, 60) or _text(_call(host, "_wardrobe_outfit_seed", ""), 60)
        rotation = _call(host, "_wardrobe_outfit_rotation_days", 7)
        try:
            rotation_days = int(rotation)
        except (TypeError, ValueError):
            rotation_days = 7
        selection = select_wardrobe_outfit(
            items, outfits, scene=scene, seed=seed, rotation_days=rotation_days
        )
    except Exception:
        return {}

    profile = selection.get("profile") if isinstance(selection, Mapping) else None
    if not isinstance(profile, Mapping) or not profile:
        return {}

    result: dict[str, str] = {}
    for key in PHOTO_PROFILE_FIELDS:
        value = _text(profile.get(key), PHOTO_PROFILE_LIMITS[key])
        if value:
            result[key] = value
    look_id = _text(selection.get("look_id"), 80)
    if look_id:
        result["look_id"] = look_id
    if scene:
        result["scene"] = scene
    weather = _text(_call(host, "_wardrobe_current_weather", ""), 32)
    if weather:
        result["weather"] = weather
    return result


__all__ = [
    "PHOTO_PROFILE_FIELDS",
    "PHOTO_PROFILE_LIMITS",
    "WARDROBE_PHOTO_SOURCE_BUILTIN",
    "WARDROBE_PHOTO_SOURCE_WARDROBE",
    "resolve_daily_outfit_profile",
    "wardrobe_photo_source",
]
