# -*- coding: utf-8 -*-
"""src/timetable/holiday_fetch.py 的自测（纯 stdlib + 本仓库模块，无 pytest）。

用法（工作目录 = 仓库根）：
    python tools/test_holiday_fetch.py

覆盖：
  * 相邻同名 off-day 合并、调休打断合并、乱序 days 自行排序（含复合名「国庆节、中秋节」）；
  * 跨年 holidays_for_range 与 derive_semester_years（秋季学期 20 周 → [2026, 2027]）；
  * to_holiday_file_text ↔ holidays.load_holiday_file 文本往返一致（关键需求）；
  * 缓存链：session/内存 → 磁盘 → 回源，坏缓存/坏数据一律降级不炸；use_cache=False 绕过；
  * 错误分支全部用假镜像测，**不打真外网**：
      - file:/// 镜像缺年份文件 → 「尚未发布」文案（未来年份）；
      - 不存在的 .invalid 域名  → 「网络不通」文案，且给足三条出路；
      - 坏 JSON / 年份不符 / days 为空 / 顶层不是对象 → 校验错误，不是 TypeError；
    （只有一个用例真联网：默认双源拉 2099 探 404、以及最后与 DEFAULT_HOLIDAYS 的交叉比对；
     没网时这两个自动 SKIP，不算失败。）
  * 真联网拉 2026 并与 holidays.DEFAULT_HOLIDAYS / DEFAULT_MAKEUP_DAYS 交叉比对。

约定：每个用例把 TIMETABLE_HOLIDAY_CACHE_DIR 指到一次性临时目录并清空 _MEM，
不污染用户真实缓存；对 holiday_fetch 的私有 _MEM 的直接操作仅限本仓库自测。
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys
import tempfile
import traceback
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")   # 从仓库根或 tools/ 下跑都要能找到包
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from timetable import holiday_fetch as hf
from timetable import holidays as hol
from timetable.holidays import Holiday, MakeupDay

_D = dt.date.fromisoformat


def day(name, s, off):
    return {"name": name, "date": s, "isOffDay": off}


# 故意乱序 + 复合名 + 同一天单休（端午）+ 被调休打断的同名区间（2025），
# 每个 fixture 都踩一个真实数据里的坑。
FIX2026 = [
    day("元旦", "2026-01-01", True), day("元旦", "2026-01-02", True),
    day("元旦", "2026-01-03", True), day("元旦", "2026-01-04", False),
    day("春节", "2026-02-15", True), day("春节", "2026-02-16", True),
    day("春节", "2026-02-14", False),                      # 排在放假日之后：测排序
    day("国庆节、中秋节", "2026-10-01", True),
    day("国庆节、中秋节", "2026-10-02", True),             # 复合名合并成一个区间
    day("国庆节", "2026-10-03", True),                     # 名字变了 → 不合并
    day("中秋节", "2026-09-25", True), day("中秋节", "2026-09-26", True),
    day("中秋节", "2026-09-27", True),
    day("国庆节", "2026-09-20", False),                    # 调休打断合并
    day("端午节", "2026-06-19", True),                     # 单天区间
]
OBJ2026 = {"year": 2026, "papers": ["https://www.gov.cn/fake/2026.htm"], "days": FIX2026}
EXP2026_HOLIDAYS = [
    Holiday(_D("2026-01-01"), _D("2026-01-03"), "元旦"),
    Holiday(_D("2026-02-15"), _D("2026-02-16"), "春节"),
    Holiday(_D("2026-06-19"), _D("2026-06-19"), "端午节"),
    Holiday(_D("2026-09-25"), _D("2026-09-27"), "中秋节"),
    Holiday(_D("2026-10-01"), _D("2026-10-02"), "国庆节、中秋节"),
    Holiday(_D("2026-10-03"), _D("2026-10-03"), "国庆节"),
]
EXP2026_MAKEUP = [_D("2026-01-04"), _D("2026-02-14"), _D("2026-09-20")]

OBJ2025 = {"year": 2025, "papers": ["https://www.gov.cn/fake/2025.htm"], "days": [
    day("元旦", "2025-01-01", True),
    day("国庆节、中秋节", "2025-10-01", True),
    day("国庆节、中秋节", "2025-10-02", True),
    day("国庆节、中秋节", "2025-10-03", False),   # 同名区间被上班日劈成两段
    day("国庆节、中秋节", "2025-10-04", True),
    day("国庆节、中秋节", "2025-10-05", True),
]}
EXP2025_HOLIDAYS = [
    Holiday(_D("2025-01-01"), _D("2025-01-01"), "元旦"),
    Holiday(_D("2025-10-01"), _D("2025-10-02"), "国庆节、中秋节"),
    Holiday(_D("2025-10-04"), _D("2025-10-05"), "国庆节、中秋节"),
]

OBJ2027 = {"year": 2027, "papers": ["https://www.gov.cn/fake/2027.htm"], "days": [
    day("元旦", "2027-01-01", True), day("元旦", "2027-01-03", False),
    day("春节", "2027-02-05", True), day("春节", "2027-02-06", True),
]}
POISON = {"year": 2026, "papers": [],
          "days": [day("毒节", "2026-12-31", True)]}   # 结构合法但内容作恶：
                                                       # 凡命中缓存都不该看见它


# --------------------------------------------------------------- 工具

def fresh_env():
    """一次性镜像+缓存环境：临时缓存目录（走模块的 env 后门）+ 清空进程内存缓存。"""
    root = tempfile.mkdtemp(prefix="ttc-hol-")
    os.environ["TIMETABLE_HOLIDAY_CACHE_DIR"] = os.path.join(root, "cache")
    hf._MEM.clear()
    return root


def file_url(path):
    """本地路径 → file:/// URL（percent-encode 中文/空格，但保留 {year} 占位）。"""
    p = path.replace("\\", "/")
    return "file:///" + urllib.parse.quote(p.lstrip("/"), safe="/:@{}")


