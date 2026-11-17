# -*- coding: utf-8 -*-
"""Excel（.xlsx / .csv）课表：读入、导出、模板。

为什么要有这一层
    它是这套工具**门槛最低、结果最确定**的输入方式：
      * 没有 PDF 的坐标/字号/跨列串行问题，单元格就是单元格；
      * 用户可以在 Excel 里直接改，改完导回来 —— 这是「PDF 解析 → 导出 Excel →
        人工核对修改 → 再导入」这条闭环的基础，也是 PDF/OCR 都失败时的兜底；
      * 出错的定位精确到「第 7 行 D 列」，用户能自己修，不像 PDF 只能整份重来。

设计取舍
    * 列名不写死：复用 parser.TABLE_ROLES 的中文别名，用户表头叫「课程名」「科目」
      都认；认不出来才报错并列出「我认得哪些名字」。
    * 表头行不必在第 1 行：前面可以有几行标题/元信息（真实导出常见），会在前 10 行里找。
    * 每行一条课。一门课多段（不同周次/节次/教室）就写多行 —— 与 PDF 解析结果的
      组织结构一致，导出再导入能完全还原。
    * **不猜**：缺课程名/星期/节次/周次的行走 warnings 并跳过，报错里带行号列名。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
from typing import Dict, List, Optional, Tuple

from .layout import DAY_ALIASES, LayoutError, expand_weeks_token
from .parser import (TABLE_ROLES, Meta, ParseResult, Record, Segment,
                     _classify_header, compress_weeks)

# 导出/模板用的规范列（顺序也是给人看的顺序）
COLUMNS = [
    ("course", "课程名称"),
    ("code", "课程代码"),
    ("day", "星期"),
    ("period", "节次"),
    ("weeks", "周次"),
    ("teacher", "教师"),
    ("room", "上课地点"),
]

WEEKDAY_OUT = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

HELP_SHEET_NAME = "填写说明"

HELP_LINES = [
    ("怎么用", ""),
    ("1", "在「课表」工作表里一行写一条课；一门课有多个时间段就写多行。"),
    ("2", "表头名字不用和示例完全一致，程序认这些别名："),
    ("", "    课程名称 / 课程名 / 课程 / 科目"),
    ("", "    课程代码 / 代码 / 编号"),
    ("", "    星期 / 周几 / 上课星期"),
    ("", "    节次 / 节 / 上课节次 / 时段"),
    ("", "    周次 / 起止周 / 上课周次"),
    ("", "    教师 / 任课教师 / 老师"),
    ("", "    上课地点 / 教室 / 地点 / 场地"),
    ("3", "星期可以写「星期一」「周一」或数字 1-7。"),
    ("4", "节次写「1-2」「1-2节」「3」都行；不连续的（如 1,3）请拆成两行。"),
    ("5", "周次写「1-16」「1-16周」「2-10(双)」「1,3,5」都可以；"
          "注意是**第几周**，不是日期。"),
    ("6", "教师多位用「/」或「、」分隔，如「张三/李四」。"),
    ("7", "整行留空的会被忽略；只填了一半的行会在导入时指出是哪一行缺什么。"),
    ("", ""),
    ("注意", "这里填的是「第几周」，具体哪天上日历还要在程序里填「第 1 周周一」。"),
    ("注意", "节次对应的上下课时间用程序里的「节次时间…」设定（或 --period-times）。"),
]


class XlsxError(Exception):
    """Excel 输入有问题。message 要能让用户直接改好表：带行号、列名、为什么。"""


def _openpyxl():
    try:
        import openpyxl
    except ImportError as exc:                     # pragma: no cover
        raise XlsxError(
            "缺少依赖 openpyxl（读 Excel 需要它）。\n"
            "  处理办法：pip install openpyxl，或改用 CSV 格式（Excel 里「另存为 CSV」）。"
        ) from exc
    return openpyxl


# ------------------------------------------------------------------ 单元格归一
def _cell_text(v) -> str:
    """把单元格值归一成字符串。Excel 里可能是数字、日期、None。"""
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))          # 1.0 -> "1"，避免"第1.0节"
    return str(v).strip()


def _parse_day(text: str) -> Optional[int]:
    """「星期一」/「周一」/「1」/「周一(单)」 -> 0..6。"""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return None
    for i, aliases in enumerate(DAY_ALIASES):
        for a in aliases:
            if t == a or t.startswith(a):
                return i
    m = re.search(r"[1-7]", t)
    if m:
        return int(m.group(0)) - 1
    m = re.search(r"[一二三四五六日天]", t)
    if m:
        return {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
                "日": 6, "天": 6}[m.group(0)]
    return None


def _parse_periods(text: str) -> Tuple[Optional[int], Optional[int]]:
    """「1-2」「1-2节」「第1-2节」「3」 -> (1,2) / (3,3)。不连续返回 (None,None)。"""
    t = re.sub(r"\s+", "", text or "")
    t = re.sub(r"^第", "", t)
    t = re.sub(r"节$", "", t)
    if not t:
        return None, None
    m = re.fullmatch(r"(\d{1,2})\s*[-~—–至]\s*(\d{1,2})", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (a, b) if a <= b else (b, a)
    m = re.fullmatch(r"(\d{1,2})", t)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


def _parse_weeks(text: str) -> List[int]:
    """周次：交给 layout.expand_weeks_token（它已支持 1-16 / 2-10(双) / 1,3,5 / 第3周）。"""
    t = (text or "").strip()
    if not t:
        return []
    # 允许只写数字区间，没有「周」字
    try:
        return expand_weeks_token(t)
    except LayoutError:
        return []


# --------------------------------------------------------------------- 读入
def _find_header_row(rows: List[List[str]], max_scan: int = 10):
    """在前几行里找表头，返回 (行号, {role: 列号})。"""
    for ri, row in enumerate(rows[:max_scan]):
        found: Dict[str, int] = {}
        for ci, cell in enumerate(row):
            role = _classify_header(cell)
            if role and role not in found:
                found[role] = ci
        if "course" in found and len(found) >= 3:
            return ri, found
    return None, {}


def read_xlsx(path: str) -> ParseResult:
    """读 Excel/CSV 课表。返回 ParseResult（records + warnings + meta 里的来源说明）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        rows = _read_csv_rows(path)
    else:
        rows = _read_xlsx_rows(path)
    if not rows:
        raise XlsxError("这份表格是空的（没读到任何行）：%s" % path)

    hdr_i, cols = _find_header_row(rows)
    if hdr_i is None:
        raise XlsxError(
            "没找到表头行（前 10 行里至少有「课程」列 + 另外两列）。\n"
            "  我认得的列名：%s\n"
            "  第一条建议：用 --xlsx-template 导出一份模板，照着模板的列名填。"
            % "、".join(n for names in TABLE_ROLES.values() for n in names[:3]))

    result = ParseResult(layout_id="xlsx")
    result.notes.append("来源：%s（表头在第 %d 行）" % (os.path.basename(path), hdr_i + 1))
    result.meta = Meta()

    for ri in range(hdr_i + 1, len(rows)):
        row = rows[ri]
        line_no = ri + 1                       # 给用户看的行号从 1 开始
        if not any((c or "").strip() for c in row):
            continue                            # 整行空 -> 跳过
        get = lambda role: _cell_text(row[cols[role]]) if role in cols and cols[role] < len(row) else ""  # noqa: E731

        course = re.sub(r"\s+", " ", get("course")).strip()
        if not course:
            result.warnings.append("第 %d 行没有课程名，已跳过。" % line_no)
            continue
        day = _parse_day(get("day"))
        if day is None:
            result.warnings.append(
                "第 %d 行「%s」的星期认不出来（%r），已跳过；请写「星期一」或 1-7。"
                % (line_no, course, get("day")))
            continue
        pfrom, pto = _parse_periods(get("period"))
        if pfrom is None:
            result.warnings.append(
                "第 %d 行「%s」的节次认不出来（%r），已跳过；例：1-2 或 3。"
                % (line_no, course, get("period")))
            continue
        weeks = _parse_weeks(get("weeks"))
        if not weeks:
            result.warnings.append(
                "第 %d 行「%s」的周次认不出来（%r），已跳过；例：1-16、(双)2-16、1,3,5。"
                % (line_no, course, get("weeks")))
            continue

        code = get("code")
        teacher = re.sub(r"\s+", "", get("teacher"))
        room = re.sub(r"\s+", "", get("room"))
        result.records.append(Record(
            day=day, course=course, code=code, pfrom=pfrom, pto=pto, room=room,
            segments=[Segment(weeks=weeks, teacher=teacher)]))

    if not result.records:
        raise XlsxError(
            "表读到了，但一条能用的课都没有。%s\n"
            "  第一条建议：用 --xlsx-template 导出模板，照着填；"
            "或把 --check 输出的警告贴出来看。"
            % (("、".join(result.warnings[:3]) + "…") if result.warnings else ""))

    # 合并「同一天同节次同课程同教室」的多行（用户常把一门课拆成几行写）
    result.records = _merge_same_slots(result.records)
    return result


