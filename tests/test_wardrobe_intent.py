# -*- coding: utf-8 -*-
"""本会话明确换装（作者的 dialogue_outfit_override）如何接管衣柜段落。

P0：只读接管 —— 有换装意图时不再把轮换裁决出的那一套标成「当前着装」。
"""

from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.wardrobe import (
    WARDROBE_PROMPT_MAX_CHARS,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
)
from astrbot_plugin_private_companion.wardrobe_runtime import WardrobeMixin

USER = {"user_id": "u-owner"}


class _IntentHarness(WardrobeMixin):
    def __init__(self, *, override=None, items=None, outfits=None, mode="select", detail="full"):
        self.config = {
            "enable_wardrobe": True,

            "enable_wardrobe_prompt": True,
            "wardrobe_tendency": "偏爱宽松针织",
            "wardrobe_items": list(items or []),
            "wardrobe_outfits": list(outfits or []),
            "wardrobe_outfit_mode": mode,
            "wardrobe_injection_detail": detail,
            "wardrobe_outfit_rotation_days": 7,
        }
        self.override = dict(override or {})

    def persona_setting(self, key: str, default: object = None) -> object:
        return self.config.get(key, default)

    async def _save_config_if_possible(self) -> bool:
        return True

    def _current_dialogue_outfit_override(self, *, user_id: str = "", now=None):
        """模拟作者实现：不是发起换装的那个人就什么都看不到。"""

        snapshot = dict(self.override)
        if not snapshot:
            return {}
        if user_id and snapshot.get("source_user_id") != user_id:
            return {}
        return snapshot


ITEMS = normalize_wardrobe_items(
    [
        {"id": "i-tee", "name": "白色纯棉T恤", "description": "基础圆领", "slot": "upper"},
        {"id": "i-jeans", "name": "深蓝直筒牛仔裤", "description": "经典款", "slot": "lower"},
        {"id": "i-swim", "name": "分体泳衣上装", "description": "运动款速干", "slot": "upper"},
    ]
)


def _section(**kwargs):
    plugin = _IntentHarness(items=ITEMS, **kwargs)
    return plugin, plugin._wardrobe_prompt_section(USER, "")


class WardrobeIntentTakeoverTests(unittest.TestCase):
    def test_override_with_items_replaces_the_rotating_outfit(self) -> None:
        _plugin, section = _section(
            override={
                "instruction": "换上泳衣",
                "source_user_id": "u-owner",
                "wardrobe_items": ["i-swim"],
            }
        )
        assert section is not None
        content = str(section.content)
        self.assertIn("分体泳衣上装", content)
        self.assertIn("换上泳衣", content)
        # 关键：不能再出现「当前着装：」——那个标题意味着这是我们裁决出来的那一套。
        self.assertNotIn("当前着装：", content)
        self.assertIn("以这次换装为准", content)

    def test_override_outfit_id_resolves_to_its_items(self) -> None:
        outfits = normalize_wardrobe_outfits(
            [{"id": "o-trip", "name": "出差三件套", "kind": "bundle", "items": ["i-tee", "i-jeans"]}]
        )
        plugin = _IntentHarness(
            items=ITEMS,
            outfits=outfits,
            override={
                "instruction": "换上出差那套",
                "source_user_id": "u-owner",
                "wardrobe_outfit_id": "o-trip",
            },
        )
        section = plugin._wardrobe_prompt_section(USER, "")
        assert section is not None
        content = str(section.content)
        self.assertIn("白色纯棉T恤", content)
        self.assertIn("深蓝直筒牛仔裤", content)
        self.assertNotIn("分体泳衣上装", content)

    def test_override_without_items_still_takes_over_and_says_so(self) -> None:
        _plugin, section = _section(
            override={"instruction": "换上 JK 制服", "source_user_id": "u-owner"}
        )
        assert section is not None
        content = str(section.content)
        self.assertIn("换上 JK 制服", content)
        self.assertIn("衣柜清单里没有完全对应的衣物", content)
        self.assertNotIn("当前着装：", content)

    def test_override_wins_over_the_progressive_minimal_body(self) -> None:
        _plugin, section = _section(
            detail="progressive",
            override={"instruction": "换上泳衣", "source_user_id": "u-owner"},
        )
        assert section is not None
        content = str(section.content)
        self.assertIn("换上泳衣", content)
        self.assertNotIn("需要细节时再展开", content)

    def test_override_stays_within_the_character_budget(self) -> None:
        _plugin, section = _section(
            override={
                "instruction": "换上" + "很长的换装描述" * 20,
                "source_user_id": "u-owner",
                "wardrobe_items": ["i-swim", "i-tee", "i-jeans"],
            }
        )
        assert section is not None
        self.assertLessEqual(len(str(section.content)), WARDROBE_PROMPT_MAX_CHARS)


class WardrobeIntentFallbackTests(unittest.TestCase):
    """没有意图时必须与加这个功能之前逐字一致。"""

    def test_without_override_the_rotating_outfit_is_still_injected(self) -> None:
        _plugin, section = _section()
        assert section is not None
        content = str(section.content)
        self.assertIn("当前着装：", content)
        self.assertNotIn("以这次换装为准", content)

    def test_override_from_another_user_is_ignored(self) -> None:
        _plugin, section = _section(
            override={"instruction": "换上泳衣", "source_user_id": "u-someone-else"}
        )
        assert section is not None
        content = str(section.content)
        self.assertNotIn("换上泳衣", content)
        self.assertIn("当前着装：", content)

    def test_override_is_ignored_without_a_user_identity(self) -> None:
        # 群聊（user=None）不带某个人的换装：作者的连续性段落本身也只在私聊注入。
        plugin = _IntentHarness(
            items=ITEMS,
            override={"instruction": "换上泳衣", "source_user_id": "u-owner"},
        )
        section = plugin._wardrobe_prompt_section(None, "")
        assert section is not None
        self.assertNotIn("换上泳衣", str(section.content))

    def test_missing_host_accessor_degrades_to_normal_behaviour(self) -> None:
        class _NoOverrideAccessor(_IntentHarness):
            def _current_dialogue_outfit_override(self, *, user_id: str = "", now=None):
                raise RuntimeError("host changed")

        plugin = _NoOverrideAccessor(
            items=ITEMS,
            override={"instruction": "换上泳衣", "source_user_id": "u-owner"},
        )
        section = plugin._wardrobe_prompt_section(USER, "")
        assert section is not None
        self.assertIn("当前着装：", str(section.content))

    def test_inventory_mode_is_unaffected_without_override(self) -> None:
        _plugin, section = _section(mode="inventory")
        assert section is not None
        self.assertIn("衣柜里的具体衣物：", str(section.content))


if __name__ == "__main__":
    unittest.main()