def make_mirror(root, by_year):
    """把 {年份: payload} 写成一个 file:/// 假镜像，返回 url_template。"""
    d = os.path.join(root, "mirror")
    os.makedirs(d, exist_ok=True)
    for y, obj in by_year.items():
        with io.open(os.path.join(d, "%d.json" % y), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False))
    return file_url(os.path.join(d, "{year}.json"))


def cache_file(year):
    return os.path.join(hf.cache_dir(), "%d.json" % year)


def expect_error(fn, *substr, **kw):
    """fn 必须抛 HolidayFetchError，且 message 含所有 substr。"""
    try:
        fn(**kw)
    except hf.HolidayFetchError as exc:
        msg = str(exc)
        for s in substr:
            assert s in msg, "错误信息缺少「%s」：\n%s" % (s, msg)
        return msg
    raise AssertionError("没有抛 HolidayFetchError（静默成功了，最不能接受）")


# --------------------------------------------------------------- 用例

def test_sources_and_cache_dir():
    srcs = hf.sources()
    assert len(srcs) == 2 and "{year}" in srcs[0] and "{year}" in srcs[1]
    assert "jsdelivr" in srcs[0] and "NateScarlet/holiday-cn" in srcs[0]
    assert "raw.githubusercontent.com/NateScarlet/holiday-cn" in srcs[1]

    root = fresh_env()
    assert hf.cache_dir() == os.environ["TIMETABLE_HOLIDAY_CACHE_DIR"]
    try:
        del os.environ["TIMETABLE_HOLIDAY_CACHE_DIR"]
        d = hf.cache_dir()
        assert d.endswith("holidays") and not d.startswith(HERE)  # 绝不是程序目录
        if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
            assert d == os.path.join(os.environ["LOCALAPPDATA"],
                                     "TimetableToCalendar", "holidays")
        else:
            assert d == os.path.join(os.path.expanduser("~"),
                                     ".timetable2calendar", "holidays")
    finally:
        os.environ["TIMETABLE_HOLIDAY_CACHE_DIR"] = os.path.join(root, "cache")


def test_merge_adjacent():
    root = fresh_env()
    tpl = make_mirror(root, {2026: OBJ2026})
    yh = hf.fetch_year(2026, url_template=tpl)

    assert yh.holidays == EXP2026_HOLIDAYS, yh.holidays        # 合并/排序/复合名全对
    assert [m.date for m in yh.makeup_days] == EXP2026_MAKEUP  # 调休按日期升序
    assert all(m.follow_weekday is None for m in yh.makeup_days)
    assert yh.makeup_days[0].note == "元旦调休上班"
    assert yh.makeup_days[2].note == "国庆节调休上班"
    assert yh.years == [2026] and yh.from_cache is False
    assert yh.papers == ["https://www.gov.cn/fake/2026.htm"]
    assert yh.source.endswith("/2026.json") and yh.source.startswith("file:///")
    assert yh.fetched_at.startswith("20")                       # ISO 时间打头
    lines = yh.describe().splitlines()
    assert lines[0] == hol.describe(yh.holidays, yh.makeup_days)  # 摘要与 holidays 一致
    assert "本次未联网" not in yh.describe()


