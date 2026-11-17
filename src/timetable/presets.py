# -*- coding: utf-8 -*-
"""作息时间（每节课几点上、几点下）。

数据全部跟着「版式」走（见 layout.py 的 `period_presets`）：内置的通用版式带一份
45 分钟/节的通用作息，界面上的下拉框列的就是当前版式自带的那几套，用户也可以
手改（改完若与某套预设一致，下拉框会自动跳回那套的名字）。

本模块只做取值与查询，**不含任何具体学校的作息表** —— 学校专属作息属于版式配置
（内置的或用户自己写的 JSON），不写死在代码里。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .layout import BUILTIN, DEFAULT_LAYOUT_ID, Layout, REGISTRY

_DEFAULT_LAYOUT = BUILTIN[DEFAULT_LAYOUT_ID]


def layout_of(layout) -> Layout:
    """允许调用方传版式对象、版式 id，或者干脆不传（= 默认的通用版式）。"""
    if layout is None:
        return _DEFAULT_LAYOUT
    if isinstance(layout, Layout):
        return layout
    return REGISTRY.get(str(layout))


def presets_for(layout=None) -> Dict[str, Dict[int, Tuple[str, str]]]:
    return layout_of(layout).all_period_presets()


def preset_names(layout=None) -> list:
    """这套版式自带哪几份作息。**没有就是空列表** —— 绝不能拿别的版式的表去顶，
    否则换了学校还显示上一所学校的节次时间，用户以为有作息其实是错的。"""
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
