# -*- coding: utf-8 -*-
"""把西北工业大学教务系统导出的《学生课表》PDF 解析成结构化数据。

核心思路（这一步是整个程序可靠性的关键）：
  课表是一个「星期 × 节次」的网格，同一段文字在不同列里的字号可能不同，导致同一
  视觉行的 y 坐标有几像素差异。因此必须先按 x 把每个文本块归到某一列，**再在列内
  纵向成行**；否则跨列的同一水平行会被错误合并，文字就会串到错误的星期下面。

每个条目的处理分两步，避免「续行」拼错：
  1. 先把列内各行分类 —— 以「(周次)(节次)」开头的算正文行，字号更小或含说明性词
     语的算说明行，其余算上一行的换行续行；
  2. 续行拼接完成后再统一把正文行拆成「教室 + 教师」，并按 (节次起, 节次止, 教室)
     归组，这样被换行截断的教师名能正确补全。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
WEEKDAY_SHORT = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

TITLE_RE = re.compile(r"^[^(\s].*?[\s（(]?\d{2}$")
CODELIST_RE = re.compile(r"^[0-9A-Za-z;]+$")
WEEKS_RE = re.compile(r"\((\d+)(?:~(\d+))?\s*(?:\(([单双])\))?\s*周\)")
PERIOD_RE = re.compile(r"\((\d+)\s*-\s*(\d+)\s*节\)")
REMARK_RE = re.compile(
    r"(\d+)\s*[.、]\s*([^\d.、]+?)\s+([A-Za-z]{2,}\d+[.\d]*)\s*\((\d+)\s*~\s*(\d+)\)")
NOTE_WORD_RE = re.compile(r"(级|本科|公共课|全校|^大物实验$)")


@dataclass
class Segment:
    """「周次 + 教师」的组合：同一门课不同周次可能换老师。"""
    weeks: List[int]
    teacher: str = ""
    raw: str = ""

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

    @property
    def day_name(self) -> str:
        return WEEKDAY_SHORT[self.day]

    def all_weeks(self) -> List[int]:
        out = set()
        for s in self.segments:
            out.update(s.weeks)
        return sorted(out)

    def week_span(self) -> Tuple[int, int]:
        w = self.all_weeks()
        return (w[0], w[-1]) if w else (0, 0)

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
    warnings: List[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


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


def _is_note_line(text: str, size: float, entry_size: float) -> bool:
    if size < entry_size - 0.15:
        return True
    if CODELIST_RE.match(text):
        return True
    if "周" not in text and NOTE_WORD_RE.search(text):
        return True
    return False


def _split_room_teacher(rest: str) -> Tuple[str, str]:
    """把正文行「(节次) 之后的部分」拆成 教室 + 教师。

    按空白切分，最后一段若像人名（2~4 个汉字、不含数字）就当作教师。
    """
    rest = rest.strip()
    if not rest:
        return "", ""
    room = teacher = ""
    m = re.match(r"^(.*?)\s+([\u4e00-\u9fff]{2,4})$", rest)
    if m:
        room, teacher = m.group(1), m.group(2)
    else:
        parts = rest.split()
        if len(parts) >= 2:
            last = parts[-1]
            if 2 <= len(last) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", last):
                room, teacher = "".join(parts[:-1]), last
    if not room and not teacher:
        room = rest
    # 中文楼名/教室名内部不需要空格（"教西 D-101" -> "教西D-101"）
    return re.sub(r"\s+", "", room), re.sub(r"\s+", "", teacher)


# --------------------------------------------------------------------- parsing
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


def _find_header(spans):
    """返回 (centres, header_bottom)；找不到表头返回 (None, None)。"""
    found: Dict[str, List[dict]] = {}
    for s in spans:
        t = s["text"].strip()
        if t in WEEKDAY_NAMES:
            found.setdefault(t, []).append(s)
    if len(found) < 7:
        return None, None
    picked = []
    for name in WEEKDAY_NAMES:
        picked.append(min(found[name], key=lambda z: z["y0"]))
    centres = [(s["x0"] + s["x1"]) / 2.0 for s in picked]
    return centres, max(s["y1"] for s in picked)


def parse_pdf(pdf_path: str, log=None) -> ParseResult:
    def say(msg):
        if log:
            log(msg)

    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("缺少依赖 PyMuPDF，请先执行：pip install pymupdf") from exc

    doc = pymupdf.open(pdf_path)
    result = ParseResult()
    if doc.page_count > 1:
        result.warnings.append("PDF 共 %d 页，将逐页解析并合并。" % doc.page_count)

    for pno in range(doc.page_count):
        page = doc[pno]
        spans = _collect_spans(page)
        if not spans:
            continue
        centres, header_bottom = _find_header(spans)
        if centres is None:
            if pno == 0:
                raise RuntimeError(
                    "没有找到「星期一…星期日」表头，这大概不是教务系统导出的学生课表。")
            continue

        gaps = [centres[i + 1] - centres[i] for i in range(6)]
        step = sorted(gaps)[len(gaps) // 2]
        bounds = [(centres[i] + centres[i + 1]) / 2.0 for i in range(6)]
        col_left = centres[0] - step / 2.0

        body_bottom = page.rect.height
        for s in spans:
            t = s["text"].strip()
            if t.startswith("备注") or t.startswith("总学分"):
                body_bottom = min(body_bottom, s["y0"] - 1.0)

        def col_of(x):
            for i, b in enumerate(bounds):
                if x < b:
                    return i
            return 6

        body = []
        for s in spans:
            t = s["text"].strip()
            if s["x0"] < col_left or s["y0"] <= header_bottom + 1 or s["y0"] >= body_bottom:
                continue
            if "打印日期" in t or t.startswith("http") or re.fullmatch(r"\d+/\d+", t):
                continue
            body.append(dict(s, col=col_of(s["x0"])))

        result.stats["body_spans"] = result.stats.get("body_spans", 0) + len(body)

        for ci in range(7):
            items = sorted([s for s in body if s["col"] == ci],
                           key=lambda z: (z["y0"], z["x0"]))
            lines = []
            for s in items:
                if lines and abs(s["y0"] - lines[-1]["ybase"]) <= 2.5:
                    lines[-1]["spans"].append(s)
                else:
                    lines.append(dict(ybase=s["y0"], spans=[s]))
            for ln in lines:
                ln["spans"].sort(key=lambda z: z["x0"])
                # 空白折叠成一个空格，再去掉首尾
                ln["text"] = re.sub(r"\s+", " ",
                                    "".join(z["text"] for z in ln["spans"])).strip()
                ln["y"] = min(z["y0"] for z in ln["spans"])
                ln["size"] = round(max(z["size"] for z in ln["spans"]), 2)
            lines.sort(key=lambda z: z["y"])

            entries = []
            for ln in lines:
                is_new = (not entries) or (
                    not CODELIST_RE.match(ln["text"]) and TITLE_RE.match(ln["text"]))
                if is_new:
                    entries.append(dict(title=ln["text"], size=ln["size"], body=[]))
                else:
                    entries[-1]["body"].append(ln)
            for ent in entries:
                result.records.extend(_parse_entry(ci, ent, result.warnings))

        if pno == 0:
            result.meta = _parse_meta(page)

    if not result.records:
        raise RuntimeError("没有解析到任何课程，请确认 PDF 是教务系统的学生课表。")

    result.records = _dedupe(result.records)
    say("解析到 %d 条课程安排。" % len(result.records))
    return result


def _parse_entry(day: int, ent: dict, warnings: List[str]) -> List[Record]:
    title = ent["title"]
    m = re.match(r"^(.*?)[\s（(]?(\d{2})$", title)
    if m:
        course, code = m.group(1).strip(), m.group(2)
    else:
        course, code = title.strip(), ""
    entry_size = ent["size"]

    details: List[dict] = []
    notes: List[str] = []
    for ln in ent["body"]:
        text = ln["text"]
        wm, pm = WEEKS_RE.search(text), PERIOD_RE.search(text)
        if wm and pm:
            details.append(dict(
                weeks=expand_weeks(int(wm.group(1)),
                                   int(wm.group(2)) if wm.group(2) else None,
                                   wm.group(3)),
                pfrom=int(pm.group(1)), pto=int(pm.group(2)),
                rest=text[pm.end():].strip()))
            continue
        if _is_note_line(text, ln["size"], entry_size):
            notes.append(text)
            continue
        if details:
            # 换行续行：只去掉首尾空白再拼接，保留行内空格（那是教室与教师的分隔）
            details[-1]["rest"] += text.strip()
        else:
            notes.append(text)

    if not details:
        warnings.append("「%s」（%s）没解析出周次和节次，已跳过。" % (course, WEEKDAY_SHORT[day]))
        return []

    groups: Dict[Tuple[int, int, str], dict] = {}
    order: List[Tuple[int, int, str]] = []
    for d in details:
        room, teacher = _split_room_teacher(d["rest"])
        key = (d["pfrom"], d["pto"], room)
        if key not in groups:
            groups[key] = dict(pfrom=d["pfrom"], pto=d["pto"], room=room, segments=[])
            order.append(key)
        groups[key]["segments"].append(
            Segment(weeks=d["weeks"], teacher=teacher, raw=d["rest"]))

    out = []
    for key in order:
        g = groups[key]
        out.append(Record(day=day, course=course, code=code, pfrom=g["pfrom"],
                          pto=g["pto"], room=g["room"], segments=g["segments"],
                          notes=list(dict.fromkeys(notes))))
    return out


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
    return list(merged.values())


def _parse_meta(page) -> Meta:
    meta = Meta()
    text = page.get_text()          # 保留空格，否则字段会连成一片

    def grab(label):
        m = re.search(re.escape(label) + r"\s*([^\s]+)", text)
        return m.group(1) if m else ""

    meta.grade = grab("年级：")
    meta.college = grab("学院：")
    meta.major = grab("专业：")
    meta.klass = grab("班级：")
    meta.sid = grab("学号：")
    meta.name = grab("姓名：")

    m = re.search(r"(\d{4}-\d{4})\s*([秋冬春夏])", text)
    if m:
        meta.term = "%s %s" % (m.group(1), m.group(2))
    m = re.search(r"打印日期\s*[:：]?\s*([\d/]+)", text)
    if m:
        meta.print_date = m.group(1)
    m = re.search(r"总学分\s*[:：]?\s*([\d.]+)", text)
    if m:
        meta.total_credits = m.group(1)

    m = re.search(r"备注\s*[:：](.{0,400})", text, re.S)
    if m:
        for mm in REMARK_RE.finditer(m.group(1)):
            meta.remark_courses.append(dict(
                name=mm.group(2).strip(), code=mm.group(3),
                week_from=int(mm.group(4)), week_to=int(mm.group(5))))
    return meta


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
