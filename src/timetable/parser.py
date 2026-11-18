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

import datetime as dt
import re
import unicodedata
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
    pfrom: Optional[int]        # None = 这份课表只印了时刻，没印第几节（待 resolve_periods 反查）
    pto: Optional[int]
    room: str
    segments: List[Segment] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # 版式直接印了上下课时刻时记在这里（`08:00-09:35`）。节次时间以用户设定为准，
    # 所以它主要用来**反查第几节**；反查不到时才作为最后的时间来源（并会明确报警）。
    clock: Tuple[str, str] | None = None

    @property
    def day_name(self) -> str:
        return WEEKDAY_SHORT[self.day]

    def has_periods(self) -> bool:
        return self.pfrom is not None and self.pto is not None

    def period_desc(self) -> str:
        """给日历/表预览用的人类可读节次。"""
        if self.has_periods():
            return "%d-%d节" % (self.pfrom, self.pto)
        if self.clock:
            return "%s-%s（未匹配到节次）" % self.clock
        return "（无节次信息）"

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
        return "%s|%s|%s|%s|%s" % (self.course, self.day_name, self.pfrom, self.pto,
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


# ---------------------------------------------------------------- 文本归一化
# 有些教务系统（尤其是**网页打印成 PDF**）会把常用汉字写成「康熙部首 / CJK 部首补充」
# 区的字符：肉眼一模一样，码位却不同。实测一份这样的课表里有 141 个这样的字符
# （`⼯` U+2F2F 不是「工」U+5DE5，`⽣` U+2F63 不是「生」），于是
#   * 「星期一」匹配不上表头 -> 整份报「没找到星期表头」；
#   * 课程名/教室/教师全是"看着对、比较全不等"的字符串。
# NFKC 能把「康熙部首」区（U+2F00–U+2FDF）还原成正常汉字，但**管不到**
# 「CJK 部首补充」区（U+2E80–U+2EFF）—— 那批字符没有兼容分解，只能手工映射。
_RADICAL_MAP = {
    # 实测遇到的（无 NFKC 分解，必须手工）
    "\u2EC4": "西", "\u2EA0": "民", "\u2EE9": "黄", "\u2EE2": "马", "\u2EF0": "龙",
    # 同一区里常见、容易出现在校名/课程名里的
    "\u2E8C": "小", "\u2E95": "网", "\u2EA1": "水", "\u2EA6": "火", "\u2EB6": "月",
    "\u2EC1": "虎", "\u2EC6": "见", "\u2EDB": "言", "\u2EE0": "足", "\u2EEC": "鱼",
    "\u2EF6": "齿", "\u2EF4": "鸟",
}
_RADICAL_RE = re.compile(r"[\u2E80-\u2EFF]")
# 康熙部首区（U+2F00–U+2FDF）：NFKC 会把它们还原成正常汉字
_KANGXI_RE = re.compile(r"[\u2F00-\u2FDF]")


def normalize_text(s: str, aggressive: bool = False) -> str:
    """把「看着一样、码位不同」的字符统一成正常汉字。

    **默认只在必要时动**（aggressive=False）：
      * 只有发现「康熙部首 / CJK 部首补充」区字符时才做 NFKC —— 因为 NFKC 是整体
        转换，会把全角括号、全角冒号也变成半角，从而改掉**本来正常**的课程名
        （实测把「体育（篮球）」改成「体育(篮球)」，把 fixture 断言弄红）。
        普通 PDF 的原文应当一字不改。
      * 含部首字符时：NFKC 处理康熙部首区，再对「CJK 部首补充」区（NFKC 管不到）
        走手工映射。
    aggressive=True 时无条件 NFKC —— 给 OCR 文本用：OCR 输出的全角/半角混用
    （`Ａ－302`、`（上）`）必须归一，否则同一门课会因宽度差异变成两条记录。
    """
    if not s:
        return s
    radical = bool(_RADICAL_RE.search(s)) or bool(_KANGXI_RE.search(s))
    if not (aggressive or radical):
        return s
    s = unicodedata.normalize("NFKC", s)
    if _RADICAL_RE.search(s):
        s = s.translate(str.maketrans(_RADICAL_MAP))
    return s


def has_unmapped_radicals(s: str) -> bool:
    """还有没被映射掉的部首字符（用来在解析结果里明确提示，而不是静默错）。"""
    return bool(s and _RADICAL_RE.search(s))


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
                # 行聚合要用**基线**（origin），不要用 bbox 的 y0：y0 取决于该行实际
                # 字形（数字、全角括号、汉字的 ascender 各不相同），同一行里不同字号
                # 或不同字符的行会差 1~2pt。实测一份 9pt 行距的课表，y0 抖动 1.7pt，
                # 超过 2.5pt 容差的一半，相邻两行直接被并成一行 —— 于是课程名和详情行
                # 粘在一起、整门课丢信息。基线对同一行是**精确相同**的，用它才稳。
                base = sp["origin"][1] if "origin" in sp else y0
                spans.append(dict(x0=x0, y0=y0, x1=x1, y1=y1, base=base,
                                  size=round(sp["size"], 2),
                                  text=normalize_text(sp["text"])))
    return spans


def _header_labels(lay: Layout) -> List[List[str]]:
    labels = lay.day_headers or []
    if len(labels) < lay.min_day_columns:
        labels = list(labels) + [list(x) for x in BUILTIN[DEFAULT_LAYOUT_ID].day_headers]
    return labels[:7]


def _find_header(spans, lay: Layout):
    """找星期表头，返回 (day_indices, centres, header_bottom)；找不到返回 (None, None, None)。

    `day_indices` 是每个找到的列对应的星期序号（0=周一…6=周日）。之所以不假设
    「一定是从周一数到周日、连续 7 列」：
      * 有些学校周日起头（列序与序号不同）；
      * **网页打印的课表常常只有前几列**（实测一份只有 星期一~星期四 —— 表格被裁掉
        或者页面只显示了左半边）。这种必须能读，但要在结果里明确警告"课表可能不完整"，
        否则用户会以为拿到的是整学期课表。
    """
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
        return None, None, None
    picked = [(i, min(found[i], key=lambda z: z["y0"])) for i in sorted(found)]
    picked.sort(key=lambda kv: (kv[1]["x0"] + kv[1]["x1"]) / 2.0)   # 按 x 从左到右
    day_indices = [i for i, _ in picked]
    centres = [(s["x0"] + s["x1"]) / 2.0 for _, s in picked]
    header_bottom = max(s["y1"] for _, s in picked)
    return day_indices, centres, header_bottom


def _group_into_lines(items, tol: float) -> List[dict]:
    """同一列内按**基线**聚成「视觉行」。必须先分列再走这里（见模块 docstring）。

    用 base（基线）而不是 bbox.y0：y0 受字形 ascender 影响，同一行能差 1~2pt，
    在 9pt 行距的课表上足以把相邻两行误并（实测过）。基线对同一行精确相同。
    """
    items = sorted(items, key=lambda z: (z.get("base", z["y0"]), z["x0"]))
    lines: List[dict] = []
    for s in items:
        b = s.get("base", s["y0"])
        if lines and abs(b - lines[-1]["ybase"]) <= tol:
            lines[-1]["spans"].append(s)
        else:
            lines.append(dict(ybase=b, spans=[s]))
    for ln in lines:
        ln["spans"].sort(key=lambda z: z["x0"])
        # 空白折叠成一个空格，再去掉首尾
        ln["text"] = re.sub(r"\s+", " ", "".join(z["text"] for z in ln["spans"])).strip()
        ln["y"] = min(z["y0"] for z in ln["spans"])
        ln["size"] = round(max(z["size"] for z in ln["spans"]), 2)
    lines.sort(key=lambda z: z["ybase"])
    return lines


# --------------------------------------------------------------------- parsing
def parse_spans_pages(pages, layout: Optional[Layout] = None, log=None,
                      period_times: Optional[Dict[int, Tuple[str, str]]] = None,
                      clock_tolerance_min: int = 10) -> ParseResult:
    """从**每页的 span 列表**解析课表（与输入是 PDF 还是 OCR 无关）。

    这是「三段式」输入的关键一环：PDF 文字层、OCR（图片/扫描件）最终都变成
    「带坐标的文本」，然后走**同一个**解析器。好处是只有一套结构推断逻辑要维护、
    一套测试能覆盖两条路；坏处（如果分叉）就是今天已经踩过的坑 —— 两个入口各自
    演化，其中一个悄悄解析出 0 条。

    pages: [[span, ...], ...]，每页一组。span 至少要有 x0/y0/x1/y1/size/text，
           有 base（基线）更好；没有就用 y0 代替。
    """
    def say(msg):
        if log:
            log(msg)

    lay = layout or REGISTRY.get(DEFAULT_LAYOUT_ID)
    rx = lay.compiled()
    result = ParseResult(layout_id=lay.id)
    if len(pages) > 1:
        result.notes.append("共 %d 页，将逐页解析并合并。" % len(pages))

    for pno, spans in enumerate(pages):
        spans = _with_base(spans)
        if not spans:
            continue
        if lay.entry_mode == "table":
            recs = _parse_table_page(spans, lay, rx, result.warnings)
        else:
            recs = _parse_grid_page(spans, lay, rx, result, pno, result.warnings)
        result.records.extend(recs)
        if pno == 0:
            result.meta = _parse_meta(_spans_text(spans), lay, rx)

    if not result.records:
        raise RuntimeError(
            "版式「%s」没有解析到任何课程。\n"
            "如果这份课表是别的学校/别的打印入口导出的，换一种版式试试：--layout %s\n"
            "（先用 --dump-layout 看看程序眼里的这一页长什么样，再决定是选内置版式还是写一份。）"
            % (lay.name, "、".join(k for k in REGISTRY.ids() if k != lay.id)))

    result.records = _dedupe(result.records)

    # 「时刻制」课表：把只有时刻的记录反查成第几节（用用户设定的作息表）。
    # 查不到的**不猜**，进 warnings，由调用方明确报错让用户补作息表。
    if period_times:
        result.records, unresolved = resolve_periods(result.records, period_times,
                                                     clock_tolerance_min)
        if unresolved:
            result.warnings.append(
                "这些课的格子里只印了上下课时刻，按你设定的作息表反查不到是第几节：%s。"
                "请核对「节次时间…」是否和本校一致（也可以手动补全这些行）。"
                % "；".join(unresolved[:6]))
        else:
            n = sum(1 for r in result.records if r.clock and r.has_periods())
            if n:
                result.notes.append(
                    "%d 条课程的时刻已按作息表反查成节次（时刻→节次，允许 %d 分钟误差）。"
                    % (n, clock_tolerance_min))
    elif any(r.clock and not r.has_periods() for r in result.records):
        result.warnings.append(
            "这份课表只印了上下课时刻，没印第几节；但没有传入作息表，无法反查。"
            "请先设定节次时间（界面「节次时间…」或 --period-times）。")

    say("版式：%s" % lay.name)
    say("解析到 %d 条课程安排。" % len(result.records))
    return result


def parse_pdf(pdf_path: str, log=None, layout: Optional[Layout] = None,
              period_times: Optional[Dict[int, Tuple[str, str]]] = None,
              clock_tolerance_min: int = 10) -> ParseResult:
    """解析课表 PDF（必须有文字层）。

    `layout`      不传就用默认的通用版式。
    `period_times` 用户设定的作息表（节次 -> 上下课时间）。只用于一件事：
                  把「格子里只印了时刻」的课**反查**成第几节。节次时间以用户设定为准，
                  PDF 上的时刻只是线索。不传就跳过反查（这类课会进 warnings）。

    没有文字层的 PDF／图片请走 ocr_source（它渲染成图 → OCR → 同样交给
    parse_spans_pages），两条路共用同一套结构解析。
    """
    def say(msg):
        if log:
            log(msg)

    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("缺少依赖 PyMuPDF，请先执行：pip install pymupdf") from exc

    doc = pymupdf.open(pdf_path)
    pages = [_collect_spans(doc[pno]) for pno in range(doc.page_count)]
    doc.close()
    return parse_spans_pages(pages, layout=layout, log=log,
                             period_times=period_times,
                             clock_tolerance_min=clock_tolerance_min)


def _with_base(spans) -> list:
    """保证每个 span 都有 base（基线）；外部来源（如 OCR）可能只给了 bbox。"""
    out = []
    for s in spans:
        if "base" not in s:
            s = dict(s)
            s["base"] = s["y0"]
        out.append(s)
    return out


def _spans_text(spans) -> str:
    """把一页的 span 还原成纯文本（逐行拼接），用于抓「学期/姓名/学号」这类元信息。

    PDF 可以直接 page.get_text()，但 OCR 来源没有那个接口。统一从 span 还原，
    两条路就都能抓元信息（OCR 的元信息准确率同样受验收环节保护）。

    注意要丢掉**页码**（`1/2` 这种）：它和"打印日期"常常在同一基线上，
    不丢会被拼成 `打印日期： 2026/08/251/2`，把日期吃成 `2026/08/251/2`
    （实测就是这样把 meta.print_date 弄错的）。
    """
    by_base: Dict[float, List[dict]] = {}
    for s in spans:
        t = (s.get("text") or "").strip()
        if re.fullmatch(r"\d{1,3}\s*/\s*\d{1,3}", t):
            continue                                  # 页码
        key = round(s.get("base", s["y0"]), 1)
        by_base.setdefault(key, []).append(s)
    lines = []
    for base in sorted(by_base):
        row = sorted(by_base[base], key=lambda z: z["x0"])
        # span 之间补一个空格再做「多空格折叠」。不补的话相邻字段会粘成一串
        # （`年级:2025学院:航海学院`），标签后的取值会贪到整行、被合理性校验丢掉。
        lines.append(re.sub(r"\s+", " ", " ".join(z["text"] for z in row)).strip())
    return "\n".join(lines)


def _parse_grid_page(spans, lay: Layout, rx: dict, result: ParseResult,
                     pno: int, warnings: List[str]) -> List[Record]:
    """解析「网格」型页面（block / inline 模式）。"""
    day_indices, centres, header_bottom = _find_header(spans, lay)
    if centres is None:
        if pno == 0:
            raise RuntimeError(
                "没有找到「星期一…星期日」表头（版式「%s」），这大概不是教务系统导出的课表。"
                % lay.name)
        return []
    result.stats["header_found"] = result.stats.get("header_found", 0) + 1
    # 表头不全时必须说出来：网页打印的课表常常只印出前几列（实测一份只有周一~周四），
    # 静默按 4 天解析会让用户以为这就是整学期课表。
    if len(day_indices) < 7:
        missing = [WEEKDAY_SHORT[i] for i in range(7) if i not in day_indices]
        warnings.append(
            "只识别到 %d 个星期表头（%s），缺 %s —— 课表可能被裁掉/只打印出一部分，"
            "请在预览表里核对是否完整。"
            % (len(day_indices),
               "、".join(WEEKDAY_SHORT[i] for i in day_indices),
               "、".join(missing)))

    gaps = [centres[i + 1] - centres[i] for i in range(len(centres) - 1)]
    step = sorted(gaps)[len(gaps) // 2] if gaps else 0
    bounds = [(centres[i] + centres[i + 1]) / 2.0 for i in range(len(centres) - 1)]
    col_left = centres[0] - (step / 2.0 if step else 0)

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
    for ci, day in enumerate(day_indices):
        lines = _group_into_lines([s for s in body if s["col"] == ci],
                                  lay.col_y_tolerance)
        # 折行可能把「(4~15周)(3-4节)」断成几段，先按括号配对把同一逻辑行接回来
        lines = _merge_unbalanced(lines, rx)
        if lay.entry_mode == "inline":
            out.extend(_parse_inline_column(day, lines, lay, rx, warnings))
        else:
            out.extend(_parse_block_column(day, lines, lay, rx, warnings))
    return out


def _is_note_line(text: str, size: float, entry_size: float, lay: Layout, rx: dict) -> bool:
    if size < entry_size - 0.15:
        return True
    if rx["codelist_re"] and rx["codelist_re"].match(text):
        return True
    if "周" not in text and rx["note_words_re"] and rx["note_words_re"].search(text):
        return True
    return False


def _hhmm_digits(s: str) -> str:
    """`800` -> `08:00`，`1430` -> `14:30`，`935` -> `09:35`。

    时刻制课表把上下课时间写成 3~4 位数字（不带冒号），必须补零还原成 HH:MM，
    否则 ics 里会拼出畸形时间。
    """
    s = (s or "").strip()
    if len(s) == 3:
        s = "0" + s
    if len(s) != 4 or not s.isdigit():
        raise ValueError("看不懂的时刻：%r" % (s,))
    h, m = int(s[:2]), int(s[2:])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("时刻不合法：%r" % (s,))
    return "%02d:%02d" % (h, m)


def _parse_detail(text: str, lay: Layout, rx: dict, rest_of: str = "tail"):
    """从一行文字里抠出 (周次, 起节, 止节, 剩下的部分, 时刻)；不是详情行返回 None。

    支持两种「节次」表示：
      * `(1-2节)`  —— 直接给了第几节；
      * `(800-935)` —— 只给了上下课时刻（时刻制）。这时 pfrom/pto 为 None，
        由后面的 resolve_periods() 按**用户设定的作息表**反查它是第几节。
    顺带把 `head`（周次/节次**之前**那一段）也带出来 —— 那基本就是课程名。
    """
    if not rx["weeks_re"]:
        return None
    wm = rx["weeks_re"].search(text)
    if not wm:
        return None
    pm = rx["period_re"].search(text) if rx["period_re"] else None
    cm = None
    if pm is None:
        cm = (rx["clock_re"] or rx["clock_range_re"] or None)
        cm = cm.search(text) if cm else None
        if cm is None:
            return None
    else:
        cm = None

    spans = [(wm.start(), wm.end())]
    pfrom = pto = None
    clock = None
    if pm is not None:
        spans.append((pm.start(), pm.end()))
        pfrom = int(pm.group("pfrom"))
        pto = int(pm.group("pto")) if pm.groupdict().get("pto") else pfrom
    else:
        spans.append((cm.start(), cm.end()))
        gd = cm.groupdict()
        try:
            if "m1" in gd:                      # 带冒号的 HH:MM-HH:MM
                clock = ("%02d:%s" % (int(gd["h1"]), gd["m1"]),
                         "%02d:%s" % (int(gd["h2"]), gd["m2"]))
            else:                               # 不带冒号的 800-935
                clock = (_hhmm_digits(gd["h1"]), _hhmm_digits(gd["h2"]))
        except ValueError:
            return None

    cut = max(e for _, e in spans)
    head_end = min(s for s, _ in spans)
    rest = text[cut:] if rest_of == "tail" else text[:head_end]
    weeks = _weeks_from_match(wm)
    return dict(weeks=weeks, pfrom=pfrom, pto=pto, clock=clock,
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
def _merge_unbalanced(lines: List[dict], rx: Optional[dict] = None) -> List[dict]:
    """把「括号没闭合」的行与**紧邻的一行**合并（有严格上限，别贪）。

    真实课表（尤其是网页打印/窄表格）会把一个逻辑行折成几段，断点可能落在括号中间：
    实测 `(4~15` / `周)(3` / `-4节)` 三行，任何一行单独看都不是「周次+节次」，
    于是被判成"续行"拼进上一门课的教室，导致整门课错位。

    但这条规则必须**保守**：OCR 文本经常漏掉一个右括号（识别不全），如果"只要不配平
    就一直往下接"，一整列内容会被吞成一行（实测 OCR 用例的记录数从 25 掉到 18）。
    所以限制三条：
      * 只补**一个**缺失的右括号（差 1 才合并，差 2 以上当作本来就是残缺文本）；
      * 最多再吞**一行**；
      * 下一行若本身像课程标题（匹配 title_re），不许吞 —— 那是另一门课。
    """
    if not lines:
        return lines
    out: List[dict] = []
    i = 0
    while i < len(lines):
        cur = dict(lines[i])
        text = cur["text"]
        for _ in range(1):                      # 最多再吞一行
            miss = (text.count("(") + text.count("（")
                    - text.count(")") - text.count("）"))
            if miss != 1 or i + 1 >= len(lines):
                break
            nxt = lines[i + 1]
            if rx and rx.get("title_re") and rx["title_re"].match(nxt["text"]):
                break                           # 下一行是另一门课的标题，别吞
            text = text + nxt["text"]
            cur["size"] = nxt["size"]
            i += 1
        cur["text"] = text
        out.append(cur)
        i += 1
    return out


def _weeks_from_match(wm) -> List[int]:
    """把 weeks_re 的匹配结果展开成周次列表。

    优先用 `expand_weeks_token` 展开**整段匹配文本** —— 它已经能处理
    `1~16`、`2~4(双)`、`2~6,8~17`、`第3周` 这些写法，一个入口覆盖全部，
    不会出现「某一种写法只吃到一半、另一半被静默丢掉」的问题
    （实测 `(2~6,8~17周)` 以前只得到 8~17，日历上少排了 2~6 周）。
    展开失败才退回 a/b 分组的老逻辑。
    """
    text = (wm.group(0) or "").strip().strip("()（）").strip()
    try:
        got = expand_weeks_token(text)
        if got:
            return got
    except LayoutError:
        pass
    gd = wm.groupdict()
    a = int(gd["a"])
    b = int(gd["b"]) if gd.get("b") else None
    return expand_weeks(a, b, gd.get("parity"))


def _is_cjk(ch: str) -> bool:
    """这个字符是不是汉字（用于判断换行是断在词中间还是断在字段之间）。"""
    return bool(ch) and ("\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf")


def _entry_has_detail(ent: dict, lay: Layout, rx: dict) -> bool:
    """这一条目里是否已经有「周次 + (节次 或 时刻)」的正文行。

    以前这里只认「节次」，于是「时刻制」课表（格子只印 800-935）的正文行判不出来，
    上一条目被当成「还没有正文的残段」，下一门课的标题就被拼进了它名字里 ——
    实测课程名变成 `数学物理方法001数学物理方法`。直接用 _parse_detail 判断才对：
    两条路径（节次/时刻）它就是唯一真相。
    """
    return any(_parse_detail(ln["text"], lay, rx) for ln in ent["body"])


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
            if (not prev["body"] or not _entry_has_detail(prev, lay, rx)) \
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
            # 换行续行拼接。这里有两种断行，必须区别对待：
            #   ① 断在「教室」和「教师」之间（`教西D-101` / `陈明远`）→ 要补空格，
            #      否则教师被并进教室名；
            #   ② 断在**词中间**（网页打印/窄表格常见：`孙` / `荧`，`教西` / `A-301`）
            #      → 不能补空格，否则人名/教室名被空格劈开，后面再也切不回来。
            # 判据：两侧都是汉字 → 词内断行（不补空格）；否则（一边是数字/字母/符号
            # 或一边是汉字）→ 字段边界（补空格）。
            prev_rest = details[-1]["rest"]
            add = text.strip()
            if prev_rest and add:
                both_cjk = _is_cjk(prev_rest[-1]) and _is_cjk(add[0])
                details[-1]["rest"] = prev_rest + add if both_cjk \
                    else prev_rest + " " + add
            else:
                details[-1]["rest"] = prev_rest or add
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


def resolve_periods(records: List[Record], period_times,
                    tolerance_min: int = 10) -> Tuple[List[Record], List[str]]:
    """把「只有时刻、没有节次」的记录，按**用户设定的作息表**反查出第几节。

    为什么反查而不是直接用 PDF 上的时刻：节次时间是用户按本校实际设的，那是唯一
    权威来源；PDF 印的时刻只作为「这是第几节」的线索。这样同一份课表在不同学校的
    作息下都能落到用户自己的时间上。

    容差：教务系统印的区间常和作息表差几分钟（实测 `800-935` 对应 1-2 节
    08:00-09:40，差 5 分钟）。默认允许 10 分钟，超过就对不上。

    对不上**不猜**：留在 warnings 里交给调用方明确报错，宁可让用户去补作息表。
    """
    if not period_times:
        return records, []
    tol = dt.timedelta(minutes=tolerance_min)
    unresolved: List[str] = []

    def to_t(hhmm: str):
        h, m = hhmm.split(":")
        return dt.time(int(h), int(m))

    for r in records:
        if r.pfrom is not None or not r.clock:
            continue
        start, end = to_t(r.clock[0]), to_t(r.clock[1])
        best = None
        for a in sorted(period_times):
            s, _e = period_times[a]
            if abs(_delta_min(to_t(s), start)) > tolerance_min:
                continue
            # 从 a 起往后找，看能连续覆盖到哪一节
            b = a
            while b in period_times:
                _s2, e2 = period_times[b]
                if abs(_delta_min(to_t(e2), end)) <= tolerance_min:
                    best = (a, b)
                    break
                b += 1
            if best:
                break
        if best:
            r.pfrom, r.pto = best
        else:
            unresolved.append("%s（格子里印的是 %s–%s）"
                              % (r.course, r.clock[0], r.clock[1]))
    return records, unresolved


def _delta_min(a: dt.time, b: dt.time) -> float:
    """两个时刻相差多少分钟（带符号，便于用 abs 判等）。"""
    return ((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))


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


def _plausible(value: str, own_label: str = "", other_labels=()) -> str:
    """元信息的合理性校验：不像「一个字段值」就丢掉。

    为什么要这一步：OCR 之后文字常常粘成一长串（实测「学期 计算机学院 星期二星期三
    星期四星期五」），而 grab() 只是取「标签后的第一个非空白串」，会把整串垃圾当成
    学期/学院塞进日历名。宁可留空（日历名退化成「课表」），也不要显示一串错的。

    注意**不能**简单按关键字排除：「计算机学院」是「学院：」的合法取值，虽然它含有
    「学院」二字。所以只排除「看起来是**别的**字段的标签」的情形。
    """
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) > 24:                      # 一个字段值不该这么长
        return ""
    if ":" in v or "：" in v:            # 粘进了下一个「标签：」
        return ""
    if "星期" in v:                      # 粘进了表头
        return ""
    for lab in other_labels:
        core = lab.rstrip("：: ")
        if not core or core == own_label.rstrip("：: "):
            continue
        if core and core in v:           # 值里出现了别的字段名 -> 说明粘串了
            return ""
    return v


def _parse_meta(text: str, lay: Layout, rx: dict) -> Meta:
    """从**纯文本**里抓元信息（学期/姓名/学号…）。

    收文本而不是收 page：PDF 与 OCR 两条路都要抓元信息，而 OCR 没有 page.get_text()。
    统一由 _spans_text() 从 span 还原一行行文字，两边就一致了。
    """
    meta = Meta()
    text = text or ""

    def grab(label, own=""):
        # 标签本身也要归一：版式里写的是全角「年级：」，而 NFKC 会把正文里的全角冒号
        # 变成半角「年级:」，不归一就一个也匹配不上（实测这份网页打印 PDF 就是这样）。
        lab = normalize_text(label)
        m = re.search(re.escape(lab) + r"\s*([^\s]+)", text)
        if m is None:
            # 再退一步：冒号全半角都试（标签里没有冒号时这条不生效）
            m = re.search(re.escape(lab.rstrip(":：")) + r"\s*[:：]?\s*([^\s]+)", text)
        return _plausible(m.group(1), own, labels) if m else ""

    labels = tuple((lay.meta_labels or {}).values())
    for field_name, label in (lay.meta_labels or {}).items():
        if hasattr(meta, field_name):
            setattr(meta, field_name, grab(label, label))

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
                  log=None, period_times=None) -> Tuple[Layout, List[str]]:
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
            res = parse_pdf(pdf_path, layout=lay, period_times=period_times)
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
            # 只有「时刻、没反查出节次」的记录无法做节次级冲突判定，跳过
            if not r.has_periods():
                continue
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
            r.day_name, r.period_desc(), r.course,
            compress_weeks(r.all_weeks()), r.room, "；".join(r.teacher_desc())))
    print("-" * 100)
    print(verify_parse(res))
