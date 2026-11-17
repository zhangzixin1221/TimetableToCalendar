# -*- coding: utf-8 -*-
"""把教务系统导出的《学生课表》PDF 解析成结构化数据。

版式相关的东西全在 `layout.py`（表头叫什么、周次/节次怎么写、教室教师怎么切、
元信息标签是什么、作息预设是哪几套）。本模块只负责「怎么从版面坐标里把文字捞成
课程记录」这套与学校无关的几何 + 分类逻辑。

核心思路（这一步是整个程序可靠性的关键）：
  课表是一个「星期 × 节次」的网格，同一段文字在不同列里的字号可能不同，导致同一
  视觉行的 y 坐标有几像素差异。因此必须先按 x 把每个文本块归到某一列，**再在列内
  纵向成行**；否则跨列的同一水平行会被错误合并，文字就会串到错误的星期下面。

每个条目的处理分两步，避免「续行」拼错：
  1. 先把列内各行分类 —— 能匹配「(周次)(节次)」的算正文行，字号更小或含说明性词
     语的算说明行，其余算上一行的换行续行；
  2. 续行拼接完成后再统一把正文行拆成「教室 + 教师」，并按 (节次起, 节次止, 教室)
     归组，这样被换行截断的教师名能正确补全。

三种 entry_mode（由版式决定，见 layout.py）：
  block   格子里先一行标题、下面几行详情（西工大）
  inline  格子里每行自带课程名和周次节次
  table   不是网格，是一行一条课的平面表（正方/强智的课表列表）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .layout import (BUILTIN, DEFAULT_LAYOUT_ID, Layout, LayoutError, REGISTRY,
                     expand_weeks_token, split_room_teacher)

WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
WEEKDAY_SHORT = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 兼容保留：以前这几个常量直接写在本模块里，外部脚本（tools/、自己写的调试片段）
# 会 import 它们。值就是西工大版式的那几条正则，行为没有变化。
TITLE_RE = re.compile(BUILTIN[DEFAULT_LAYOUT_ID].title_re)
WEEKS_RE = re.compile(BUILTIN[DEFAULT_LAYOUT_ID].weeks_re)
PERIOD_RE = re.compile(BUILTIN[DEFAULT_LAYOUT_ID].period_re)
REMARK_RE = re.compile(BUILTIN[DEFAULT_LAYOUT_ID].remark_re)
CODELIST_RE = re.compile(BUILTIN[DEFAULT_LAYOUT_ID].codelist_re)
NOTE_WORD_RE = re.compile(BUILTIN[DEFAULT_LAYOUT_ID].note_words_re)


@dataclass
class Segment:
    """「周次 + 教师」的组合：同一门课不同周次可能换老师。

    （以前这里还有个 raw 字段存原始文字，但全项目只写不读 —— 要看原文用
    `--dump-layout`，它直接从解析中间结果打，不必让每条记录都背着一份冗余文本。）
    """
    weeks: List[int]
    teacher: str = ""

    def weeks_text(self) -> str:
        return compress_weeks(self.weeks)


@dataclass
class Record:
    day: int                    # 0=周一 … 6=周日
    course: str
    code: str
    pfrom: int
    pto: int
    room: str
    segments: List[Segment] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # 版式直接印了上下课时刻时记在这里（如 08:00-09:40）；有它就优先于作息表
    clock: Tuple[str, str] | None = None

    @property
    def day_name(self) -> str:
        return WEEKDAY_SHORT[self.day]

    def all_weeks(self) -> List[int]:
        out = set()
        for s in self.segments:
            out.update(s.weeks)
        return sorted(out)

    def teacher_for(self, week: int) -> str:
        """取该周的教师。若多条 segment 都覆盖这一周（例如「1~16周 李春」和
        「16周 郑军超」），取**周次范围最窄**的那条 —— 那是更具体的安排。"""
        best, best_n = "", None
        for s in self.segments:
            if week in s.weeks and s.teacher:
                if best_n is None or len(s.weeks) < best_n:
                    best, best_n = s.teacher, len(s.weeks)
        return best

    def teacher_desc(self) -> List[str]:
        if not self.segments:
            return []
        if len(self.segments) == 1:
            return [self.segments[0].teacher] if self.segments[0].teacher else []
        by_teacher: Dict[str, List[int]] = {}
        for s in self.segments:
            if s.teacher:
                by_teacher.setdefault(s.teacher, []).extend(s.weeks)
        if len(by_teacher) == 1:
            return [next(iter(by_teacher))]
        return ["%s：%s" % (compress_weeks(sorted(w)), t)
                for t, w in sorted(by_teacher.items(), key=lambda kv: min(kv[1]))]

    def key(self) -> str:
        return "%s|%s|%d|%d|%s" % (self.course, self.day_name, self.pfrom, self.pto,
                                   self.room)


@dataclass
class Meta:
    grade: str = ""
    college: str = ""
    major: str = ""
    klass: str = ""
    sid: str = ""
    name: str = ""
    term: str = ""
    print_date: str = ""
    total_credits: str = ""
    remark_courses: List[dict] = field(default_factory=list)

    def calendar_name(self) -> str:
        bits = [b for b in (self.term, self.name) if b]
        base = "课表"
        if bits:
            base += "（%s）" % " ".join(bits)
        return base


@dataclass
class ParseResult:
    records: List[Record] = field(default_factory=list)
    meta: Meta = field(default_factory=Meta)
    # warnings 只放**真出了问题的**条目（某门课没解析出周次、串行等）。
    # 以前「PDF 共 2 页」这种纯说明也塞在这里，结果 selftest 把「有 warnings」当成
    # 解析失败，多页课表必然误报。说明性的话放 notes，别污染 warnings 的判据意义。
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    layout_id: str = ""


# --------------------------------------------------------------------- helpers
def compress_weeks(weeks: List[int]) -> str:
    """[1,2,3,5] -> '1~3、5'"""
    ws = sorted(set(weeks))
    if not ws:
        return ""
    runs, start, prev = [], ws[0], ws[0]
    for w in ws[1:]:
        if w == prev + 1:
            prev = w
            continue
        runs.append((start, prev))
        start = prev = w
    runs.append((start, prev))
    return "、".join(("%d" % a) if a == b else ("%d~%d" % (a, b)) for a, b in runs)


def expand_weeks(a: int, b: Optional[int], parity: Optional[str]) -> List[int]:
    end = b if b is not None else a
    weeks = list(range(a, end + 1))
    if parity == "单":
        weeks = [w for w in weeks if w % 2 == 1]
    elif parity == "双":
        weeks = [w for w in weeks if w % 2 == 0]
    return weeks


# ---------------------------------------------------------------- 版面几何
def _collect_spans(page):
    d = page.get_text("dict")
    spans = []
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            for sp in ln["spans"]:
                if sp["text"] == "":
                    continue
                # 注意：纯空白的 span 必须保留 —— 它是「教室 / 教师」之间的分隔符
                # （例如 "C座5楼" + " " + "向礼琴"），丢掉就会把教师并进教室名。
                x0, y0, x1, y1 = sp["bbox"]
                spans.append(dict(x0=x0, y0=y0, x1=x1, y1=y1,
                                  size=round(sp["size"], 2), text=sp["text"]))
    return spans


def _header_labels(lay: Layout) -> List[List[str]]:
    labels = lay.day_headers or []
    if len(labels) < lay.min_day_columns:
        labels = list(labels) + [list(x) for x in BUILTIN[DEFAULT_LAYOUT_ID].day_headers]
    return labels[:7]


def _find_header(spans, lay: Layout):
    """返回 (centres, header_bottom)；找不到表头返回 (None, None)。"""
    labels = _header_labels(lay)
    found: Dict[int, List[dict]] = {}
    for s in spans:
        t = s["text"].strip()
        for i, aliases in enumerate(labels):
            if t in aliases:
                found.setdefault(i, []).append(s)
                break
    need = max(1, min(lay.min_day_columns, 7))
    if len(found) < need:
        return None, None
    picked = []
    for i in range(need):
        if i not in found:
            return None, None
        picked.append(min(found[i], key=lambda z: z["y0"]))
    pairs = sorted([(s["x0"] + s["x1"]) / 2.0, i, s["y1"]] for i, s in enumerate(picked))
    # 表头文字可能不是按 周一…周日 从左到右排（有些学校周日起头），按 x 排完再还原列号
    order = [idx for _, idx, _ in pairs]
    if order != sorted(order):
        return None, None
    centres = [c for c, _, _ in pairs]
    return centres, max(y for _, _, y in pairs)


def _group_into_lines(items, tol: float) -> List[dict]:
    """同一列内按 y 聚成「视觉行」。必须先分列再走这里（见模块 docstring）。"""
    items = sorted(items, key=lambda z: (z["y0"], z["x0"]))
    lines: List[dict] = []
    for s in items:
        if lines and abs(s["y0"] - lines[-1]["ybase"]) <= tol:
            lines[-1]["spans"].append(s)
        else:
            lines.append(dict(ybase=s["y0"], spans=[s]))
    for ln in lines:
        ln["spans"].sort(key=lambda z: z["x0"])
        # 空白折叠成一个空格，再去掉首尾
        ln["text"] = re.sub(r"\s+", " ", "".join(z["text"] for z in ln["spans"])).strip()
        ln["y"] = min(z["y0"] for z in ln["spans"])
        ln["size"] = round(max(z["size"] for z in ln["spans"]), 2)
    lines.sort(key=lambda z: z["y"])
    return lines


# --------------------------------------------------------------------- parsing
def parse_pdf(pdf_path: str, log=None, layout: Optional[Layout] = None) -> ParseResult:
    """解析课表 PDF。`layout` 不传就是西工大版式（老行为，一字不变）。"""
    def say(msg):
        if log:
            log(msg)

    lay = layout or REGISTRY.get(DEFAULT_LAYOUT_ID)
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("缺少依赖 PyMuPDF，请先执行：pip install pymupdf") from exc

    rx = lay.compiled()
    doc = pymupdf.open(pdf_path)
    result = ParseResult(layout_id=lay.id)
    if doc.page_count > 1:
        result.notes.append("PDF 共 %d 页，将逐页解析并合并。" % doc.page_count)

    for pno in range(doc.page_count):
        page = doc[pno]
        spans = _collect_spans(page)
        if not spans:
            continue
        if lay.entry_mode == "table":
            recs = _parse_table_page(spans, lay, rx, result.warnings)
        else:
            recs = _parse_grid_page(spans, lay, rx, result, pno, result.warnings)
        result.records.extend(recs)
        if pno == 0:
            result.meta = _parse_meta(page, lay, rx)

    if not result.records:
        raise RuntimeError(
            "版式「%s」没有解析到任何课程。\n"
            "如果这份 PDF 是别的学校/别的打印入口导出的，换一种版式试试：--layout %s\n"
            "（先用 --dump-layout 看看程序眼里的这一页长什么样，再决定是选内置版式还是写一份。）"
            % (lay.name, "、".join(k for k in REGISTRY.ids() if k != lay.id)))

    result.records = _dedupe(result.records)
    say("版式：%s" % lay.name)
    say("解析到 %d 条课程安排。" % len(result.records))
    return result


def _parse_grid_page(spans, lay: Layout, rx: dict, result: ParseResult,
                     pno: int, warnings: List[str]) -> List[Record]:
    centres, header_bottom = _find_header(spans, lay)
    if centres is None:
        if pno == 0:
            raise RuntimeError(
                "没有找到「星期一…星期日」表头（版式「%s」），这大概不是教务系统导出的课表。"
                % lay.name)
        return []
    result.stats["header_found"] = result.stats.get("header_found", 0) + 1

    gaps = [centres[i + 1] - centres[i] for i in range(len(centres) - 1)]
    step = sorted(gaps)[len(gaps) // 2]
    bounds = [(centres[i] + centres[i + 1]) / 2.0 for i in range(len(centres) - 1)]
    col_left = centres[0] - step / 2.0

    body_bottom = max(s["y1"] for s in spans) + 1
    for s in spans:
        t = s["text"].strip()
        if rx["body_end_re"] and rx["body_end_re"].match(t):
            body_bottom = min(body_bottom, s["y0"] - 1.0)

    def col_of(x):
        for i, b in enumerate(bounds):
            if x < b:
                return i
        return len(centres) - 1

    body = []
    for s in spans:
        t = s["text"].strip()
        if s["x0"] < col_left or s["y0"] <= header_bottom + 1 or s["y0"] >= body_bottom:
            continue
        if rx["skip_text_re"] and rx["skip_text_re"].search(t):
            continue
        body.append(dict(s, col=col_of(s["x0"])))

    result.stats["body_spans"] = result.stats.get("body_spans", 0) + len(body)

    out: List[Record] = []
    for ci in range(len(centres)):
        lines = _group_into_lines([s for s in body if s["col"] == ci],
                                  lay.col_y_tolerance)
        if lay.entry_mode == "inline":
            out.extend(_parse_inline_column(ci, lines, lay, rx, warnings))
        else:
            out.extend(_parse_block_column(ci, lines, lay, rx, warnings))
    return out


def _is_note_line(text: str, size: float, entry_size: float, lay: Layout, rx: dict) -> bool:
    if size < entry_size - 0.15:
        return True
    if rx["codelist_re"] and rx["codelist_re"].match(text):
        return True
    if "周" not in text and rx["note_words_re"] and rx["note_words_re"].search(text):
        return True
    return False


def _parse_detail(text: str, lay: Layout, rx: dict, rest_of: str = "tail"):
    """从一行文字里抠出 (周次, 起节, 止节, 剩下的部分)；不是详情行返回 None。

    顺带把 `head`（周次/节次**之前**那一段）也带出来 —— 那基本就是课程名。
    以前想单独写一条正则去猜课程名，结果 `(?P<course>.*?)(可选后缀)` 这种
    「后缀全可选 + 懒惰匹配」只吃一个字符就宣告成功，课程名只剩一个字
    （合成 PDF 实测）。几何信息都已经在这了，没必要再猜一次。
    """
    if not (rx["weeks_re"] and rx["period_re"]):
        return None
    wm, pm = rx["weeks_re"].search(text), rx["period_re"].search(text)
    if not (wm and pm):
        return None
    if pm.start() < wm.end():
        # 节次出现在周次之前（"3-4节 1-16周"）时，rest 取两者的拼接之外那段
        rest = text[pm.end():] + " " + text[:wm.start()]
        head_end = min(pm.start(), wm.start())
    else:
        cut = max(wm.end(), pm.end())
        rest = text[cut:] if rest_of == "tail" else text[:wm.start()]
        head_end = min(wm.start(), pm.start())
    weeks = expand_weeks(int(wm.group("a")),
                         int(wm.group("b")) if wm.groupdict().get("b") else None,
                         wm.groupdict().get("parity"))
    return dict(weeks=weeks, pfrom=int(pm.group("pfrom")), pto=int(pm.group("pto")),
                head=re.sub(r"[\s:：,，、\-]+$", "", text[:head_end]).strip(),
                rest=re.sub(r"\s+", " ", rest).strip())


def _course_from_head(head: str, lay: Layout, rx: dict) -> Tuple[str, str]:
    """详情行开头那段 -> (课程名, 课程代码)。

    版式给了 `detail_course_re` 就用它拆代码（比如末尾的 `（20241234）`），
    不给就整段当课程名。正则里没写 `course` 分组时，把匹配到的那段从名字里
    **删掉**剩下的当名字 —— 这样写版式的人只需要关心"代码长什么样"。
    """
    head = (head or "").strip()
    if not head:
        return "", ""
    if not (lay.detail_course_re and rx["detail_course_re"]):
        return head, ""
    m = rx["detail_course_re"].search(head)
    if not m:
        return head, ""
    gd = m.groupdict()
    code = (gd.get("code") or "").strip()
    if gd.get("course"):
        course = gd["course"].strip()
    else:
        course = (head[:m.start()] + head[m.end():]).strip()
    return re.sub(r"[（(【\[]\s*$", "", course).strip(), code


# ---- block 模式：标题行 + 详情行（西工大） ------------------------------------
def _entry_has_detail(ent: dict, rx: dict) -> bool:
    """这一条目里是否已经有「(周次)(节次)」正文行。"""
    for ln in ent["body"]:
        if (rx["weeks_re"] and rx["period_re"]
                and rx["weeks_re"].search(ln["text"]) and rx["period_re"].search(ln["text"])):
            return True
    return False


def _parse_block_column(day: int, lines: List[dict], lay: Layout, rx: dict,
                        warnings: List[str]) -> List[Record]:
    entries: List[dict] = []
    for ln in lines:
        is_new = (not entries) or (
            not rx["codelist_re"].match(ln["text"])
            and rx["title_re"] and rx["title_re"].match(ln["text"]))
        if is_new and entries:
            # 课程名太长被排版的换行截断：前半行不匹配 title_re（末尾没有那两位代码），
            # 后半行匹配 —— 于是后半行会**另开一个条目**，前半行那个「空条目」只能带着
            # 「没解析出周次和节次」的警告被丢掉，课程名也少了一半。
            # 判据：上一条目到现在一行正文都没有，那它就不可能是一门完整的课
            # （完整的课至少有一行 (周次)(节次)），只能是名字的残段 —— 拼回来。
            # 只在字号相同的情况下拼，避免把小字号的脚注当成课程名的一部分。
            prev = entries[-1]
            if (not prev["body"] or not _entry_has_detail(prev, rx)) \
                    and abs(ln["size"] - prev["size"]) <= 0.15:
                prev["title"] = prev["title"] + ln["text"]
                continue
        if is_new:
            entries.append(dict(title=ln["text"], size=ln["size"], body=[]))
        else:
            entries[-1]["body"].append(ln)
    out: List[Record] = []
    for ent in entries:
        out.extend(_parse_entry(day, ent, lay, rx, warnings))
    return out


def _parse_entry(day: int, ent: dict, lay: Layout, rx: dict,
                 warnings: List[str]) -> List[Record]:
    title = ent["title"]
    course, code = title.strip(), ""
    m = rx["title_re"].match(title) if rx["title_re"] else None
    if m:
        gd = m.groupdict()
        course = (gd.get("course") or title[:m.start("code")]).strip() if "course" in gd \
            else title[:m.start("code")].strip()
        code = (gd.get("code") or "").strip()
        course = re.sub(r"[（(【\[]\s*$", "", course).strip()
    entry_size = ent["size"]

    details: List[dict] = []
    notes: List[str] = []
    for ln in ent["body"]:
        text = ln["text"]
        d = _parse_detail(text, lay, rx)
        if d:
            c2, k2 = _course_from_head(d.get("head", ""), lay, rx)
            if c2:
                d["course"] = c2
                if k2:
                    d["code"] = k2
            details.append(d)
            continue
        if _is_note_line(text, ln["size"], entry_size, lay, rx):
            notes.append(text)
            continue
        if details:
            # 换行续行拼接：补一个空格再接。
            # 教室与教师的分隔本来就是一个空格；如果排版正好在**那个空格上**断了行
            # （第一行 "教西D-101"、第二行 "陈明远"），不加空格拼会得到
            # "教西D-101陈明远" —— 教师被整段吃进教室名。加了空格也不伤害另一种断法
            # （名字中间断，如 "实验大楼C座" + "5楼 向礼琴"），因为教室名内部的空格
            # 最后一定会被 squeeze 掉。
            prev_rest = details[-1]["rest"]
            add = text.strip()
            details[-1]["rest"] = (prev_rest + " " + add) if (prev_rest and add) \
                else (prev_rest or add)
        else:
            notes.append(text)

    if not details:
        warnings.append("「%s」（%s）没解析出周次和节次，已跳过。" % (course, WEEKDAY_SHORT[day]))
        return []

    groups: Dict[Tuple[int, int, str], dict] = {}
    order: List[Tuple[int, int, str]] = []
    for d in details:
        room, teacher = split_room_teacher(d["rest"], lay.room_teacher)
        key = (d["pfrom"], d["pto"], room)
        if key not in groups:
            groups[key] = dict(pfrom=d["pfrom"], pto=d["pto"], room=room, segments=[],
                               course=d.get("course") or "", code=d.get("code") or "")
            order.append(key)
        groups[key]["segments"].append(
            Segment(weeks=d["weeks"], teacher=teacher))

    out = []
    for key in order:
        g = groups[key]
        # 详情行自己带了课程名（block/inline 混排的版式）就用它，否则用标题行的
        out.append(Record(day=day, course=g["course"] or course,
                          code=g["code"] or code, pfrom=g["pfrom"],
                          pto=g["pto"], room=g["room"], segments=g["segments"],
                          notes=list(dict.fromkeys(notes))))
    return out


# ---- inline 模式：每行自带课程名 ----------------------------------------------
def _parse_inline_column(day: int, lines: List[dict], lay: Layout, rx: dict,
                         warnings: List[str]) -> List[Record]:
    out: List[Record] = []
    pending_title = ""
    for ln in lines:
        text = ln["text"]
        d = _parse_detail(text, lay, rx)
        if d is None:
            # 不是详情行：可能就是这一格里课程名单独占了一行（版式混排时常见），
            # 留着给下一行当名字用
            if text and not (rx["codelist_re"] and rx["codelist_re"].match(text)):
                pending_title = text
            continue
        course, code = _course_from_head(d.get("head", ""), lay, rx)
        if not course:
            course = pending_title
        if not course:
            warnings.append("有一行「%s」解析出了周次和节次，但没有课程名，已跳过。"
                            % text[:28])
            continue
        room, teacher = split_room_teacher(d["rest"], lay.room_teacher)
        out.append(Record(day=day, course=course, code=code, pfrom=d["pfrom"],
                          pto=d["pto"], room=room,
                          segments=[Segment(weeks=d["weeks"], teacher=teacher)]))
    return out


# ---- table 模式：平面表，一行一条课 -------------------------------------------
TABLE_ROLES = {
    "course": ("课程名称", "课程名", "课程", "科目", "course"),
    "code": ("课程代码", "代码", "编号"),
    "day": ("星期", "周几", "上课星期", "day"),
    "period": ("节次", "节", "上课节次", "时段", "periods"),
    "weeks": ("周次", "起止周", "上课周次", "weeks"),
    "teacher": ("任课教师", "教师", "老师", "teacher"),
    "room": ("上课地点", "上课教室", "教室", "地点", "场地", "room"),
}


def _classify_header(text: str) -> Optional[str]:
    t = re.sub(r"\s+", "", text or "")
    for role, names in TABLE_ROLES.items():
        if t in names:
            return role
    for role, names in TABLE_ROLES.items():
        for n in names:
            if n in t:
                return role
    return None


def _parse_table_page(spans, lay: Layout, rx: dict, warnings: List[str]) -> List[Record]:
    lines = _group_into_lines(spans, lay.col_y_tolerance)
    hdr_i, roles = None, {}
    for i, ln in enumerate(lines):
        found = {}
        for s in ln["spans"]:
            role = _classify_header(s["text"])
            if role:
                found[role] = (s["x0"] + s["x1"]) / 2.0
        if len(found) >= 4 and "course" in found:
            hdr_i, roles = i, found
            break
    if hdr_i is None:
        if warnings is not None:
            warnings.append("没找到平面表的列名（要至少有 课程/教师/周次/地点 之类 4 列）。")
        return []

    centres = sorted(roles.values())
    bounds = [(centres[i] + centres[i + 1]) / 2.0 for i in range(len(centres) - 1)]

    def role_of_x(x):
        for i, b in enumerate(bounds):
            if x < b:
                return i
        return len(centres) - 1

    inv = {role_of_x(c): r for r, c in roles.items()}

    out: List[Record] = []
    for ln in lines[hdr_i + 1:]:
        cells: Dict[int, str] = {}
        for s in ln["spans"]:
            k = role_of_x(s["x0"])
            cells[k] = (cells.get(k, "") + " " + s["text"]).strip()
        texts = {inv[k]: v for k, v in cells.items() if k in inv}
        course = re.sub(r"\s+", " ", texts.get("course", "")).strip()
        if not course:
            continue
        if re.fullmatch(r"[-—–\s]*", course):
            continue
        rec = _table_record(texts, course, lay, rx, warnings)
        if rec:
            out.extend(rec)
    return out


def _table_record(texts: Dict[str, str], course: str, lay: Layout, rx: dict,
                  warnings: List[str]) -> List[Record]:
    m = re.match(r"^(.*?)[\s（(](\d{2,})[)）]?$", course)
    code = ""
    if m and texts.get("code"):
        course, code = m.group(1).strip(), texts["code"].strip()
    elif texts.get("code"):
        code = texts["code"].strip()

    # 星期：可能写在「时间」列里（"星期二 3-4节"）
    day = None
    for key in ("day", "period", "time"):
        t = texts.get(key, "")
        for i, aliases in enumerate(_header_labels(lay)):
            if any(a in t for a in aliases):
                day = i
                break
        if day is not None:
            break
    if day is None:
        d2 = re.search(r"(?:^|\D)([1-7])(?:\D|$)", texts.get("day", ""))
        if d2:
            day = int(d2.group(1)) - 1
    if day is None:
        warnings.append("「%s」没解析出星期，已跳过。" % course)
        return []

    pfrom = pto = None
    pm = rx["period_list_re"].match(texts.get("period", "")) if rx["period_list_re"] else None
    if pm:
        pfrom = int(pm.group("pfrom"))
        pto = int(pm.group("pto")) if pm.groupdict().get("pto") else pfrom
    else:
        m2 = re.search(r"(\d{1,2})\s*[-~—–]\s*(\d{1,2})", texts.get("period", ""))
        if m2:
            pfrom, pto = int(m2.group(1)), int(m2.group(2))
        elif re.fullmatch(r"\s*\d{1,2}\s*", texts.get("period", "")):
            pfrom = pto = int(texts["period"].strip())
    if pfrom is None:
        warnings.append("「%s」没解析出节次，已跳过。" % course)
        return []

    weeks: List[int] = []
    if rx["weeks_token_re"]:
        # 优先看「周次」列；没有这一列才退回「时间」列。
        # 但退回去时**只接受带「周」字的写法**：时间列里的 "1-2" 是节次不是周次，
        # 按裸区间匹配会把课排到第 1~2 周去 —— 那正是「不报错、只是全排错」的事故。
        wtext = texts.get("weeks", "")
        cand = [(wtext, True)] if wtext else []
        if texts.get("time"):
            cand.append((texts["time"], False))
        for txt, is_weeks_col in cand:
            mw = rx["weeks_token_re"].search(txt)
            if not mw:
                continue
            if not is_weeks_col and "周" not in mw.group(0):
                continue
            try:
                weeks = expand_weeks_token(mw.group(0))
            except LayoutError as exc:
                warnings.append("「%s」的周次看不懂：%s" % (course, exc))
                return []
            if weeks:
                break
    if not weeks:
        warnings.append("「%s」没解析出周次，已跳过。" % course)
        return []

    clock = None
    if rx["clock_re"]:
        mc = rx["clock_re"].search(texts.get("period", "") or texts.get("time", ""))
        if mc:
            clock = ("%02d:%s" % (int(mc.group("h1")), mc.group("m1")),
                     "%02d:%s" % (int(mc.group("h2")), mc.group("m2")))

    room = re.sub(r"\s+", "", texts.get("room", ""))
    teacher = re.sub(r"\s+", "", texts.get("teacher", ""))
    if not room and texts.get("period") and room is None:
        room = ""
    return [Record(day=day, course=course, code=code, pfrom=pfrom, pto=pto, room=room,
                   segments=[Segment(weeks=weeks, teacher=teacher)], clock=clock)]


# --------------------------------------------------------------------- 后处理
def _dedupe(records: List[Record]) -> List[Record]:
    merged: Dict[str, Record] = {}
    for r in records:
        k = r.key()
        if k not in merged:
            merged[k] = r
            continue
        tgt = merged[k]
        have = {(tuple(s.weeks), s.teacher) for s in tgt.segments}
        for s in r.segments:
            if (tuple(s.weeks), s.teacher) not in have:
                tgt.segments.append(s)
        for n in r.notes:
            if n not in tgt.notes:
                tgt.notes.append(n)
        if r.clock and not tgt.clock:
            tgt.clock = r.clock
    return list(merged.values())


def _parse_meta(page, lay: Layout, rx: dict) -> Meta:
    meta = Meta()
    text = page.get_text()          # 保留空格，否则字段会连成一片

    def grab(label):
        m = re.search(re.escape(label) + r"\s*([^\s]+)", text)
        return m.group(1) if m else ""

    for field_name, label in (lay.meta_labels or {}).items():
        if hasattr(meta, field_name):
            setattr(meta, field_name, grab(label))

    m = rx["term_re"].search(text) if rx["term_re"] else None
    if m:
        season = (m.groupdict().get("season") or "").strip()
        meta.term = "%s-%s%s" % (m.group("a"), m.group("b"), (" " + season) if season else "")
    m = rx["print_date_re"].search(text) if rx["print_date_re"] else None
    if m:
        meta.print_date = m.group("v")
    m = rx["credits_re"].search(text) if rx["credits_re"] else None
    if m:
        meta.total_credits = m.group("v")

    if rx["remark_head_re"] and rx["remark_re"]:
        m = rx["remark_head_re"].search(text)
        if m:
            for mm in rx["remark_re"].finditer(m.group(1)):
                meta.remark_courses.append(dict(
                    name=mm.group("name").strip(), code=mm.group("code"),
                    week_from=int(mm.group("a")), week_to=int(mm.group("b"))))
    return meta


# ------------------------------------------------------------- 版式自动识别
def detect_layout(pdf_path: str, reg: Optional[REGISTRY.__class__] = None,
                  log=None) -> Tuple[Layout, List[str]]:
    """挨个版式试解析，挑一个「解析得最完整」的。

    为什么要自动识别：用户不在乎版式叫什么，只在乎课表能不能导进日历。

    打分怎么排的（这是踩了两版坑才定下来的）：
        第一版只看条数 —— 宽松的正则天然能切出更多「记录」，拿西工大课表去试 inline
        版式，条数反而更多，于是错误版式赢了。
        第二版主分改成「读出教室的条数」—— 还是输，因为越宽松的版式不仅条数多、连
        「带教室的条数」也多（它把「教室 教师」行也当成独立记录）。条数这类**越多越好的
        量**根本不能当主分。
        定版：冲突最少 → 警告最少 → **特异性最高**（正则越严格越可能是对这份 PDF 的
        正确解释，两家都干净时选严格的那个）→ 读出教室的多 → 总条数多。
        特异性用内置版式各带的 specificity（nwpu 的周次节次必须带括号=30，最严格）。
    为什么要返回 notes：识别终究是猜的，必须把「每个版式各读出什么」讲明白，
    用户才能在预览表里核对（本项目的设计底线是不静默算错）。
    """
    reg = reg or REGISTRY
    trials = []
    notes: List[str] = []
    for lay in reg.all():
        try:
            res = parse_pdf(pdf_path, layout=lay)
        except Exception as exc:                        # noqa: BLE001
            notes.append("  · %s：读不出来（%s）" % (lay.name, str(exc).splitlines()[0]))
            continue
        chk = verify_parse(res)
        with_room = sum(1 for r in res.records if r.room)
        rank = (-len(chk["conflicts"]), -len(res.warnings), lay.specificity,
                with_room, len(res.records))
        trials.append((rank, lay, res, chk))
        notes.append("  · %s：%d 条课程（其中 %d 条读出了教室）、%d 处冲突、%d 条警告"
                     % (lay.name, len(res.records), with_room, len(chk["conflicts"]),
                        len(res.warnings)))
    if not trials:
        raise RuntimeError("所有已知版式都读不出这份 PDF。用 --dump-layout 看结构，"
                           "再 --layout-template 导一份版式文件照着改。")
    trials.sort(key=lambda t: t[0], reverse=True)
    best_rank, best_lay, best_res, best_chk = trials[0]
    if not best_res.records:
        raise RuntimeError("已知版式都没能从这份 PDF 里读出课程。\n"
                           "各版式的尝试结果：\n" + "\n".join(notes))
    if best_chk["conflicts"]:
        notes.append("  ⚠ 选中版式仍报出 %d 处时间冲突，几乎一定是解析串行了，务必逐条核对。"
                     % len(best_chk["conflicts"]))
    if not any(r.room for r in best_res.records):
        notes.append("  ⚠ 选中的版式没读出任何教室：请核对，或写一份更贴合的版式"
                     "（--dump-layout 看结构）。")
    if log:
        log("自动识别版式（冲突 → 警告 → 正则严格度 → 教室数 → 条数）：")
        for n in notes:
            log(n)
        log("  → 采用：%s" % best_lay.name)
    return best_lay, notes


# --------------------------------------------------------------------- 结构自检
def verify_parse(result: ParseResult) -> dict:
    """自检：同一天同一时段不应同时出现两门不同的课。"""
    slot: Dict[Tuple[int, int, int], str] = {}
    conflicts = []
    for r in result.records:
        for w in r.all_weeks():
            for p in range(r.pfrom, r.pto + 1):
                k = (w, r.day, p)
                if k in slot and slot[k] != r.course:
                    conflicts.append((w, WEEKDAY_SHORT[r.day], p, slot[k], r.course))
                slot[k] = r.course
    total = sum(len(r.all_weeks()) for r in result.records)
    return dict(records=len(result.records), instances=total, conflicts=conflicts,
                courses=sorted({r.course for r in result.records}))


if __name__ == "__main__":
    import sys
    res = parse_pdf(sys.argv[1], log=print)
    md = res.meta
    print("学期=%s 年级=%s 学院=%s 专业=%s 班级=%s 学号=%s 姓名=%s 总学分=%s"
          % (md.term, md.grade, md.college, md.major, md.klass, md.sid, md.name,
             md.total_credits))
    print("备注课程:", md.remark_courses)
    print("-" * 100)
    for r in res.records:
        print("%s 第%-5s %-30s 周次 %-12s %-24s %s" % (
            r.day_name, "%d-%d节" % (r.pfrom, r.pto), r.course,
            compress_weeks(r.all_weeks()), r.room, "；".join(r.teacher_desc())))
    print("-" * 100)
    print(verify_parse(res))
