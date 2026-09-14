# -*- coding: utf-8 -*-
"""衣柜决策层：priority / weight 双维打分 + 三趟式装箱。

这是架构提案第五节里第 5 层（决策层）的实现，只做"预算怎么分"这一件事：
给定若干候选条目和它们各自的渲染长度，决定留下谁、丢掉谁、留下的按什么顺序排。
本模块是**纯函数**：不读配置、不写状态、不调模型、不 import 插件运行时，
同样的输入永远得到同样的输出（排序全部走稳定排序，没有随机、没有时间戳）。

两个维度（语义借鉴 Agnai 的记忆排序，出处与原文见
`wardrobe-research/11-对比-角色扮演前端侧.md`）
------------------------------------------------------------------
`priority` —— **预算不足时先丢谁**。数值越大越该保留：装箱时按它降序满足，
最后被丢的永远是数值最小的那些。

`weight` —— **最终文本里谁更靠下**。数值越大越靠后（越接近文本末尾），
所以第三趟按 weight **升序**输出，权重最高的那一项落在最下面。

两者刻意独立：一件低优先级的衣物可以因为"最后才提到"而排在文本末尾，
一件高优先级的衣物也可以出现在文本最前 —— "要不要"与"放哪里"不互相污染。

三趟式装箱（借鉴 RisuAI 世界书的预算分配，出处同上）
----------------------------------------------------
1. **排序**：按 priority 降序（同分保持输入顺序，靠稳定排序，不掷骰子）；
2. **贪心**：按上面的顺序逐条吃预算 —— 放得下就收，放不下就丢并记下原因；
3. **重排**：把收下的条目按 weight 升序重新排列后返回。

第三趟只改顺序、不改集合，所以"谁被留下"与"谁在前面"可以分别调参。
分组条目（例如按部位分组渲染、每组一个标题）给出 `group_of` 与
`group_cost` 时，标题开销在**该组第一条被收下时**计入一次；整组都没收下就不计。

返回结构保持可直接 JSON 序列化，便于面板预览与日志：

.. code-block:: python

    {
        "kept": [...],        # 已按 weight 升序重排（靠下的在后）
        "dropped": [
            {"entry": ..., "reason": "budget", "priority": 40, "weight": 20, "length": 18},
        ],
        "used": 862,          # 计入分组标题开销后的实际占用
        "budget": 900,
        "remaining": 38,
        "kept_count": 7,
        "dropped_count": 3,
        "truncated": True,
    }
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

DECISION_VERSION = 1

# 丢弃原因。渲染层据此区分"这件太大，塞不进任何预算"与"轮到它时预算已经用完了"，
# 前者是数据问题（该压缩描述或提高预算），后者只是排序结果。
DROP_REASON_BUDGET = "budget"
DROP_REASON_OVERSIZE = "oversize"
DROP_REASON_LIMIT = "limit"
DROP_REASONS = (DROP_REASON_OVERSIZE, DROP_REASON_LIMIT, DROP_REASON_BUDGET)

# score_priority 的固定权重。数量级刻意拉开，让它等价于**字典序**而不是加权平均：
#   分类 40 > 描述 20 + 冷却 10 + 贴身 5 = 35，所以带满附加项的未分类件
#   也压不过一件光秃秃的已分类件；
#   描述 20 > 冷却 10 + 贴身 5 = 15，所以有描述永远压过没描述。
PRIORITY_CLASSIFIED = 40
PRIORITY_DESCRIBED = 20
PRIORITY_FRESH = 10
PRIORITY_INTIMATE = 5

# weight 的档位间距：留出在两档之间插一档的空间（例如给"外套"单独一档）。
WEIGHT_STEP = 10

__all__ = [
    "DECISION_VERSION",
    "DROP_REASON_BUDGET",
    "DROP_REASON_LIMIT",
    "DROP_REASON_OVERSIZE",
    "DROP_REASONS",
    "PRIORITY_CLASSIFIED",
    "PRIORITY_DESCRIBED",
    "PRIORITY_FRESH",
    "PRIORITY_INTIMATE",
    "WEIGHT_STEP",
    "clean_length",
    "clean_score",
    "entry_field",
    "entry_priority",
    "entry_weight",
    "order_by_priority",
    "order_by_weight",
    "pack_entries",
    "score_priority",
    "weight_for_rank",
]


def clean_score(value: Any, default: int = 0) -> int:
    """Fold any input into an integer score, falling back to `default`.

    非法输入（None、字符串、NaN、正负无穷）一律落回 `default`：分数可能来自
    配置或模型，宁可当它是 0，也不能让一次 TypeError 打断整轮注入。
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return int(number)


def clean_length(value: Any, default: int = 0) -> int:
    """Fold any input into a non-negative length (a negative length would cheat the budget)."""

    return max(0, clean_score(value, default))


def entry_field(entry: Any, name: str, default: Any = None) -> Any:
    """Read a field off an entry: mappings by key, everything else by attribute."""

    if isinstance(entry, Mapping):
        return entry.get(name, default)
    return getattr(entry, name, default)


def entry_priority(entry: Any) -> int:
    """Default priority accessor: the entry's own `priority` field, else 0."""

    return clean_score(entry_field(entry, "priority"), 0)


def entry_weight(entry: Any) -> int:
    """Default weight accessor: the entry's own `weight` field, else 0."""

    return clean_score(entry_field(entry, "weight"), 0)