def _merge_same_slots(records: List[Record]) -> List[Record]:
    out: Dict[str, Record] = {}
    order: List[str] = []
    for r in records:
        k = r.key()
        if k not in out:
            out[k] = r
            order.append(k)
            continue
        tgt = out[k]
        have = {(tuple(s.weeks), s.teacher) for s in tgt.segments}
        for s in r.segments:
            if (tuple(s.weeks), s.teacher) not in have:
                tgt.segments.append(s)
    return [out[k] for k in order]


def _read_xlsx_rows(path: str) -> List[List[str]]:
    openpyxl = _openpyxl()
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:                        # noqa: BLE001
        raise XlsxError("打不开这个 Excel：%s（%s）" % (path, exc)) from exc
    # 优先用叫「课表」的工作表，否则第一张
    ws = wb["课表"] if "课表" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = [[_cell_text(c) for c in row] for row in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _read_csv_rows(path: str) -> List[List[str]]:
    for enc in ("utf-8-sig", "gbk"):                # Excel 另存 CSV 常是 GBK
        try:
            with io.open(path, encoding=enc, newline="") as f:
                return [[c.strip() for c in row] for row in csv.reader(f)]
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise XlsxError("读不到这个 CSV：%s（%s）" % (path, exc)) from exc
    raise XlsxError("这个 CSV 既不是 UTF-8 也不是 GBK 编码，请另存为 UTF-8：" + path)


# --------------------------------------------------------------------- 写出
def _records_to_rows(records: List[Record]) -> List[List[str]]:
    rows = []
    for r in records:
        for seg in (r.segments or [Segment(weeks=[])]):
            rows.append([
                r.course,
                r.code or "",
                WEEKDAY_OUT[r.day],
                ("%d-%d" % (r.pfrom, r.pto)) if r.has_periods() else "",
                compress_weeks(seg.weeks),
                seg.teacher or "",
                r.room or "",
            ])
    rows.sort(key=lambda x: (WEEKDAY_OUT.index(x[2]) if x[2] in WEEKDAY_OUT else 9,
                             x[3]))
    return rows


def write_xlsx(path: str, records: Optional[List[Record]] = None,
               include_help: bool = True) -> str:
    """写出 Excel。records=None 就写一份**空模板**（带表头、示例行、填写说明）。

    返回实际写入的路径。csv 后缀会写成 CSV（不带说明页）。
    """
    ext = os.path.splitext(path)[1].lower()
    hdr = [name for _, name in COLUMNS]
    body = _records_to_rows(records or [])

    if ext == ".csv":
        with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(hdr)
            for row in body:
                w.writerow(row)
        return path

    openpyxl = _openpyxl()
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "课表"
    ws.append(hdr)
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="4F46E5")
    for c in ws[1]:
        c.font = head_font
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center")
    widths = {"课程名称": 24, "课程代码": 10, "星期": 9, "节次": 8,
              "周次": 12, "教师": 12, "上课地点": 18}
    for i, (_, name) in enumerate(COLUMNS, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = \
            widths.get(name, 12)

    if body:
        for row in body:
            ws.append(row)
    else:
        # 空模板给两行示例，并明确标注「示例，导出后可删」
        ws.append(["高等数学", "0123", "星期一", "1-2", "1-16", "张三", "教三楼201"])
        ws.append(["大学物理", "", "星期三", "3-4", "2-16(双)", "李四", "实验楼C座5楼"])
        for c in ws[2]:
            c.font = Font(color="999999", italic=True)
        for c in ws[3]:
            c.font = Font(color="999999", italic=True)

    if include_help:
        hs = wb.create_sheet(HELP_SHEET_NAME)
        hs.column_dimensions["A"].width = 10
        hs.column_dimensions["B"].width = 88
        for a, b in HELP_LINES:
            hs.append([a, b])
        hs["A1"].font = Font(bold=True)
    wb.save(path)
    return path


def read_any(path: str) -> ParseResult:
    """按扩展名把 .xlsx/.csv 读成 ParseResult（供 CLI/GUI 统一入口用）。"""
    return read_xlsx(path)


def is_table_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".xlsx", ".xlsm", ".csv")