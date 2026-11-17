# -*- coding: utf-8 -*-
"""作息时间预设。

以前这份表直接写死在西工大上，现在它跟着「版式」走：每套版式自带
`period_presets`（见 layout.py），换学校就换到那所学校的作息，界面上的下拉框也会
跟着换。本模块只保留两件事：

  1. 兼容层：`PRESETS` / `CHANGAN` / `DEFAULT_PRESET` 仍是西工大的作息，
     老脚本（selftest.py、tools/*.py）照旧能用；
  2. 取表函数：`presets_for(layout)` / `get_preset(name, layout)` 才是新代码该用的。

数据来源：西北工业大学官方《课程节次时间表》
  * 长安校区：1 节 08:30-09:15 … 13 节 20:40-21:25
  * 友谊校区：分冬季（10.1-4.30）与夏季（5.1-9.30），每节 50 分钟，只有 1~12 节
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .layout import BUILTIN, DEFAULT_LAYOUT_ID, Layout, REGISTRY

_NWPU = BUILTIN[DEFAULT_LAYOUT_ID]

CHANGAN = _NWPU.preset_times("西北工业大学 · 长安校区")
YOUYI_WINTER = _NWPU.preset_times("西北工业大学 · 友谊校区（冬季 10.1-4.30）")
YOUYI_SUMMER = _NWPU.preset_times("西北工业大学 · 友谊校区（夏季 5.1-9.30）")

PRESETS: Dict[str, Dict[int, Tuple[str, str]]] = _NWPU.all_period_presets()
DEFAULT_PRESET = _NWPU.default_preset


def layout_of(layout) -> Layout:
    """允许调用方传版式对象、版式 id，或者干脆不传（= 西工大）。"""
    if layout is None:
        return _NWPU
    if isinstance(layout, Layout):
        return layout
    return REGISTRY.get(str(layout))


def presets_for(layout=None) -> Dict[str, Dict[int, Tuple[str, str]]]:
    return layout_of(layout).all_period_presets()


def preset_names(layout=None) -> list:
    """这套版式自带哪几份作息。**没有就是空列表** —— 绝不能拿西工大的表去顶，
    否则换了学校还显示「西北工业大学 · 长安校区」，用户以为有作息其实是错的。"""
    return list(presets_for(layout))


def default_preset_name(layout=None) -> str:
    lay = layout_of(layout)
    names = list(lay.period_presets)
    if lay.default_preset in lay.period_presets:
        return lay.default_preset
    return names[0] if names else ""


def get_preset(name: Optional[str] = None, layout=None) -> Dict[int, Tuple[str, str]]:
    """取一套作息。名字找不到就用该版式的默认那套（返回副本，调用方随便改）。"""
    lay = layout_of(layout)
    allp = lay.all_period_presets()
    if not allp:
        return {}
    if name and name in allp:
        return dict(allp[name])
    fallback = lay.default_preset if lay.default_preset in allp else next(iter(allp))
    return dict(allp[fallback])


def find_preset_containing(period_times: Dict[int, Tuple[str, str]],
                           layout=None) -> str:
    """给界面用：当前节次时间还和哪套预设完全一致（一致就显示预设名，否则显示自定义）。"""
    target = {int(k): tuple(v) for k, v in (period_times or {}).items()}
    for name, times in presets_for(layout).items():
        if times == target:
            return name
    return ""


def describe(preset, max_items=None) -> str:
    """给界面用的一行说明（节次多了要截断，否则一行撑爆卡片）。"""
    if not preset:
        return "（未设置）"
    keys = sorted(preset)
    if not keys:
        return "（未设置）"
    if max_items is not None and len(keys) > max_items:
        keys = keys[:max_items]
    return "  ".join("%d节 %s-%s" % (k, preset[k][0], preset[k][1]) for k in keys)