def score_priority(
    *,
    classified: bool = True,
    described: bool = True,
    fresh: bool = True,
    intimate: bool = False,
    explicit: Any = None,
) -> int:
    """Turn "which facts hold for this garment" into a priority score.

    四个事实按固定权重相加，权重刻意拉开到互不越级（见模块头的常量说明）：

    * 已分类 > 未分类 —— 有部位的衣物才排得进一套搭配；
    * 有描述 > 无描述 —— 带描述的条目注入后信息量更大；
    * 冷却外 > 冷却内 —— 刚穿过的先让位；
    * 贴身件最后丢 —— 贴身层缺席时整套搭配缺一层，少一件配件只是少个点缀。

    `explicit` 不为 None 时以它为准，忽略上面四项 —— 将来新增的维度或面板
    可以直接给分数，不必挤进这套细则。
    """

    if explicit is not None:
        return clean_score(explicit, 0)
    return (
        (PRIORITY_CLASSIFIED if classified else 0)
        + (PRIORITY_DESCRIBED if described else 0)
        + (PRIORITY_FRESH if fresh else 0)
        + (PRIORITY_INTIMATE if intimate else 0)
    )


def weight_for_rank(rank: Any, *, explicit: Any = None, step: int = WEIGHT_STEP) -> int:
    """Turn "which group is this" into a weight: higher rank renders further down.

    调用方只需要给出自己的分组顺序（例如 整身 → 上装 → 下装 → 鞋袜 → 配件），
    本模块不关心这些组叫什么名字。`step` 是档位间距，留出插档空间。
    """

    if explicit is not None:
        return clean_score(explicit, 0)
    span = clean_score(step, WEIGHT_STEP)
    if span <= 0:
        span = WEIGHT_STEP
    return clean_score(rank, 0) * span


def order_by_priority(
    entries: Iterable[Any],
    *,
    priority_of: Callable[[Any], Any] | None = None,
) -> list[Any]:
    """第一趟：priority 降序；同分保持输入顺序（`sorted` 是稳定排序）。"""

    accessor = priority_of or entry_priority
    return sorted(entries, key=lambda entry: -clean_score(accessor(entry), 0))


def order_by_weight(
    entries: Iterable[Any],
    *,
    weight_of: Callable[[Any], Any] | None = None,
) -> list[Any]:
    """第三趟：weight 升序（值越大越靠后）；同权重保持传入顺序。"""

    accessor = weight_of or entry_weight
    return sorted(entries, key=lambda entry: clean_score(accessor(entry), 0))


def _resolve_group_cost(group_cost: Any, group: str) -> int:
    if group_cost is None:
        return 0
    if isinstance(group_cost, Mapping):
        return clean_length(group_cost.get(group), 0)
    if callable(group_cost):
        return clean_length(group_cost(group), 0)
    return 0


def pack_entries(
    entries: Iterable[Any] | None,
    *,
    budget: Any,
    measure: Callable[[Any], Any],
    priority_of: Callable[[Any], Any] | None = None,
    weight_of: Callable[[Any], Any] | None = None,
    group_of: Callable[[Any], str] | None = None,
    group_cost: Any = None,
    max_entries: Any = 0,
) -> dict[str, Any]:
    """三趟式装箱：priority 降序 → 贪心吃预算 → weight 升序重排。

    `budget` 是这一批条目可用的总长度，`measure(entry)` 给出单个条目自己的
    渲染长度 —— 两者必须用同一把尺子（调用方怎么渲染，就怎么量）。
    `max_entries` > 0 时同时受条目数约束。

    `group_of` / `group_cost`：分组标题之类的一次性开销。标题在"该组第一条
    被收下"时计入一次（不管最终它排在组里第几位），整组都没收下就不计 ——
    调用方可以把标题算进预算，而不用担心给空标题留额度。

    丢因只有三种，都在 `dropped` 里带出来：

    * `oversize`：单条就超过总预算，空箱也放不下；
    * `limit`   ：轮到它时条目数已经用满；
    * `budget`  ：轮到它时长度已经用满。

    `measure` 抛出的异常不吞：长度算不出来是调用方的 bug，静默按 0 处理会让
    渲染结果悄悄超出预算，这类错误应该当场暴露。
    """

    rows = list(entries or ())
    clean_budget = clean_length(budget, 0)
    limit = clean_length(max_entries, 0)
    priority_accessor = priority_of or entry_priority
    weight_accessor = weight_of or entry_weight

    kept: list[Any] = []
    dropped: list[dict[str, Any]] = []
    used = 0
    charged_groups: set[str] = set()

    for entry in order_by_priority(rows, priority_of=priority_accessor):
        record = {
            "entry": entry,
            "priority": clean_score(priority_accessor(entry), 0),
            "weight": clean_score(weight_accessor(entry), 0),
            "length": clean_length(measure(entry), 0),
        }
        if limit and len(kept) >= limit:
            dropped.append({**record, "reason": DROP_REASON_LIMIT})
            continue
        if record["length"] > clean_budget:
            dropped.append({**record, "reason": DROP_REASON_OVERSIZE})
            continue
        # 空字符串是**合法**的分组名（例如"未分类"那一桶），所以"没有分组"
        # 只能用 None 表示 —— 否则未分类那一组的标题开销永远收不到，装箱算出来的
        # "放得下"会和实际渲染长度对不上。
        group: str | None = None
        extra = 0
        if group_of is not None:
            group = str(group_of(entry) or "")
            if group not in charged_groups:
                extra = _resolve_group_cost(group_cost, group)
        if used + record["length"] + extra > clean_budget:
            dropped.append({**record, "reason": DROP_REASON_BUDGET})
            continue
        used += record["length"] + extra
        if group is not None:
            charged_groups.add(group)
        kept.append(entry)

    ordered = order_by_weight(kept, weight_of=weight_accessor)
    return {
        "kept": ordered,
        "dropped": dropped,
        "used": used,
        "budget": clean_budget,
        "remaining": clean_budget - used,
        "kept_count": len(ordered),
        "dropped_count": len(dropped),
        "truncated": bool(dropped),
    }
