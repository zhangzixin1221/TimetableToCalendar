# -*- coding: utf-8 -*-
"""给「inline-grid」和「flat-table」两种版式造合成课表 PDF，并验证解析结果。

    python tools\\make_fixture_alt.py            # 只生成
    python tools\\make_fixture_alt.py --check    # 生成 + 解析 + 断言（回归入口）

为什么需要这一份：`nwpu` 版式有真实的导出格式做依据（见 make_fixture.py），而
inline / flat-table 是「按常见教务导出格式推断着实现」的 —— 没有任何真件可参考，
那就更必须有测试钉住它们的行为，否则改一次 parser 就可能悄悄把它们弄坏。
事实上这份测试一写出来就抓到了两个真 bug：
  * inline 模式的课程名提取根本没被调用 → 整份课表解析出 0 条记录；
  * 用「(?P<course>.*?)(全是可选的后缀)」猜课程名 → 懒惰匹配只吃一个字符，
    课程名只剩第一个字。
两个都已修（现在课程名从「周次/节次之前那一段」几何取，不再靠正则猜）。

覆盖的形态：
  inline  单双周同格互补（同课名→合并成一条 2 段）、教师/教室首位互换、
          纯汉字地点名（"翱翔体育馆"没有数字）、多天多节次。
  table   `1-16周` / `2-10(双)` / `1,3,5` 三种周次写法、列名识别、
          一行一条课、教室名含"楼/馆"等无数字地点。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import pymupdf                                          # noqa: E402

from timetable import layout as lay_mod                 # noqa: E402
from timetable.parser import (compress_weeks, parse_pdf,  # noqa: E402
                              verify_parse)

FIXDIR = os.path.join(ROOT, "fixtures")
INLINE_OUT = os.path.join(FIXDIR, "inline-sample.pdf")
TABLE_OUT = os.path.join(FIXDIR, "flat-table-sample.pdf")

COL_X = [120, 230, 340, 450, 560, 670, 780]          # 七列中心
DAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# (星期几 0-6, 行文本) —— 一行就是一条完整安排：课程名 周次 节次 教师 教室
INLINE_ROWS = [
    (0, "高等数学A 1-16周 1-2节 陈明远 教西D-101"),
    (0, "大学英语 1-16周 3-4节 王小明 友谊西外A-302"),
    (0, "数据结构 2-16(双)周 7-8节 李华 教西A-201"),
    (0, "数据结构 1-15(单)周 7-8节 王强 教西A-201"),
    (1, "大学物理 1-16周 1-2节 孙雅雯 教西C-202"),
    (1, "体育 1-16周 5-6节 张伟 翱翔体育馆"),
    (2, "操作系统 1-16周 3-4节 向礼琴 实验大楼C座5楼"),
    (3, "人工智能导论 1-8周 9-10节 崔明轩 教西A-305"),
    (4, "数据库系统 1-16周 11-12节 唐伯涛 计南B-206"),
]

# 平面表：列名 + 每行各列的值（顺序与 TABLE_COLS 对应）
TABLE_COLS = [("课程名称", 60), ("教师", 300), ("星期", 380), ("节次", 470),
              ("周次", 560), ("上课地点", 680)]
TABLE_ROWS = [
    ("高等数学A(上)", "陈明远", "星期一", "1-2节", "1-16周", "教西D-101"),
    ("大学英语", "王小明", "星期二", "3-4节", "1-16周", "友谊西外A-302"),
    ("概率论", "高翔", "星期三", "5-6节", "2-10(双)", "教西C-205"),
    ("体育", "张伟", "星期四", "7-8节", "1,3,5", "翱翔体育馆"),
    ("操作系统", "向礼琴", "星期五", "9-10节", "1-16周", "实验大楼C座5楼"),
    ("人工智能导论", "崔明轩", "星期一", "11-12节", "1-8周", "教西A-305"),
    ("数据结构", "李华", "星期二", "1-2节", "1-16周", "教西A-201"),
    ("数据库系统", "唐伯涛", "星期三", "3-4节", "1-16周", "计南B-206"),
]

# 期望值（不是从解析结果抄的，是照上面表格手写的）
INLINE_EXPECT = {
    ("周一", "高等数学A"): (1, 2, "教西D-101", "陈明远"),
    ("周一", "大学英语"): (3, 4, "友谊西外A-302", "王小明"),
    ("周一", "数据结构"): (7, 8, "教西A-201", None),      # 单双周合并 → 两位老师
    ("周二", "大学物理"): (1, 2, "教西C-202", "孙雅雯"),
    ("周二", "体育"): (5, 6, "翱翔体育馆", "张伟"),
    ("周三", "操作系统"): (3, 4, "实验大楼C座5楼", "向礼琴"),
    ("周四", "人工智能导论"): (9, 10, "教西A-305", "崔明轩"),
    ("周五", "数据库系统"): (11, 12, "计南B-206", "唐伯涛"),
}
TABLE_EXPECT = {
    ("周一", "高等数学A(上)"): ("1~16", (1, 2), "教西D-101", "陈明远"),
    ("周二", "大学英语"): ("1~16", (3, 4), "友谊西外A-302", "王小明"),
    ("周三", "概率论"): ("2、4、6、8、10", (5, 6), "教西C-205", "高翔"),
    ("周四", "体育"): ("1、3、5", (7, 8), "翱翔体育馆", "张伟"),
    ("周五", "操作系统"): ("1~16", (9, 10), "实验大楼C座5楼", "向礼琴"),
    ("周一", "人工智能导论"): ("1~8", (11, 12), "教西A-305", "崔明轩"),
    ("周二", "数据结构"): ("1~16", (1, 2), "教西A-201", "李华"),
    ("周三", "数据库系统"): ("1~16", (3, 4), "计南B-206", "唐伯涛"),
}


def _put(page, x, y, text, size=7.0):
    page.insert_text((x, y), text, fontname="china-s", fontsize=size)


def build_inline(path):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    # 元信息：inline 版式用默认 meta_labels（"姓名：" 这类带冒号的标签）
    _put(page, 40, 40, "2026-2027 秋 学生课表", 10.0)
    _put(page, 40, 54, "姓名：张三  学号：2024123456  学院：计算机学院", 8.0)
    for i, name in enumerate(DAY_NAMES):
        _put(page, COL_X[i] - 18, 76, name, 9.0)
    for i in range(7):
        y = 92.0
        for day, text in INLINE_ROWS:
            if day != i:
                continue
            _put(page, COL_X[i] - 50, y, text)
            y += 10.0
    doc.save(path)
    doc.close()
    return path


def build_table(path):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    _put(page, 40, 44, "2026-2027 秋 学生课表总课表", 10.0)
    _put(page, 40, 60, "姓名 张三   学号 2024123456", 8.0)
    for name, x in TABLE_COLS:
        _put(page, x, 100, name, 8.5)
    y = 120.0
    for row in TABLE_ROWS:
        for (_name, x), val in zip(TABLE_COLS, row):
            _put(page, x, y, val, 7.5)
        y += 16.0
    doc.save(path)
    doc.close()
    return path


# ---------------------------------------------------------------- 断言
class Fail(Exception):
    pass


def expect(cond, msg):
    if not cond:
        raise Fail(msg)


def check_inline(path):
    lay = lay_mod.REGISTRY.get("inline-grid")
    res = parse_pdf(path, layout=lay)
    chk = verify_parse(res)
    print("  [inline] 记录 %d 条 / 冲突 %d / warnings %r / notes %r"
          % (len(res.records), len(chk["conflicts"]), res.warnings, res.notes))
    for r in res.records:
        print("      %s %d-%d节 %-14s 第%-9s %-14s %s"
              % (r.day_name, r.pfrom, r.pto, r.course,
                 compress_weeks(r.all_weeks()), r.room, "；".join(r.teacher_desc())))

    expect(res.warnings == [], "warnings 非空：%r" % (res.warnings,))
    expect(not chk["conflicts"], "有时间冲突：%r" % (chk["conflicts"][:3],))
    # 单双周那两行同课名同格，应当合并成 1 条 2 段，所以 9 行 -> 8 条
    expect(len(res.records) == 8, "记录数应为 8（9 行里单双周合并成 1 条），实际 %d"
           % len(res.records))
    expect(res.meta.name == "张三" and res.meta.sid == "2024123456",
           "元信息没抓到：name=%r sid=%r" % (res.meta.name, res.meta.sid))
    expect(res.meta.term == "2026-2027 秋", "学期没抓到：%r" % (res.meta.term,))

    got = {(r.day_name, r.course): r for r in res.records}
    for key, (pf, pt, room, teacher) in INLINE_EXPECT.items():
        expect(key in got, "缺了 %r（实际有：%s）"
               % (key, "、".join("%s/%s" % k for k in got)))
        r = got[key]
        expect((r.pfrom, r.pto) == (pf, pt),
               "%r 节次应为 %d-%d，实际 %d-%d" % (key, pf, pt, r.pfrom, r.pto))
        expect(r.room == room, "%r 教室应为 %r，实际 %r" % (key, room, r.room))
        if teacher:
            expect(teacher in r.teacher_desc(),
                   "%r 教师里应有 %r，实际 %r" % (key, teacher, r.teacher_desc()))

    ds = got[("周一", "数据结构")]
    expect(len(ds.segments) == 2, "单双周应合并成 2 段，实际 %d 段" % len(ds.segments))
    expect(compress_weeks(ds.all_weeks()) == "1~16",
           "单双周合并后周次应为 1~16，实际 %r" % compress_weeks(ds.all_weeks()))
    expect(all(any(c.isdigit() for c in s.teacher) or s.teacher in ("李华", "王强")
               for s in ds.segments),
           "两段教师应能在提示里区分出来：%r" % [s.teacher for s in ds.segments])
    print("  单双周：%s" % ds.teacher_desc())


def check_table(path):
    lay = lay_mod.REGISTRY.get("flat-table")
    res = parse_pdf(path, layout=lay)
    chk = verify_parse(res)
    print("  [table] 记录 %d 条 / 冲突 %d / warnings %r"
          % (len(res.records), len(chk["conflicts"]), res.warnings))
    for r in res.records:
        print("      %s %d-%d节 %-14s 第%-14s %-14s %s"
              % (r.day_name, r.pfrom, r.pto, r.course,
                 compress_weeks(r.all_weeks()), r.room, "；".join(r.teacher_desc())))

    expect(res.warnings == [], "warnings 非空：%r（说明又有周次写法没认出来）"
           % (res.warnings,))
    expect(not chk["conflicts"], "有时间冲突：%r" % (chk["conflicts"][:3],))
    expect(len(res.records) == 8, "记录数应为 8，实际 %d" % len(res.records))
    expect(res.meta.term == "2026-2027 秋", "学期没抓到：%r" % (res.meta.term,))

    got = {(r.day_name, r.course): r for r in res.records}
    for key, (weeks, (pf, pt), room, teacher) in TABLE_EXPECT.items():
        expect(key in got, "缺了 %r（实际有：%s）"
               % (key, "、".join("%s/%s" % k for k in got)))
        r = got[key]
        expect(compress_weeks(r.all_weeks()) == weeks,
               "%r 周次应为 %r，实际 %r" % (key, weeks, compress_weeks(r.all_weeks())))
        expect((r.pfrom, r.pto) == (pf, pt),
               "%r 节次应为 %d-%d，实际 %d-%d" % (key, pf, pt, r.pfrom, r.pto))
        expect(r.room == room, "%r 教室应为 %r，实际 %r" % (key, room, r.room))
        expect(teacher in r.teacher_desc(),
               "%r 教师里应有 %r，实际 %r" % (key, teacher, r.teacher_desc()))


def main(argv):
    do_check = "--check" in argv
    if not os.path.isdir(FIXDIR):
        os.makedirs(FIXDIR)
    i = build_inline(INLINE_OUT)
    print("已生成：%s" % i)
    t = build_table(TABLE_OUT)
    print("已生成：%s" % t)
    if not do_check:
        print("（加 --check 跑解析断言）")
        return 0

    ok = True
    for label, fn, path in (("inline-grid", check_inline, i), ("flat-table", check_table, t)):
        print("\n== %s ==" % label)
        try:
            fn(path)
        except Fail as exc:
            ok = False
            print("  [FAIL] %s" % exc)
    print("")
    if ok:
        print("--check 通过：两种版式都解析正确")
        return 0
    print("--check 失败：见上面的 FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))