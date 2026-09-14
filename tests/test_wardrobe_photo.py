# -*- coding: utf-8 -*-
"""生图通道桥接（wardrobe_photo）单元测试：只读、可关、失败即回落。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.wardrobe_photo import (
    PHOTO_PROFILE_FIELDS,
    WARDROBE_PHOTO_SOURCE_BUILTIN,
    resolve_daily_outfit_profile,
    wardrobe_photo_source,
)


class _Host:
    """最小替身：只实现桥接会用到的那几个读取口。"""

    def __init__(self, **overrides):
        self.setting = overrides.pop("setting", {})
        self.items = overrides.pop("items", [
            {"id": "w_top", "name": "米色针织开衫", "description": "宽松细针织", "slot": "upper"},
            {"id": "w_bottom", "name": "深色直筒长裤", "slot": "lower"},
            {"id": "w_feet", "name": "白色帆布鞋", "slot": "feet"},
            {"id": "w_extra", "name": "细框眼镜", "slot": "extra"},
        ])
        self.outfits = overrides.pop("outfits", [])
        self.raise_on = overrides.pop("raise_on", "")

    def _wardrobe_setting(self, key, default=None):
        return self.setting.get(key, default)

    def _guard(self, name):
        if self.raise_on == name:
            raise RuntimeError("boom")

    def _wardrobe_owned_items(self):
        self._guard("items"); return self.items

    def _wardrobe_owned_outfits(self):
        self._guard("outfits"); return self.outfits

    def _wardrobe_current_scene(self):
        self._guard("scene"); return "home"

    def _wardrobe_outfit_seed(self):
        self._guard("seed"); return "2026-09-14|persona-a"

    def _wardrobe_outfit_rotation_days(self):
        return 7

    def _wardrobe_current_weather(self):
        return "冷"


class WardrobePhotoBridgeTests(unittest.TestCase):
    def test_default_source_is_wardrobe(self) -> None:
        self.assertEqual("wardrobe", wardrobe_photo_source(_Host()))
        self.assertEqual(WARDROBE_PHOTO_SOURCE_BUILTIN,
                         wardrobe_photo_source(_Host(setting={"wardrobe_photo_source": "builtin"})))

    def test_profile_carries_the_fields_the_author_was_dropping(self) -> None:
        profile = resolve_daily_outfit_profile(_Host())
        self.assertTrue(profile)
        for key in profile:
            self.assertIn(key, (*PHOTO_PROFILE_FIELDS, "look_id", "scene", "weather"), key)
        # footwear 是作者白名单里缺的那一项，必须真的产出
        self.assertIn("footwear", profile)
        self.assertIn("top", profile)
        self.assertEqual("home", profile["scene"])
        self.assertEqual("冷", profile["weather"])
        self.assertTrue(profile["look_id"])

    def test_builtin_switch_disables_takeover(self) -> None:
        host = _Host(setting={"wardrobe_photo_source": "builtin"})
        self.assertEqual({}, resolve_daily_outfit_profile(host))

    def test_empty_wardrobe_falls_back(self) -> None:
        self.assertEqual({}, resolve_daily_outfit_profile(_Host(items=[], outfits=[])))

    def test_any_failure_degrades_instead_of_raising(self) -> None:
        # 读取口挂掉时要么降级（还有别的数据可用），要么整条回落，
        # 但**绝不能抛异常**把照片链路带崩。
        self.assertEqual({}, resolve_daily_outfit_profile(_Host(raise_on="items")))
        for name in ("outfits", "scene", "seed"):
            profile = resolve_daily_outfit_profile(_Host(raise_on=name))
            self.assertTrue(profile, name)
            self.assertIn("top", profile, name)

    def test_missing_rotation_days_does_not_break(self) -> None:
        host = _Host()
        host._wardrobe_outfit_rotation_days = lambda: "不是数字"
        self.assertTrue(resolve_daily_outfit_profile(host))

    def test_same_seed_is_stable_and_a_new_day_can_differ(self) -> None:
        first = resolve_daily_outfit_profile(_Host(), date_key="2026-09-14|persona-a")
        second = resolve_daily_outfit_profile(_Host(), date_key="2026-09-14|persona-a")
        self.assertEqual(first, second)

    def test_bundle_outfit_wins_and_keeps_its_own_items(self) -> None:
        host = _Host()
        host.outfits = [{
            "id": "o1", "name": "通勤正装", "kind": "bundle", "ownership": "owned",
            "items": ["w_top", "w_bottom"],
        }]
        profile = resolve_daily_outfit_profile(host)
        self.assertIn("米色针织开衫", profile.get("top", ""))
        self.assertIn("深色直筒长裤", profile.get("bottom", ""))


class AuthorWhitelistWiringTests(unittest.TestCase):
    """作者侧三处白名单必须含 footwear，否则我们的字段会被静默丢弃。"""

    def test_author_files_mention_footwear(self) -> None:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        text = (root / "proactive_message.py").read_text(encoding="utf-8")
        self.assertIn('"footwear": 140,', text)          # _normalize_daily_outfit_profile.limits
        self.assertIn('("footwear", "footwear"),', text)  # _daily_outfit_outfit_hint.fields
        self.assertIn('"footwear": 9,', text)             # cooldown_score 权重
        self.assertIn("resolve_wardrobe_daily_outfit_profile", text)  # 接管钩子


if __name__ == "__main__":
    unittest.main()