def test_cache_chain():
    root = fresh_env()
    tpl = make_mirror(root, {2026: OBJ2026})
    s = {}
    yh1 = hf.fetch_year(2026, url_template=tpl, session=s)
    assert yh1.from_cache is False and 2026 in s
    assert os.path.isfile(cache_file(2026))                     # 磁盘缓存落了

    # —— 毒化镜像：之后凡「回源」都会拿到单条「毒节」，命中缓存必须看不见它
    with io.open(os.path.join(root, "mirror", "2026.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(POISON, ensure_ascii=False))

    # a) session 命中：把磁盘缓存写坏、_MEM 清空——只有 session 这条路能给好数据
    with io.open(cache_file(2026), "w", encoding="utf-8") as fh:
        fh.write("坏缓存，不是 JSON")
    hf._MEM.clear()
    ya = hf.fetch_year(2026, url_template=tpl, session=s)
    assert ya.from_cache is True and ya.holidays == EXP2026_HOLIDAYS
    assert "命中缓存" in ya.describe()

    # b) 磁盘命中：手工放回一份好信封，session 弃用、_MEM 清空 → 只能从磁盘读
    with io.open(cache_file(2026), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(OBJ2026, cached_at="2026-01-01T00:00:00+08:00",
                                 cached_source=yh1.source), ensure_ascii=False))
    yb = hf.fetch_year(2026, url_template=tpl)                  # session=None
    assert yb.from_cache is True and yb.holidays == EXP2026_HOLIDAYS
    assert yb.source == yh1.source
    assert yb.fetched_at == "2026-01-01T00:00:00+08:00"         # 信封里的原始抓取时间
    # 命中返回的是拷贝：改坏返回值不能污染缓存（下一次 _MEM 命中仍是 6 条）
    yb.holidays.append(Holiday(_D("2999-01-01"), _D("2999-01-01"), "污染"))
    yc = hf.fetch_year(2026, url_template=tpl)
    assert len(yc.holidays) == len(EXP2026_HOLIDAYS)

    # c) 全未命中 → 回源（毒镜像数据如实取回，证明链真的会落到底）
    hf._MEM.clear()
    os.remove(cache_file(2026))
    ynet = hf.fetch_year(2026, url_template=tpl)
    assert ynet.from_cache is False and len(ynet.holidays) == 1
    assert ynet.holidays[0].name == "毒节"

    # d) 坏缓存文件 → 视为未命中重新取，绝不炸
    root2 = fresh_env()
    tpl2 = make_mirror(root2, {2025: OBJ2025})
    y25a = hf.fetch_year(2025, url_template=tpl2)
    assert y25a.holidays == EXP2025_HOLIDAYS                    # 同名区间被打断处劈开
    with io.open(cache_file(2025), "w", encoding="utf-8") as fh:
        fh.write("} { 这不是 JSON")
    hf._MEM.clear()
    y25b = hf.fetch_year(2025, url_template=tpl2)
    assert y25b.from_cache is False and y25b.holidays == EXP2025_HOLIDAYS

    # e) clear_cache 计数与内存联动
    assert os.path.isfile(cache_file(2025))
    assert hf.clear_cache(2025) == 1 and not os.path.isfile(cache_file(2025))
    assert hf.clear_cache(2025) == 0                            # 本来就没有，不算错
    assert hf.clear_cache(2030) == 0
    hf._MEM[2025] = y25a
    with io.open(cache_file(2025), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(OBJ2025, cached_at="x"), ensure_ascii=False))
    assert hf.clear_cache() >= 1                                # 全清
    assert 2025 not in hf._MEM                                  # 内存也丢了


