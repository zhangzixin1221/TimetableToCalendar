# -*- coding: utf-8 -*-
"""版式层回归测试（不依赖真实课表 PDF）。

    python tools\\test_layout.py

覆盖 parser/layout/presets/ics 里「与学校无关」的那部分逻辑。之所以单独一份：
`selftest.py` 要有真实课表才能跑，而版式层大量是边界判断（正则拼错、字段名写错、
切分策略退化），这些必须能在任何机器上、没有课表的情况下也验一遍。

顺带把几条「设计底线」写成断言钉住，防止以后被顺手改回去：
  * 字段名写错必须报错，不能静默用默认值（否则换学校时会拿到一份"看起来能用其实全错"的版式）
  * 版式没有自带作息时，绝不能回落到西工大的表
  * 合并连排不能就地改写调用方的记录
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from timetable import holidays as hol_mod                    # noqa: E402
from timetable import layout as lay_mod                      # noqa: E402
from timetable import presets                                # noqa: E402
from timetable.ics import Config, build_ics, merge_contiguous  # noqa: E402
from timetable.parser import (Meta, ParseResult, Record, Segment,  # noqa: E402
                              compress_weeks, verify_parse)

ok_all = True
cases = []


def check(label, cond, extra=""):
    global ok_all
    if not cond:
        ok_all = False
    cases.append(("PASS" if cond else "FAIL", label, extra))
    return cond


def expect_error(fn, must_contain, label):
    """必须抛 LayoutError 且信息里有指定字样（只要求"抛错"是不够的：抛错但没说清
    哪里错，用户照样会拿着一份坏版式反复试）。"""
    try:
        fn()
    except lay_mod.LayoutError as exc:
        return check(label, must_contain in str(exc), "" if must_contain in str(exc)
                     else "报错信息里没有 %r，实际：%s" % (must_contain, exc))
    except Exception as exc:                              # noqa: BLE001
        return check(label, False, "抛的是 %s 而不是 LayoutError：%s"
                     % (type(exc).__name__, exc))
    return check(label, False, "根本没报错")


# ============================================================ 1) 教室/教师切分
print("[1] split_room_teacher 各策略")
S = lay_mod.split_room_teacher
check("西工大式：教室在前、教师是结尾 2~4 汉字",
      S("教西D-101 张三") == ("教西D-101", "张三"), str(S("教西D-101 张三")))
check("教师名前有空格也不会被吃进教室",
      S("实验大楼C座5楼 向礼琴") == ("实验大楼C座5楼", "向礼琴"))
check("三字名", S("教三101 欧阳锋") == ("教三101", "欧阳锋"))
check("没有空格就整段当教室（不猜教师）",
      S("教西D-101") == ("教西D-101", ""))
check("结尾不像人名就不切",
      S("翱翔体育馆 1~8") == ("翱翔体育馆1~8", ""), str(S("翱翔体育馆 1~8")))

check("教师在前、教室在末（space_tail_room）",
      S("张伟 教三-101", "space_tail_room") == ("教三-101", "张伟"),
      str(S("张伟 教三-101", "space_tail_room")))
check("space_tail_room 丢掉周次残段，不把「1-16周」并进教室",
      S("1-16周 张伟 教三-101", "space_tail_room") == ("教三-101", "张伟"),
      str(S("1-16周 张伟 教三-101", "space_tail_room")))
check("space_tail_room 保留像地点的前置段（多词教室名不丢信息）",
      S("校区 张伟 教三-101", "space_tail_room") == ("校区教三-101", "张伟"),
      str(S("校区 张伟 教三-101", "space_tail_room")))
check("space_tail_room 结尾不是地点样式就整段当教室，不乱猜教师",
      S("张伟 王强", "space_tail_room") == ("张伟王强", ""))

check("labels：显式标签优先", S("教师：张伟 教室：A201", "labels") == ("A201", "张伟"))
check("labels：没标签退回「教室在前」", S("教三101 张伟", "labels") == ("教三101", "张伟"))
check("room_only 一律不产教师", S("腾讯会议 123456", "room_only") == ("腾讯会议123456", ""))
check("none 原样保留", S("a b c", "none") == ("a b c", ""))
check("空串不炸", S("") == ("", "") and S(None) == ("", ""))

# ============================================================ 2) 周次归一
print("[2] expand_weeks_token")
E = lay_mod.expand_weeks_token
check("1-16周", E("1-16周") == list(range(1, 17)))
check("裸 1-8", E("1-8") == [1, 2, 3, 4, 5, 6, 7, 8])
check("中文波浪号与 em dash", E("1—8周") == list(range(1, 9)) and E("2~5") == [2, 3, 4, 5])
check("单周", E("1-8(单)") == [1, 3, 5, 7])
check("双周", E("2-10(双)") == [2, 4, 6, 8, 10])
check("第3周", E("第3周") == [3])
check("逗号列举", E("1,3,5") == [1, 3, 5])
check("顿号/中文逗号也认", E("1、3、5") == [1, 3, 5] and E("1，3，5") == [1, 3, 5])
check("乱序去重", E("5,1,5,3") == [1, 3, 5])
check("奇偶参数也能从外面传", E("1-9", "单") == [1, 3, 5, 7, 9]
      and E("1-9", "双") == [2, 4, 6, 8])
check("compress 往返", compress_weeks(E("1-3周")) == "1~3"
      and compress_weeks(E("1,3,5")) == "1、3、5")
check("空串是「没有周次」而不是「非法」，返回空表由调用方决定怎么报",
      E("") == [] and E("   ") == [] and E("周") == [])
for bad, why in [("16-1周", "反了"), ("abc", "看不懂"), ("1-", "残缺")]:
    try:
        E(bad)
        check("非法周次 %r 必须报错" % bad, False, "没报错（应该拒）")
    except lay_mod.LayoutError:
        check("非法周次 %r 报错" % bad, True, why)

# ============================================================ 3) 版式载入与校验
print("[3] Layout 载入 / 校验")
nwpu = lay_mod.REGISTRY.get("nwpu")
check("内置 nwpu 能编译全部正则", bool(nwpu.compiled()))
check("内置版式都合法", all(l.compiled() is not None for l in lay_mod.REGISTRY.all()))
check("nwpu 作息三套且默认长安",
      len(presets.preset_names("nwpu")) == 3
      and presets.default_preset_name("nwpu") == "西北工业大学 · 长安校区")
check("长安 1 节 08:30-09:15、13 节 20:40-21:25",
      presets.get_preset(None, "nwpu")[1] == ("08:30", "09:15")
      and presets.get_preset(None, "nwpu")[13] == ("20:40", "21:25"))
# 这条是底线：换学校绝不能表面换个名字、骨子里还是西工大的作息
check("没有自带作息的版式返回空表，而不是回落到西工大",
      presets.get_preset(None, "inline-grid") == {}
      and presets.preset_names("inline-grid") == []
      and presets.default_preset_name("inline-grid") == "")
check("find_preset_containing 认得出当前用的是哪套预设",
      presets.find_preset_containing(presets.get_preset(None, "nwpu"), "nwpu")
      == "西北工业大学 · 长安校区")
check("改过一节就不再匹配任何预设",
      (lambda d: (d.__setitem__(1, ("07:00", "07:45")),
                  presets.find_preset_containing(d, "nwpu") == "")[1])(
          presets.get_preset(None, "nwpu")))

tmp = tempfile.mkdtemp(prefix="layout-test-")


def write_json(obj, name="x.json"):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


p = write_json(nwpu.to_dict() | {"id": "mine", "name": "我的学校"})
back = lay_mod.load_layout_file(p)
check("to_dict → from_dict 往返一致",
      back.title_re == nwpu.title_re and back.weeks_re == nwpu.weeks_re
      and back.preset_times("西北工业大学 · 长安校区") == presets.get_preset(None, "nwpu"))
check("往返保留 specificity（自动识别要靠它）", back.specificity == nwpu.specificity)

# 字段名写错必须报错：静默用默认值是「换学校时最难查」的一类坑
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b", "weeks_regex": "(?P<a>\\d+)周"}, "bad1.json")),
    "不认识的字段", "字段名写错要报错")
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b", "weeks_re": "(?P<wrong>\\d+)周"}, "bad2.json")),
    "不认识的分组名", "正则分组名写错要报错")
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b", "weeks_re": "(?P<a>[+"}, "bad3.json")),
    "不是合法正则", "正则语法错要报错")
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b", "room_teacher": "magic"}, "bad4.json")),
    "room_teacher", "切分策略名写错要报错")
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b", "entry_mode": "grid"}, "bad5.json")),
    "entry_mode", "entry_mode 写错要报错")
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b", "period_presets": {"x": {"1": ["8:00"]}}}, "bad6.json")),
    "作息", "作息格式错要报错")
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b", "period_presets": {"x": {"1": ["8点", "9:00"]}}},
    "bad6b.json")), "HH:MM", "作息时间不是 HH:MM 要报错")
# 「8:00」这种不补零的写法要收下，但必须存成 08:00 —— 否则 ics 里会拼出 T80000
# 这种畸形时间（日历端拒收或排到错误时刻），属于「不报错、只出错」的一类。
_lenient = lay_mod.Layout.from_dict(
    {"id": "a", "name": "b", "period_presets": {"x": {"1": ["8:00", "9:30"]}}})
check("小时不补零的作息会被规范化成 08:00 / 09:30",
      _lenient.preset_times("x")[1] == ("08:00", "09:30"),
      str(_lenient.preset_times("x")))
expect_error(lambda: lay_mod.Layout.from_dict(
    {"id": "a", "name": "b", "period_presets": {"x": {"1": ["25:00", "26:00"]}}}),
    "不是合法时刻", "超出 0-23 点的作息要报错")
expect_error(lambda: lay_mod.load_layout_file(write_json(
    {"id": "a", "name": "b",
     "period_presets": {"甲": {"1": ["08:00", "08:50"]}}, "default_preset": "乙"},
    "bad6c.json")), "default_preset", "默认预设名写错要报错（否则会静默用错校区的作息）")
expect_error(lambda: lay_mod.load_layout_file(os.path.join(tmp, "不存在.json")),
             "读不到版式文件", "文件不存在要说清")
expect_error(lambda: lay_mod.load_layout_file(write_json([1, 2], "bad7.json")),
             "最外层要是对象", "顶层类型错要说清")
expect_error(lambda: lay_mod.load_layout_file(write_json({"name": "没id"}, "bad8.json")),
             "必须有 id 和 name", "缺必填字段要说清")

p_bad = os.path.join(tmp, "broken.json")
with open(p_bad, "w", encoding="utf-8") as f:
    f.write("{ 这不是 JSON")
expect_error(lambda: lay_mod.load_layout_file(p_bad), "不是合法 JSON", "坏 JSON 要说清")

lay_mod.export_template(os.path.join(tmp, "tpl.json"))
tpl = lay_mod.load_layout_file(os.path.join(tmp, "tpl.json"))
check("导出的模板本身可被载入（不会一发就报错）", tpl.id == "my-school" and tpl.compiled())

# ============================================================ 4) 合并语义
print("[4] merge_contiguous")


def rec(day, cf, ct, course="高等数学", room="A101", weeks=None, clock=None):
    return Record(day=day, course=course, code="", pfrom=cf, pto=ct, room=room,
                  segments=[Segment(weeks=weeks or list(range(1, 17)), teacher="张三")],
                  clock=clock)


orig = [rec(0, 11, 12), rec(0, 13, 13), rec(0, 1, 2), rec(0, 3, 4)]
snapshot = [(r.pfrom, r.pto, r.course, r.room) for r in orig]
for mode in ("all", "artifact", "none"):
    merge_contiguous(orig, mode)
    check("%s 模式不改写调用方的记录" % mode,
          [(r.pfrom, r.pto, r.course, r.room) for r in orig] == snapshot)
check("all：1-2+3-4 合成一条长日程",
      len(merge_contiguous(orig, "all")) == 2,
      str(len(merge_contiguous(orig, "all"))))
art = merge_contiguous(orig, "artifact")
check("artifact：只合排版碎片（11-12+13-13），不合并 1-2+3-4",
      len(art) == 3 and any(r.pfrom == 11 and r.pto == 13 for r in art)
      and any(r.pfrom == 1 and r.pto == 2 for r in art),
      str([(r.pfrom, r.pto) for r in art]))
check("none：完全按行", len(merge_contiguous(orig, "none")) == 4)

cl = merge_contiguous([rec(0, 1, 2, clock=("08:00", "08:50")),
                       rec(0, 3, 4, clock=("09:00", "09:40"))], "all")
check("两段都有 PDF 自带时刻 → 合并取外包区间",
      len(cl) == 1 and cl[0].clock == ("08:00", "09:40"), str(cl[0].clock))
mixed = merge_contiguous([rec(0, 1, 2, clock=("08:00", "08:50")), rec(0, 3, 4)], "all")
check("一段有时刻一段没有 → 不猜，退回按节次查作息",
      len(mixed) == 1 and mixed[0].clock is None, str(mixed[0].clock))

# ============================================================ 5) clock 优先于作息
print("[5] Record.clock 生效")
cfg = Config(week1_monday=dt.date(2026, 8, 31),
             period_times={1: ("08:30", "09:15"), 2: ("09:25", "10:10")},
             class_alarm_min=20, night_summary=True, night_hour=22, night_minute=0,
             calendar_name="t", merge_mode="all")
one = ParseResult(records=[rec(0, 1, 2, course="实验课", clock=("14:00", "16:30"))],
                  meta=Meta(term="2026-2027 秋"))
text, stats = build_ics(one, cfg)
check("有 clock 就用 PDF 上的时刻", "DTSTART;TZID=Asia/Shanghai:20260831T140000" in text,
      "找不到 14:00 开头")
check("下课时刻也来自 clock", "T163000" in text)
# 作息表里压根没有这门课的节次，但因为 PDF 自带时刻，照样能生成
nocfg = Config(week1_monday=dt.date(2026, 8, 31), period_times={},
               class_alarm_min=None, night_summary=False)
t2, _ = build_ics(ParseResult(records=[rec(0, 7, 8, course="夜航",
                                          clock=("19:00", "22:00"))], meta=Meta()), nocfg)
check("作息表是空的也能生成（只要 PDF 自带时刻）",
      "T190000" in t2 and "T220000" in t2)
try:
    build_ics(ParseResult(records=[rec(0, 9, 9, course="没作息")], meta=Meta()),
              Config(week1_monday=dt.date(2026, 8, 31), period_times={},
                     class_alarm_min=None, night_summary=False))
    check("没 clock 又没作息必须报错", False, "没报错")
except Exception as exc:                              # noqa: BLE001
    msg = str(exc)
    # 三件事都要有：指出缺哪一节、说明怎么补、给出至少一条可操作路径
    check("没 clock 又没作息必须报错",
          "9" in msg and ("上下课时间" in msg or "节次" in msg), msg[:60])
    check("缺节次的报错要给出可操作的出路（界面/命令行/换版式 至少两条）",
          sum(k in msg for k in ("节次时间", "--period-times", "--list-layouts")) >= 2,
          msg[:100])

# ============================================================ 6) 配置自检口径
print("[6] doctor 与新字段一致")
from timetable import doctor                                      # noqa: E402
from timetable.ics import build_day_map                           # noqa: E402

cfg2 = Config(week1_monday=dt.date(2026, 8, 31),
              period_times=presets.get_preset(None, "nwpu"),
              holidays=[hol_mod.Holiday(dt.date(2026, 10, 1), dt.date(2026, 10, 7), "国庆节")],
              makeup_days=[hol_mod.MakeupDay(dt.date(2026, 10, 10), None, "调休")],
              night_summary=False, class_alarm_min=None)
res2 = ParseResult(records=[rec(5, 1, 2, course="周六课")],   # 2026-10-10 是周六
                   meta=Meta(term="2026-2027 秋"))
day_map, cancelled, added = build_day_map(res2.records, cfg2)
hit = [r for d, r, w in added]
check("「按当天星期几执行」的调休日不再重复注入课程（曾经会同一时刻排两条）",
      hit == [], "额外注入了 %d 条" % len(hit))
check("那天确实有一节课（来自循环日程本身，不是补出来的）",
      len(day_map.get(dt.date(2026, 10, 10), [])) == 1)
findings = doctor.diagnose(cfg2, res2)
txt_all = " ".join(str(f) for f in findings)
check("doctor 对这种情况不谎报「补 N 节课」",
      "无需额外补" in txt_all or "本来没有" in txt_all, txt_all[:90])

cfg3 = Config(week1_monday=dt.date(2026, 8, 31),
              period_times=presets.get_preset(None, "nwpu"),
              makeup_days=[hol_mod.MakeupDay(dt.date(2026, 10, 10), 0, "周六补周一")],
              night_summary=False, class_alarm_min=None)
res3 = ParseResult(records=[rec(0, 1, 2, course="周一课"),
                            rec(5, 3, 4, course="周六课")], meta=Meta())
dm3, _, ad3 = build_day_map(res3.records, cfg3)
check("真要「周六补周一」时，那节课会被排进 10-10",
      any(r.course == "周一课" for r, w in dm3.get(dt.date(2026, 10, 10), []))
      and len(ad3) == 1)

# ============================================================ 7) 自动识别
print("[7] detect_layout 打分口径")
FIX = os.path.join(ROOT, "fixtures", "nwpu-sample.pdf")
if os.path.isfile(FIX):
    from timetable.parser import detect_layout, parse_pdf        # noqa: E402

    picked, notes = detect_layout(FIX)
    check("真实西工大版式的 fixture 自动识别回 nwpu（而不是更宽松的版式）",
          picked.id == "nwpu", picked.id + " / " + " | ".join(notes))
    r_nwpu = parse_pdf(FIX, layout=lay_mod.REGISTRY.get("nwpu"))
    # 不钉死记录数：fixture 会随覆盖场景增删，写死数字只会天天修测试。
    # 要钉的是「解析干净」这件事：有足够多的课、零冲突、零解析失败告警。
    chk_n = verify_parse(r_nwpu)
    check("fixture 解析出足够多的课且零冲突",
          len(r_nwpu.records) >= 20 and not chk_n["conflicts"] and not r_nwpu.warnings,
          "%d 条 / %d 冲突 / warnings=%r"
          % (len(r_nwpu.records), len(chk_n["conflicts"]), r_nwpu.warnings))
    check("多页提示放 notes、不污染 warnings",
          r_nwpu.warnings == [] and any("2 页" in n for n in r_nwpu.notes),
          "warnings=%r notes=%r" % (r_nwpu.warnings, r_nwpu.notes))
    # 越宽松的版式切得不一定更少，但**信息更少** —— 所以「条数」不能当主分。
    # 这条用「教师信息丢失」来钉住打分口径，比数条数稳（条数会随 fixture 变动）。
    r_inline = parse_pdf(FIX, layout=lay_mod.REGISTRY.get("inline-grid"))
    teach_n = sum(1 for r in r_nwpu.records if r.teacher_desc())
    teach_i = sum(1 for r in r_inline.records if r.teacher_desc())
    check("用错版式会丢信息（inline 在这份网格课表上基本读不出教师）",
          teach_i < teach_n,
          "nwpu 读出教师 %d 条 / inline 只有 %d 条" % (teach_n, teach_i))
else:
    print("  （fixtures/nwpu-sample.pdf 不在，跳过 detect_layout 实测）")

# 自造一个只匹配得上一份畸形版式的场景，验证"读不出课程"会报错而不是硬选
empty_reg = lay_mod.Registry([lay_mod.Layout(
    id="impossible", name="永远匹配不上", weeks_re=r"(?P<a>\d{9})周",
    period_re=r"(?P<pfrom>\d{9})节")])
if os.path.isfile(FIX):
    try:
        detect_layout(FIX, reg=empty_reg)
        check("没有任何版式读得出时必须报错", False, "没报错")
    except RuntimeError as exc:
        check("没有任何版式读得出时必须报错", "读不出" in str(exc), str(exc)[:60])

# ============================================================ 结果
print("\n" + "=" * 78)
for st, label, extra in cases:
    if st == "FAIL":
        print("  [FAIL] %s  %s" % (label, extra))
print("共 %d 项，失败 %d 项" % (len(cases), sum(1 for s, _, _ in cases if s == "FAIL")))
print("全部通过" if ok_all else "存在失败项")
sys.exit(0 if ok_all else 1)
