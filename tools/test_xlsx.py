# -*- coding: utf-8 -*-
"""Excel / CSV 课表读写的回归测试。

    python tools\\test_xlsx.py

覆盖什么：
  * 模板能导出、且导出的模板能被自己读回来（不然用户「照模板填」就会踩空）；
  * 列名别名（课程名/科目、教室/地点…）都认；
  * 表头不在第一行也认（真实导出常带标题行）；
  * 星期/节次/周次的各种写法；
  * **导出→导入往返一致**：把解析结果导出成 Excel 再读回来，记录必须完全相同 ——
    这是「PDF 解析 → 导出核对 → 改完导回」这条闭环成立的前提；
  * 出错时必须指出**第几行、哪一列、为什么**，而不是笼统失败。
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from timetable.parser import compress_weeks                      # noqa: E402
from timetable.xlsx_source import (COLUMNS, XlsxError, read_xlsx,  # noqa: E402
                                   write_xlsx)

ok_all = True
TMP = tempfile.mkdtemp(prefix="xlsx-test-")


def check(label, cond, extra=""):
    global ok_all
    if not cond:
        ok_all = False
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + str(extra)) if extra else ""))
    return cond


def expect_error(fn, must, label):
    try:
        fn()
    except XlsxError as exc:
        return check(label, must in str(exc), str(exc).splitlines()[0][:80])
    except Exception as exc:                                   # noqa: BLE001
        return check(label, False, "抛的是 %s：%s" % (type(exc).__name__, exc))
    return check(label, False, "没报错")


def write_sheet(path, rows, sheet_title="课表"):
    """直接写一个 xlsx（模拟用户自己填的表）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


print("[1] 模板：导出 -> 读回")
tpl = os.path.join(TMP, "模板.xlsx")
write_xlsx(tpl)
check("模板文件已生成", os.path.isfile(tpl) and os.path.getsize(tpl) > 3000,
      os.path.getsize(tpl) if os.path.isfile(tpl) else 0)
res = read_xlsx(tpl)
check("模板里的示例行能被读出来（2 条）", len(res.records) == 2, len(res.records))
check("示例课程名正确", res.records[0].course == "高等数学",
      res.records[0].course)
check("示例的星期/节次/周次都对",
      (res.records[0].day, res.records[0].pfrom, res.records[0].pto) == (0, 1, 2)
      and compress_weeks(res.records[0].all_weeks()) == "1~16",
      (res.records[0].day, res.records[0].pfrom, res.records[0].pto,
       compress_weeks(res.records[0].all_weeks())))
check("第二条是双周课", compress_weeks(res.records[1].all_weeks())
      == "2、4、6、8、10、12、14、16", compress_weeks(res.records[1].all_weeks()))
check("教室含「楼/座」也能读（实验楼C座5楼）",
      res.records[1].room == "实验楼C座5楼", res.records[1].room)
check("notes 里说明了来源与表头行", any("表头在第" in n for n in res.notes), res.notes)

print("\n[2] 列名别名与表头位置")
p = write_sheet(os.path.join(TMP, "别名.xlsx"), [
    ["某某大学 2026-2027-1 学生课表"],          # 标题行，不是表头
    ["制表日期", "2026/8/1"],
    ["科目", "编号", "周几", "上课节次", "上课周次", "老师", "地点"],
    ["线性代数", "MATH01", "周二", "3-4节", "1-8周", "王五", "教二101"],
    ["大学物理", "", "5", "第5-6节", "1,3,5", "赵六/钱七", "实验楼C座5楼"],
])
res2 = read_xlsx(p)
check("表头在第 3 行也能找到", len(res2.records) == 2, len(res2.records))
check("「科目/编号/周几/上课节次/上课周次/老师/地点」这些别名都认",
      res2.records[0].course == "线性代数" and res2.records[0].code == "MATH01"
      and res2.records[0].day == 1
      and "王五" in res2.records[0].teacher_desc(),
      (res2.records[0].course, res2.records[0].code, res2.records[0].day,
       res2.records[0].teacher_desc()))
check("星期写数字 5 -> 星期五", res2.records[1].day == 4, res2.records[1].day)
check("「第5-6节」这种写法也认",
      (res2.records[1].pfrom, res2.records[1].pto) == (5, 6),
      (res2.records[1].pfrom, res2.records[1].pto))