def test_use_cache_false_bypasses_everything():
    root = fresh_env()
    tpl = make_mirror(root, {2026: OBJ2026})
    yh = hf.fetch_year(2026, url_template=tpl, use_cache=False)
    assert yh.from_cache is False
    assert not os.path.isfile(cache_file(2026))                 # 不写磁盘
    assert 2026 not in hf._MEM                                  # 也不回填内存

    # 磁盘上放一份好缓存，use_cache=False 必须无视它去回源（取回毒数据）
    with io.open(cache_file(2026), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(OBJ2026, cached_at="x",
                                 cached_source=tpl.format(year=2026)),
                            ensure_ascii=False))
    with io.open(os.path.join(root, "mirror", "2026.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(POISON, ensure_ascii=False))
    y2 = hf.fetch_year(2026, url_template=tpl, use_cache=False)
    assert y2.from_cache is False and y2.holidays[0].name == "毒节"


def test_range_and_semester_years():
    root = fresh_env()
    tpl = make_mirror(root, {2026: OBJ2026, 2027: OBJ2027})
    yh = hf.holidays_for_range(_D("2026-08-31"), _D("2027-01-17"), url_template=tpl)
    assert yh.years == [2026, 2027]
    starts = [h.start for h in yh.holidays]
    assert starts == sorted(starts)                             # 跨年也按日期升序
    assert any(h.name == "元旦" and h.start == _D("2027-01-01") for h in yh.holidays)
    assert any(h.name == "春节" and h.start == _D("2027-02-05") for h in yh.holidays)
    assert _D("2027-01-03") in [m.date for m in yh.makeup_days]
    assert len(yh.papers) == 2                                  # 通知原文链接合并去重
    assert "2026.json" in yh.source and "2027.json" in yh.source
    assert yh.from_cache is False

    # 覆盖年份推导 = 区间的自然年（两个入口口径必须一致，不然用户会见到两套答案）
    w1, n = _D("2026-08-31"), 20
    assert hf.derive_semester_years(w1, n) == [2026, 2027]      # 西工大 2026 秋
    assert hf.derive_semester_years(w1, 14) == [2026]           # 提前结束不出跨年
    assert hf.derive_semester_years(w1, 21) == [2026, 2027]
    assert hf.derive_semester_years(_D("2027-02-22"), 18) == [2027]
    assert yh.years == hf.derive_semester_years(w1, n)          # 2027 元旦在学期尾周内

    expect_error(lambda **k: hf.derive_semester_years(w1, 0, **k))
    expect_error(lambda **k: hf.derive_semester_years("2026-08-31", 20, **k))
    expect_error(lambda **k: hf.holidays_for_range(_D("2027-01-17"), _D("2026-08-31"), **k),
                 "区间反了")


def test_text_roundtrip():
    root = fresh_env()
    tpl = make_mirror(root, {2026: OBJ2026, 2025: OBJ2025})
    yh = hf.holidays_for_range(_D("2025-01-01"), _D("2026-12-31"), url_template=tpl)
    text = hf.to_holiday_file_text(yh)

    assert "放假 2026-10-01 2026-10-02 国庆节、中秋节" in text   # 复合名原样进 DSL
    assert "放假 2026-06-19 端午节" in text                      # 单天用两列写法
    assert "调休 2026-09-20" in text
    assert "https://www.gov.cn/fake/2026.htm" in text           # 溯源注释

    path = os.path.join(root, "holidays.txt")
    with io.open(path, "w", encoding="utf-8") as fh:            # 显式 utf-8，不走 PS 管道
        fh.write(text)
    hs, ms = hol.load_holiday_file(path)
    assert hs == yh.holidays                                    # Holiday 集合一模一样
    assert [(m.date, m.follow_weekday) for m in ms] == \
           [(m.date, m.follow_weekday) for m in yh.makeup_days]  # note 进不了 DSL，只比这两个
    assert all(m.note == "调休上班" for m in ms)

    # follow_weekday 非 None 也能往返（导出「调休 日期 周三」，读回 2）
    yh2 = hf.YearHolidays(
        holidays=[Holiday(_D("2026-05-01"), _D("2026-05-03"), "劳动节")],
        makeup_days=[MakeupDay(_D("2026-04-26"), 2, "补周三的课")],
        years=[2026], source="test", papers=[], fetched_at="x", from_cache=False)
    t2 = hf.to_holiday_file_text(yh2)
    assert "调休 2026-04-26 周三" in t2
    p2 = os.path.join(root, "rt2.txt")
    with io.open(p2, "w", encoding="utf-8") as fh:
        fh.write(t2)
    _, mm = hol.load_holiday_file(p2)
    assert mm[0].follow_weekday == 2 and mm[0].date == _D("2026-04-26")

    # 会破坏往返的输入必须当场报错，而不是写出读不回来的行
    bad = lambda name, md=None: hf.YearHolidays(
        holidays=[Holiday(_D("2026-05-01"), _D("2026-05-03"), name)],
        makeup_days=md or [], years=[2026], source="t", papers=[],
        fetched_at="x", from_cache=False)
    expect_error(lambda: hf.to_holiday_file_text(bad("五 一")), "空格")
    expect_error(lambda: hf.to_holiday_file_text(bad("国#庆")), "#")
    expect_error(lambda: hf.to_holiday_file_text(bad("元旦", [MakeupDay(_D("2026-01-04"), 9, "")])),
                 "0~6")
    expect_error(lambda: hf.to_holiday_file_text(hf.YearHolidays(
        holidays=[Holiday(_D("2026-05-03"), _D("2026-05-01"), "反了")], makeup_days=[],
        years=[2026], source="t", papers=[], fetched_at="x", from_cache=False)), "区间反了")


def test_error_404_vs_network():
    root = fresh_env()
    future = dt.date.today().year + 1
    tpl = make_mirror(root, {2026: OBJ2026})                   # 镜像里只有 2026

    # 未来年份缺文件 → 「尚未发布」，而不是笼统的「网络不通」
    msg = expect_error(lambda **k: hf.fetch_year(future, url_template=tpl, **k),
                       "尚未发布", "11 月初", "--holidays-file", "--holidays-url",
                       "导出模板", str(future), timeout=5)
    assert "×" in msg                                           # 列了尝试过的地址

    # 过去年份缺文件 → 说明是镜像不全，不是「还没发布」
    expect_error(lambda **k: hf.fetch_year(2016, url_template=tpl, **k),
                 "服务器上没有 2016", timeout=5)

    # 域名不存在 → 「网络不通」类文案，且绝不出现「尚未发布」误导。
    # （若环境配了 HTTP_PROXY，代理可能以别的姿态回错——那也属于「取不到」，
    #  关键是必须报错、给足出路，而不是静默空列表。）
    msg = expect_error(lambda **k: hf.fetch_year(
        2026, url_template="https://timetable-nonexistent-host.invalid/{year}.json", **k),
        "--holidays-file", "--holidays-url", "holidays off", timeout=5)
    assert any(k in msg for k in ("网络不可达", "HTTP", "超时", "读取失败")), msg
    assert "尚未发布" not in msg
    assert ".invalid" in msg                                    # 列了尝试过的地址

    # url_template 自身校验
    expect_error(lambda **k: hf.fetch_year(2026, url_template="https://x.com/a.json", **k),
                 "占位符")
    expect_error(lambda **k: hf.fetch_year(2026, url_template=r"D:\mir\{year}.json", **k),
                 "file:///")

    # 参数校验：这些全是要冒 TypeError/ValueError 的入口，必须被拦成 HolidayFetchError
    expect_error(lambda **k: hf.fetch_year("2026", **k), "整数")
    expect_error(lambda **k: hf.fetch_year(True, **k), "整数")
    expect_error(lambda **k: hf.fetch_year(1900, **k), "超出合理范围")
    expect_error(lambda **k: hf.fetch_year(2026, timeout=0, **k), "正数")
    expect_error(lambda **k: hf.fetch_year(2026, timeout="快一点", **k), "数字")

    # 数据校验：坏 JSON / 年份不符 / days 空 / 顶层不是对象 / days 条目逐条校验。
    # 每份坏数据放各自的小目录、文件名必须叫 {year}.json —— 模板不带占位符会先被
    # _check_templates 拦掉，这里测的是「下载之后」的校验链。
    def bad_url(name, text):
        sub = os.path.join(root, "m_" + name)
        os.makedirs(sub, exist_ok=True)
        with io.open(os.path.join(sub, "2026.json"), "w", encoding="utf-8") as fh:
            fh.write(text)
        return file_url(os.path.join(sub, "{year}.json"))

    expect_error(lambda **k: hf.fetch_year(
        2026, url_template=bad_url("badjson", "}{ 坏 JSON"), **k), "不是合法")
    expect_error(lambda **k: hf.fetch_year(
        2026, url_template=bad_url("badyear", json.dumps(dict(OBJ2026, year=2027))), **k),
        "不符")
    expect_error(lambda **k: hf.fetch_year(
        2026, url_template=bad_url("emptydays", json.dumps({"year": 2026, "days": []})), **k),
        "days")
    expect_error(lambda **k: hf.fetch_year(
        2026, url_template=bad_url("toplist", json.dumps([1, 2])), **k), "顶层应为对象")
    for name, bad_days, hint in [
            ("baddate", [day("节", "2026-1-1", True)], "YYYY-MM-DD"),
            ("noname", [{"date": "2026-01-01", "isOffDay": True}], "name"),
            ("noff", [{"name": "节", "date": "2026-01-01"}], "isOffDay"),
            ("feb30", [{"name": "节", "date": "2026-02-30", "isOffDay": True}], "日期非法"),
            ("dup", [day("节", "2026-01-01", True), day("节", "2026-01-01", False)], "多次")]:
        expect_error(lambda **k: hf.fetch_year(
            2026, url_template=bad_url(name, json.dumps({"year": 2026, "days": bad_days},
                                                        ensure_ascii=False)), **k), hint)


def test_http_404_real_probe():
    """真联网探一次 HTTP 404 分支（2099 年必无数据；一次请求，不多打）。
    机器没网时消息会是「网络不通」——那也是正确分支，同样通过。"""
    fresh_env()
    try:
        yh = hf.fetch_year(2099, timeout=8)
    except hf.HolidayFetchError as exc:
        msg = str(exc)
        assert ("404" in msg or "尚未发布" in msg or "网络" in msg), msg
        assert "--holidays-file" in msg
        return
    print("    （意外取到 2099 数据？上游行为变了，请检查）")


def test_live_2026_crosscheck():
    """真联网拉 2026，与 holidays.DEFAULT_HOLIDAYS / DEFAULT_MAKEUP_DAYS 交叉比对。"""
    fresh_env()
    try:
        yh = hf.fetch_year(2026, timeout=15)
    except hf.HolidayFetchError as exc:
        if "网络" in str(exc):
            print("    SKIP：本机当前无法访问 holiday-cn（离线环境），跳过交叉比对")
            return
        raise
    # 2026 秋学期相关的两条硬编码预设，必须逐字段命中联网数据
    for d in hol.DEFAULT_HOLIDAYS:
        assert d in yh.holidays, "联网数据里没有硬编码预设的 %s" % d
    # 名称也要对得上（国务院口径就是「中秋节」「国庆节」）
    names = {(h.name, h.start, h.end) for h in yh.holidays}
    assert ("中秋节", _D("2026-09-25"), _D("2026-09-27")) in names
    assert ("国庆节", _D("2026-10-01"), _D("2026-10-07")) in names
    mk = {m.date for m in yh.makeup_days}
    assert mk == {_D("2026-01-04"), _D("2026-02-14"), _D("2026-02-28"),
                  _D("2026-05-09"), _D("2026-09-20"), _D("2026-10-10")}, sorted(mk)
    assert {m.date for m in hol.DEFAULT_MAKEUP_DAYS} <= mk
    assert yh.papers and "gov.cn" in yh.papers[0]
    assert yh.from_cache is False
    # 再走一次内存缓存，结果必须逐条一致（除 from_cache 标注）
    again = hf.fetch_year(2026)
    assert again.from_cache is True and again.holidays == yh.holidays
    assert again.makeup_days == yh.makeup_days
    print(yh.describe())


# --------------------------------------------------------------- 运行

def main():
    tests = [test_sources_and_cache_dir, test_merge_adjacent, test_cache_chain,
             test_use_cache_false_bypasses_everything, test_range_and_semester_years,
             test_text_roundtrip, test_error_404_vs_network, test_http_404_real_probe,
             test_live_2026_crosscheck]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print("FAIL  %s" % t.__name__)
            traceback.print_exc()
        else:
            print("PASS  %s" % t.__name__)
    total = len(tests)
    print("\n%d/%d 通过%s" % (total - failed, total, "" if not failed else "，失败见上"))
    if failed:
        return 1
    # 收尾确认真实默认缓存路径（供人工核对；env 后门已在各用例内恢复）
    del os.environ["TIMETABLE_HOLIDAY_CACHE_DIR"]
    hf._MEM.clear()
    print("默认磁盘缓存目录：%s" % hf.cache_dir())
    return 0


if __name__ == "__main__":
    sys.exit(main())