check("多教师用 / 分隔原样保留",
      any("/" in t for t in res2.records[1].teacher_desc()),
      res2.records[1].teacher_desc())
check("周次写「1,3,5」", compress_weeks(res2.records[1].all_weeks()) == "1、3、5",
      compress_weeks(res2.records[1].all_weeks()))

print("\n[3] 导出 -> 导入 往返一致")
src = res2
out = os.path.join(TMP, "导出.xlsx")
write_xlsx(out, src.records, include_help=False)
back = read_xlsx(out)


def sig(records):
    out = []
    for r in records:
        out.append((r.course, r.code, r.day, r.pfrom, r.pto, r.room,
                    tuple(sorted((tuple(s.weeks), s.teacher) for s in r.segments))))
    return sorted(out)


check("往返后记录数不变", len(back.records) == len(src.records),
      "%d -> %d" % (len(src.records), len(back.records)))
check("往返后每条记录逐字段相同（课程/代码/星期/节次/教室/周次+教师分段）",
      sig(back.records) == sig(src.records),
      "" if sig(back.records) == sig(src.records)
      else "差异：%r" % [x for x in sig(back.records) if x not in sig(src.records)][:2])

print("\n[4] 出错必须指明位置与原因")
bad = write_sheet(os.path.join(TMP, "坏表.xlsx"), [
    ["课程名称", "星期", "节次", "周次"],
    ["高等数学", "星期一", "1-2", "1-16"],
    ["", "星期二", "3-4", "1-16"],              # 缺课程名
    ["大学物理", "星期八", "5-6", "1-16"],       # 星期认不出
    ["线性代数", "星期三", "一二", "1-16"],      # 节次认不出
    ["概率论", "星期四", "7-8", "开学到期末"],   # 周次认不出
])
res3 = read_xlsx(bad)
check("能用的那行读出来了", len(res3.records) == 1, len(res3.records))
joined = " | ".join(res3.warnings)
check("缺课程名指到第 3 行", "第 3 行" in joined, joined[:60])
check("星期认不出指到第 4 行且说明怎么写",
      "第 4 行" in joined and "星期一" in joined, joined[:90])
check("节次认不出指到第 5 行", "第 5 行" in joined, joined[:90])
check("周次认不出指到第 6 行", "第 6 行" in joined, joined[:90])

expect_error(lambda: read_xlsx(
    write_sheet(os.path.join(TMP, "无表头.xlsx"), [["随便", "写点", "东西"]])),
    "没找到表头行", "没有表头行要明确报错")

expect_error(lambda: read_xlsx(os.path.join(TMP, "不存在.xlsx")),
             "打不开", "文件不存在要说清楚")

expect_error(lambda: read_xlsx(write_sheet(os.path.join(TMP, "空表.xlsx"), [
    ["课程名称", "星期", "节次", "周次"]])), "一条能用的课都没有",
    "只有表头没有数据时要说明白")

print("\n[5] CSV 也能读（Excel 另存 CSV 的常见编码）")
csv_path = os.path.join(TMP, "课表.csv")
import csv as _csv
import io as _io
with _io.open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    w = _csv.writer(f)
    w.writerow([n for _, n in COLUMNS])
    w.writerow(["大学英语", "", "星期一", "1-2", "1-16", "张三", "教三201"])
res4 = read_xlsx(csv_path)
check("UTF-8-BOM 的 CSV 能读", len(res4.records) == 1, len(res4.records))
# GBK 编码（Excel「另存为 CSV」在中文 Windows 上的默认）
gbk_path = os.path.join(TMP, "课表-gbk.csv")
with _io.open(gbk_path, "w", encoding="gbk", newline="") as f:
    w = _csv.writer(f)
    w.writerow([n for _, n in COLUMNS])
    w.writerow(["大学英语", "", "星期一", "1-2", "1-16", "张三", "教三201"])
res5 = read_xlsx(gbk_path)
check("GBK 的 CSV 也能读", len(res5.records) == 1
      and res5.records[0].course == "大学英语", len(res5.records))

print("\n" + "=" * 74)
print("全部通过" if ok_all else "存在失败项")
sys.exit(0 if ok_all else 1)