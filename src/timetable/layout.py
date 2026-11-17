# -*- coding: utf-8 -*-
"""课表「版式」定义：把 parser 从一个学校抽死，变成配置驱动。

为什么要这一层
    parser.py 原来的每一条正则都写着西工大教务系统的排版习惯：七列叫「星期一…星期日」、
    条目是「标题行 + 若干 (N周)(N-M节) 教室 教师」正文行、周次一定带括号写「1~16」、
    教室和教师靠空格分隔且教师是结尾 2~4 个汉字。换一所学校（哪怕同一所学校换个打印入口），
    这些假设会成批不成立。以前要改就得改源码，现在写一份版式即可。

三种条目模式（entry_mode）
    block   网格式，一个格子里先一行课程标题，下面几行是 (周次)(节次) 详情。
            西工大《学生课表》就是这种。
    inline  网格式，一个格子里每一行本身就是一条完整安排（课程名和周次节次在同一行）。
            很多教务系统的「学生课表打印」走这种。
    table   平面表，不是星期×节次的网格，而是一行一条课，列名是「课程 / 教师 / 时间 / 周次 / 地点」。
            正方、强智的「按课程列表」导出多数长这样 —— 这种模式**不依赖表头是七列星期**。

版本兼容性约定
    内置版式的 id 一旦发布就不要改名（用户脚本里写死了 --layout nwpu）。
    新增字段一律给默认值，老版式文件不用跟着改。
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 星期列的规范顺序（0=周一 … 6=周日），parser 内部一律用它
DAY_ALIASES: List[List[str]] = [
    ["星期一", "周一", "Mon", "Monday"],
    ["星期二", "周二", "Tue", "Tuesday"],
    ["星期三", "周三", "Wed", "Wednesday"],
    ["星期四", "周四", "Thu", "Thursday"],
    ["星期五", "周五", "Fri", "Friday"],
    ["星期六", "周六", "Sat", "Saturday"],
    ["星期日", "周日", "星期天", "Sun", "Sunday"],
]

# 正则字段里允许的具名分组（写错分组名在载入版式时就会被拦下，不会拖到解析时才发现）
GROUPS_ALLOWED = {
    "title_re": {"course", "code"},
    "weeks_re": {"a", "b", "parity"},
    "period_re": {"pfrom", "pto"},
    "clock_re": {"h1", "m1", "h2", "m2"},
    "clock_range_re": {"h1", "h2"},
    "detail_course_re": {"course", "code"},
}


class LayoutError(Exception):
    """版式本身有问题（缺字段、正则写错、分组名不认识）。"""


# ----------------------------------------------------------------- 教室/教师切分
def split_room_teacher(rest: str, how: str = "space_tail_name") -> Tuple[str, str]:
    """把「节次之后的那截文字」切成 (教室, 教师)。

    how:
      space_tail_name  按空格切，结尾若是 2~4 个汉字就当教师（西工大：教室在前教师在后）。
      space_tail_room  按空格切，结尾那段当教室，它前面那段当教师（多数教务是
                       「1-16周 张三 教三-101」这种教师在前、地点在末）。
      labels           显式标签优先：「教师：X 教室：Y」；没有标签再退回 space_tail_name。
      room_only        整段当教室，没有教师信息。
      none             不切，整段塞进教室字段（宁可显示得糙，也不要猜错）。
    """
    rest = (rest or "").strip()
    if not rest:
        return "", ""
    squeeze = lambda s: re.sub(r"\s+", "", s)        # noqa: E731

    if how == "none":
        return rest, ""
    if how == "room_only":
        return squeeze(rest), ""

    if how == "labels":
        t = re.search(r"(?:教师|任课教师|老师)\s*[:：]?\s*([^\s，,;；]+)", rest)
        r = re.search(r"(?:教室|地点|上课地点|场地)\s*[:：]?\s*([^\s，,;；]+)", rest)
        if t or r:
            return squeeze(r.group(1)) if r else "", squeeze(t.group(1)) if t else ""
        return _space_tail(rest, squeeze, teacher_at_end=True)

    if how == "space_tail_room":
        return _space_tail(rest, squeeze, teacher_at_end=False)

    if how == "auto":
        return _space_tail_auto(rest, squeeze)

    return _space_tail(rest, squeeze, teacher_at_end=True)


def _space_tail(rest: str, squeeze, teacher_at_end: bool) -> Tuple[str, str]:
    if teacher_at_end:
        m = re.match(r"^(.*?)\s+([\u4e00-\u9fff]{2,4})$", rest)
        if m:
            return squeeze(m.group(1)), squeeze(m.group(2))
        parts = rest.split()
        if len(parts) >= 2:
            last = parts[-1]
            if 2 <= len(last) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", last):
                return squeeze("".join(parts[:-1])), squeeze(last)
        return squeeze(rest), ""
    # 教师在前、地点在末：最后一段只要含数字或字母就当教室（"教三-101"、"A201"），
    # 它前面那段 2~4 个汉字当教师。两个条件都不满足就整段当教室，别瞎猜教师。
    parts = rest.split()
    if len(parts) >= 2:
        room, maybe_t = parts[-1], parts[-2]
        if _looks_like_room(room) and re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", maybe_t):
            # 再前面若还剩文字：像「1-16周」这种排课残留丢掉；看着像地点的一部分就留着，
            # 宁可教室名长一点也不要把信息吞掉（吞掉了用户在预览表里根本看不见）。
            prefix = [p for p in parts[:-2] if not _SCHEDULE_NOISE.match(p)]
            return squeeze(" ".join(prefix + [room])), squeeze(maybe_t)
    return squeeze(rest), ""


# 纯排课信息（周次/节次/单双），出现在教室名前面时是切分剩下的残渣
_SCHEDULE_NOISE = re.compile(r"^[0-9〇一二三四五六七八九十,\-~—–()（）\s]*"
                             r"(周|节|星期|单双|weeks?)[0-9,\-~—–()（）\s]*$", re.I)

# 纯汉字但一眼就是地点的词尾（"翱翔体育馆"、"农场实践基地" 里一个数字都没有，
# 却肯定是教室而不是人名；早先要求教室必须含数字，这类全被判成「教师在前」失败）
_PLACE_TAIL = re.compile(r"(楼|室|馆|场|厅|区|中心|基地|堂|园|教室|车间|体育场|体育馆|音乐厅)$")

# 教室与教师**粘在一起**、中间连空格都没有的情况：`040401钱渝`、`10205*温建萍`、
# `2010205*黄武英`、`4号楼2040401钱渝`。这是「时刻制」课表（如安师大）的典型写法 ——
# 靠空格切分的所有策略在这种行上都会整体失败（把教师并进教室名）。
# 切法：结尾那段 2~4 个汉字是教师，前面（必须以数字/字母/星号结尾）是教室。
# 关键的两个否定条件（都实测踩过）：
#   * 教师段不能是「号楼」「体育馆」这类地点词 —— 否则 `1号楼` 会被拆成 教室=1、教师=号楼；
#   * 教室段必须含数字/字母 —— 否则 `翱翔体育馆` 会被当成「教室=翱翔体育 教师=馆」。
_GLUED_ROOM_TEACHER = re.compile(
    r"^(?P<room>.*[0-9A-Za-z*#])"
    r"(?P<teacher>[\u4e00-\u9fff·]{2,4}(?:[/／、,，][\u4e00-\u9fff·]{2,4})*)$")
# 教师串里可能有多个（`金伟/何淼/朱仁贵`）
_MULTI_TEACHER = re.compile(r"^[\u4e00-\u9fff·]{2,4}(?:[/／、,，][\u4e00-\u9fff·]{2,4})*$")
# 这些词尾说明「这段是地点，不是人名」
_NOT_A_NAME_TAIL = re.compile(r"(号楼|楼|室|馆|场|厅|区|中心|基地|堂|园|号|层|校区)$")

# 纯汉字教室 + 教师粘连（`教东报告厅何平`）
_GLUED_CJK_ROOM_TEACHER = re.compile(
    r"^(?P<room>.*(?:楼|室|馆|场|厅|区|中心|基地|堂|园|号|层))"
    r"(?P<teacher>[\u4e00-\u9fff·]{2,4}(?:[/／、,，][\u4e00-\u9fff·]{2,4})*)$")


def _looks_like_room(tok: str) -> bool:
    return bool(re.search(r"[0-9A-Za-z]", tok)) or bool(_PLACE_TAIL.search(tok))


def _split_glued(whole: str, squeeze) -> Optional[Tuple[str, str]]:
    """把「教室+教师粘在一起、中间没有空格」的一段拆开；拆不出来返回 None。

    两种形态：
      1. 教室含数字/字母：`040401钱渝`、`2号楼2020104*郭文梅`、`4号楼2040401钱渝`
         —— 教师段之前的字符必须是数字/字母/星号；
      2. 教室是**纯汉字地点**：`教东报告厅何平`、`翱翔体育馆张伟`
         —— 教室段以「楼/室/馆/场/厅/区/中心/基地」等地点词收尾。

    两条都带同一个否定条件：教师段不能是地点词（否则 `1号楼` 会被拆成
    教室=1、教师=号楼）。宁可拆不出来（教师并进教室名，用户能在预览表看到），
    也不要把地点词当人名（那是"张冠李戴"，更误导）。
    """
    m = _GLUED_ROOM_TEACHER.match(whole)
    if m and _MULTI_TEACHER.match(m.group("teacher")) \
            and not _NOT_A_NAME_TAIL.search(m.group("teacher")):
        return squeeze(m.group("room")), squeeze(m.group("teacher"))
    m2 = _GLUED_CJK_ROOM_TEACHER.match(whole)
    if m2 and _MULTI_TEACHER.match(m2.group("teacher")) \
            and not _NOT_A_NAME_TAIL.search(m2.group("teacher")):
        return squeeze(m2.group("room")), squeeze(m2.group("teacher"))
    return None


def _space_tail_auto(rest: str, squeeze) -> Tuple[str, str]:
    """自动判「教室在前还是教师在前」。

    这是「通用版式」要解决的问题：不同教务系统的行尾顺序是反的，而我们没有任何
    先验知识。判据看行尾那一段的**形状**：
      * 像教室（含数字/字母，或以「楼/馆/室/场/厅/区/中心/基地」等结尾）
        → 它后面若还剩 2~4 个汉字，那是教师（教室在前）。
      * 不像教室、但本身是 2~4 个汉字，且它前面那段像教室
        → 它是教师（教师在前、教室在中间），把前面那段当教室。
      * 都不像 → 整段当教室，不猜教师（宁可少一个字段，也不要张冠李戴）。
    """
    parts = rest.split()
    if not parts:
        return "", ""
    # 先试「完全粘连」：整段就是 `教室+教师`，中间没有任何空格。
    # **必须先确认真的没有空格** —— 否则会把正常用空格分隔的
    # `实验大楼C座5楼 向礼琴` 也按「数字与汉字的分界」切开，得到
    # 教室=`实验大楼C座5`、教师=`楼向礼琴`（实测踩过）。
    if len(parts) > 1:
        whole = None
    else:
        whole = rest.strip()
    if whole:
        glued = _split_glued(whole, squeeze)
        if glued:
            return glued
    if len(parts) == 1:
        return squeeze(parts[0]), ""
    last, prev = parts[-1], parts[-2]
    if _looks_like_room(last) and re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", prev):
        prefix = [p for p in parts[:-2] if not _SCHEDULE_NOISE.match(p)]
        return squeeze(" ".join(prefix + [last])), squeeze(prev)
    if (not _looks_like_room(last)) and re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", last) \
            and any(_looks_like_room(p) for p in parts[:-1]):
        return squeeze(" ".join(parts[:-1])), squeeze(last)
    # 最后一段自己就是「教室+教师」粘在一起（前面还有别的地点段，OCR 常把
    # `实验大楼 C座5楼向礼琴` 认成两段）：把最后一段拆开，剩下的并入教室。
    glued = _split_glued(last, squeeze)
    if glued:
        prefix = [p for p in parts[:-1] if not _SCHEDULE_NOISE.match(p)]
        return squeeze(" ".join(prefix + [glued[0]])), squeeze(glued[1])
    return squeeze(rest), ""


# ------------------------------------------------------------------- 周次表达式
_WEEK_RANGE = re.compile(r"^\s*(\d{1,2})\s*(?:[-~—–]|至)\s*(\d{1,2})\s*$")


def expand_weeks_token(text: str, parity: Optional[str] = None) -> List[int]:
    """把「1-16」「1~8」「第3周」「1,3,5」「2-10(双)」展开成周次列表。

    inline / table 版式的周次写法比西工号的 `(1~16周)` 自由得多，所以单独一个容错的
    归一化函数；西工大仍走版式自带的 weeks_re，行为一字不变。
    """
    s = (text or "").strip()
    s = re.sub(r"^第", "", s)
    s = re.sub(r"\s*(?:周|星期|weeks?|w)\s*$", "", s, flags=re.I)
    m = re.search(r"[（(]\s*(单|双)\s*[）)]\s*$", s)
    if m:
        parity = parity or m.group(1)
        s = s[:m.start()]
    if not s:
        return []
    out: List[int] = []
    for chunk in re.split(r"[,，、;；]", s):
        chunk = chunk.strip()
        if not chunk:
            continue
        mr = _WEEK_RANGE.match(chunk)
        if mr:
            a, b = int(mr.group(1)), int(mr.group(2))
            if a > b:
                raise LayoutError("周次区间反了：%r" % text)
            out += list(range(a, b + 1))
            continue
        if re.fullmatch(r"\d{1,2}", chunk):
            out.append(int(chunk))
            continue
        raise LayoutError("看不懂周次写法：%r" % text)
    if parity == "单":
        out = [w for w in out if w % 2 == 1]
    elif parity == "双":
        out = [w for w in out if w % 2 == 0]
    return sorted(set(out))


# ------------------------------------------------------------------------- 版式
@dataclass
class Layout:
    id: str
    name: str
    note: str = ""
    source: str = "builtin"                    # builtin | file

    # --- 网格定位
    entry_mode: str = "block"                  # block | inline | table
    day_headers: List[List[str]] = field(default_factory=lambda: [list(x) for x in DAY_ALIASES])
    min_day_columns: int = 7                   # 至少要认全 7 天才认为是课表网格
    col_y_tolerance: float = 2.5               # 同一视觉行的 y 容差（像素/磅）

    # --- 条目与详情行
    title_re: Optional[str] = r"^[^(\s].*?[\s（(]?(?P<code>\d{2})$"
    detail_course_re: Optional[str] = None     # inline 模式：从详情行里取课程名
    weeks_re: Optional[str] = r"\((?P<a>\d+)(?:~(?P<b>\d+))?\s*(?:\((?P<parity>[单双])\))?\s*周\)"
    weeks_token_re: Optional[str] = None       # 宽松周次（供 expand_weeks_token 用）
    period_re: Optional[str] = r"\((?P<pfrom>\d+)\s*-\s*(?P<pto>\d+)\s*节\)"
    period_list_re: Optional[str] = None       # 「1,3,5节」这类（table 模式常见）
    clock_re: Optional[str] = None             # 直接印了上下课时刻（HH:MM-HH:MM）
    # 不带冒号的时刻区间，如 `(800-935)` = 8:00–9:35。有些教务系统把上下课时刻
    # 直接印在格子里而**不印第几节**，这是「时刻制」课表的核心特征。
    clock_range_re: Optional[str] = None

    # --- 切分与噪声
    room_teacher: str = "space_tail_name"
    note_words_re: str = r"(级|本科|公共课|全校|^大物实验$)"
    codelist_re: str = r"^[0-9A-Za-z;]+$"
    skip_text_re: str = r"(打印日期|^https?://|^\d+/\d+$)"
    body_end_re: str = r"^(备注|总学分)"
    # 自动识别时的同分裁判：正则越严格（越不可能误吃别的版式）分越高。
    # 只在「读出的教室数 / 冲突 / 警告」都打平时才用到，见 parser.detect_layout。
    specificity: int = 20

    # --- 元信息
    meta_labels: Dict[str, str] = field(default_factory=lambda: {
        "grade": "年级：", "college": "学院：", "major": "专业：", "klass": "班级：",
        "sid": "学号：", "name": "姓名：",
    })
    term_re: str = r"(?P<a>\d{4})\s*[-–—~至]\s*(?P<b>\d{4})\s*(?P<season>[秋冬春夏])"
    credits_re: str = r"总学分\s*[:：]?\s*(?P<v>[\d.]+)"
    # 打印日期：`2026/08/25`、`2026-08-25`、`2026.08.25` 都见过，一起收下。
    # **必须是完整日期形状**：以前写成 `[\d/\-.]+`，遇到被拼在一起的页码会吃成
    # `2026/08/251/2`（实测把 print_date 弄错）。
    print_date_re: str = (r"打印日期\s*[:：]?\s*"
                          r"(?P<v>\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2})")
    remark_re: str = (r"(?P<no>\d+)\s*[.、]\s*(?P<name>[^\d.、]+?)\s+"
                      r"(?P<code>[A-Za-z]{2,}\d+[.\d]*)\s*\((?P<a>\d+)\s*~\s*(?P<b>\d+)\)")
    remark_head_re: str = r"备注\s*[:：](.{0,400})"

    # --- 随版式一起走的作息与学期预设（键是给人看的名字）
    period_presets: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    default_preset: str = ""
    # 该学校一学期的常见起点，换版式时拿来预填「第 1 周周一」（仍要用户自己核对）
    semester_hint: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ 查询
    def preset_names(self) -> List[str]:
        return list(self.period_presets)

    def preset_times(self, name: str) -> Dict[int, Tuple[str, str]]:
        raw = self.period_presets.get(name)
        if raw is None and self.period_presets:
            raw = self.period_presets[next(iter(self.period_presets))]
        return _norm_periods(raw or {})

    def all_period_presets(self) -> Dict[str, Dict[int, Tuple[str, str]]]:
        return {k: _norm_periods(v) for k, v in self.period_presets.items()}

    def compiled(self) -> dict:
        """把正则预编译一遍；顺便校验具名分组拼错（早失败，别让错误漂到解析中途）。"""
        out = {}
        for fname, allowed in GROUPS_ALLOWED.items():
            pat = getattr(self, fname, None)
            if not pat:
                out[fname] = None
                continue
            try:
                rx = re.compile(pat)
            except re.error as exc:
                raise LayoutError("版式 %s 的 %s 不是合法正则：%s" % (self.id, fname, exc)) from exc
            bad = set(rx.groupindex) - allowed
            if bad:
                raise LayoutError("版式 %s 的 %s 用了不认识的分组名 %s（可用：%s）"
                                  % (self.id, fname, "、".join(sorted(bad)),
                                     "、".join(sorted(allowed))))
            out[fname] = rx
        for fname in ("note_words_re", "codelist_re", "skip_text_re", "body_end_re",
                      "term_re", "credits_re", "print_date_re", "remark_re",
                      "remark_head_re", "weeks_token_re", "period_list_re",
                      "clock_re", "clock_range_re"):
            pat = getattr(self, fname, None)
            if not pat:
                out[fname] = None
                continue
            try:
                out[fname] = re.compile(pat)
            except re.error as exc:
                raise LayoutError("版式 %s 的 %s 不是合法正则：%s" % (self.id, fname, exc)) from exc
        for how in (self.room_teacher,):
            if how not in ("space_tail_name", "space_tail_room", "labels", "room_only",
                           "none", "auto"):
                raise LayoutError("版式 %s 的 room_teacher 取值不认识：%r" % (self.id, how))
        if self.entry_mode not in ("block", "inline", "table"):
            raise LayoutError("版式 %s 的 entry_mode 取值不认识：%r" % (self.id, self.entry_mode))
        # 作息表也在载入时验一遍。留着坏数据会一路漂到生成 .ics 时才炸，
        # 那时候用户已经改了半天，根本想不到是版式里一个时间写成了 "8:00"。
        for name, raw in (self.period_presets or {}).items():
            _norm_periods(raw)
        if self.default_preset and self.default_preset not in self.period_presets:
            # 写错默认预设名更危险：会静默拿另一套（可能是另一个校区的）节次时间，
            # 每节课都"差十分钟"，这种错最难被发现。所以直接拒绝。
            raise LayoutError("版式 %s 的 default_preset %r 不在 period_presets 里（可用：%s）"
                              % (self.id, self.default_preset,
                                 "、".join(self.period_presets) or "（空）"))
        return out

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> dict:
        d = copy.deepcopy(self.__dict__)
        d.pop("source", None)
        return d

    @classmethod
    def from_dict(cls, d: dict, source: str = "file") -> "Layout":
        known = {f for f in cls.__dataclass_fields__}          # noqa: S1105
        unknown = set(d) - known
        if unknown:
            # 不静默忽略：写错字段名（weeks_regex vs weeks_re）会让版式悄悄退回默认值
            raise LayoutError("版式里有不认识的字段：%s（可能是拼错；可用：%s）"
                              % ("、".join(sorted(unknown)), "、".join(sorted(known))))
        lay = cls(**d)
        lay.source = source
        lay.compiled()                                        # 载入即校验
        return lay


_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def _norm_periods(raw: dict) -> Dict[int, Tuple[str, str]]:
    """{"1": ["08:30","09:15"]} -> {1: ("08:30","09:15")}

    小时不补零的写法（`8:00`）也收下，但**存成 "08:00"**。以前只校验「看起来像时间」
    就放行，结果 ics._stamp 用 `"%04d%02d%02dT%s%s00"` 拼出来是 `T80000` —— 一个畸形
    的 iCalendar 时间，日历端要么拒收要么排到错误时刻。输入宽松、存储规范，两头都对。
    """
    out: Dict[int, Tuple[str, str]] = {}
    for k, v in (raw or {}).items():
        try:
            idx = int(k)
        except (TypeError, ValueError) as exc:
            raise LayoutError("作息预设的节次要写成整数，得到 %r" % (k,)) from exc
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            raise LayoutError("第 %d 节的作息时间要是 [开始, 结束]，得到 %r" % (idx, v))
        norm = []
        for t in v:
            m = _HHMM.match(str(t).strip())
            if not m:
                raise LayoutError("第 %d 节的时间格式应为 HH:MM，得到 %r" % (idx, t))
            hh, mi = int(m.group(1)), int(m.group(2))
            if not (0 <= hh <= 23 and 0 <= mi <= 59):
                raise LayoutError("第 %d 节的时间不是合法时刻：%r" % (idx, t))
            norm.append("%02d:%02d" % (hh, mi))
        out[idx] = (norm[0], norm[1])
    return out


# ---------------------------------------------------------------- 内置：西工大
_NWPU_CHANGAN = {str(i): list(v) for i, v in {
    1: ("08:30", "09:15"), 2: ("09:25", "10:10"), 3: ("10:30", "11:15"),
    4: ("11:25", "12:10"), 5: ("12:20", "13:05"), 6: ("13:05", "13:50"),
    7: ("14:00", "14:45"), 8: ("14:55", "15:40"), 9: ("16:00", "16:45"),
    10: ("16:55", "17:40"), 11: ("19:00", "19:45"), 12: ("19:55", "20:40"),
    13: ("20:40", "21:25"),
}.items()}

_NWPU_YOUYI_WINTER = {str(i): list(v) for i, v in {
    1: ("08:00", "08:50"), 2: ("09:00", "09:50"), 3: ("10:10", "11:00"),
    4: ("11:10", "12:00"), 5: ("12:20", "13:05"), 6: ("13:05", "13:50"),
    7: ("14:00", "14:50"), 8: ("15:00", "15:50"), 9: ("16:10", "17:00"),
    10: ("17:10", "18:00"), 11: ("19:00", "19:50"), 12: ("20:00", "20:50"),
}.items()}

_NWPU_YOUYI_SUMMER = {str(i): list(v) for i, v in {
    1: ("08:00", "08:50"), 2: ("09:00", "09:50"), 3: ("10:10", "11:00"),
    4: ("11:10", "12:00"), 5: ("12:20", "13:05"), 6: ("13:05", "13:50"),
    7: ("14:30", "15:20"), 8: ("15:30", "16:20"), 9: ("16:40", "17:30"),
    10: ("17:40", "18:30"), 11: ("19:30", "20:20"), 12: ("20:30", "21:20"),
}.items()}

# --------------------------------------------------------- 内置：通用（默认）
# 目标是「不假设任何一所学校」。能认的东西都是课表这种文档的**结构共性**：
#   七列星期、格子里有课程名、有「周次」和「节次」、有教室和教师（顺序不假设）。
# 刻意放宽松的部分：
#   * 周次/节次的括号可有可无（`(1~16周)` / `1-16周` / `第1-16周` 都认）
#   * 节次允许单节写法（`3节`）
#   * 教室与教师的先后顺序由 _space_tail_auto 按形状现场判断，不写死
# 通用作息：45 分钟/节，覆盖到第 13 节（不少学校是 4 早 + 5 午 + 4 晚）。
# **这是一份可编辑的默认值，不是任何一所学校的真实作息** —— 实际时间请用户按本校
# 课表改（界面「节次时间…」），或用 --period-times 指定。PDF 上若印了上下课时刻，
# 程序会优先用 PDF 的，根本不看这张表。
GENERIC_PERIODS = {str(i): list(v) for i, v in {
    1: ("08:00", "08:45"), 2: ("08:55", "09:40"),
    3: ("10:00", "10:45"), 4: ("10:55", "11:40"),
    5: ("13:30", "14:15"), 6: ("14:25", "15:10"),
    7: ("15:20", "16:05"), 8: ("16:15", "17:00"),
    9: ("17:10", "17:55"),
    10: ("19:00", "19:45"), 11: ("19:55", "20:40"),
    12: ("20:50", "21:35"), 13: ("21:45", "22:30"),
}.items()}

GENERIC = Layout(
    id="generic",
    name="通用课表（自动判断结构）",
    note="不假设具体学校：括号可有可无、节次可单节也可只印时刻、课程代码 2~4 位、"
         "教室与教师谁在前（甚至粘连在一起）由程序判断。"
         "对不上时用「看结构」看程序眼里的版面，再写一份版式文件。",
    entry_mode="block",
    specificity=25,                 # 比写死括号的版式宽松，但结构要求仍然具体
    # 课程代码放宽到 2~4 位（`078`、`001`、`20`），并允许代码前有空格或全角括号
    title_re=r"^[^(\s].*?[\s（(]?(?P<code>\d{2,4})$",
    weeks_re=r"[(（]?(?P<a>\d{1,2})(?:\s*[-~—–至]\s*(?P<b>\d{1,2}))?"
             r"\s*(?:[(（](?P<parity>[单双])[)）])?\s*周[)）]?",
    period_re=r"[(（]?(?P<pfrom>\d{1,2})(?:\s*[-~—–至]\s*(?P<pto>\d{1,2}))?\s*节[)）]?",
    # 时刻制：`(800-935)` = 8:00–9:35，`(1430-1655)` = 14:30–16:55
    clock_range_re=r"[(（]?(?P<h1>\d{3,4})\s*[-~—–]\s*(?P<h2>\d{3,4})[)）]?",
    room_teacher="auto",
    period_presets={"通用作息（45 分钟/节，可编辑）": GENERIC_PERIODS},
    default_preset="通用作息（45 分钟/节，可编辑）",
)

NWPU = Layout(
    id="nwpu",
    name="西北工业大学 · 学生课表",
    note="教务系统「学生课表」PDF：七列星期一~星期日，格子里是标题行 + (N周)(N-M节) 教室 教师。",
    entry_mode="block",
    specificity=30,                 # 周次节次必须带括号，最不可能误吃别的版式
    period_presets={
        "西北工业大学 · 长安校区": _NWPU_CHANGAN,
        "西北工业大学 · 友谊校区（冬季 10.1-4.30）": _NWPU_YOUYI_WINTER,
        "西北工业大学 · 友谊校区（夏季 5.1-9.30）": _NWPU_YOUYI_SUMMER,
    },
    default_preset="西北工业大学 · 长安校区",
    semester_hint={"week1_monday": "2026-08-31", "n_weeks": "19"},
)

# ------------------------------------------------- 内置：网格式，每行自带课程名
# 常见于「我的课表」类打印页：
#   高等数学   1-16周  1-2节  张伟  教三-101
INLINE = Layout(
    id="inline-grid",
    name="网格式课表（一行一条安排）",
    note="七列星期，格子里每行自带课程名、周次、节次，教师与教室在行尾。",
    entry_mode="inline",
    specificity=15,                 # 比 nwpu 宽松（括号可有可无），自动识别时该让位
    title_re=None,
    # 只描述「课程代码长什么样」，不用它去抓课程名 —— 课程名由 parser 从
    # 「周次/节次之前那一段」直接取（几何位置已经知道了，再写正则去猜只会猜错）。
    # 这里没有 course 分组，所以匹配到的代码会被从名字里删掉，剩下的就是课程名。
    detail_course_re=r"[（(【\[]\s*(?P<code>[0-9A-Za-z]{4,}[.\-_0-9A-Za-z]*)\s*[）)】\]]",
    weeks_re=r"(?:(?P<a>\d{1,2})(?:[-~—–](?P<b>\d{1,2}))?\s*(?:[(（](?P<parity>[单双])[)）])?\s*周)",
    period_re=r"(?:第?\s*(?P<pfrom>\d{1,2})\s*[-~—–]\s*(?P<pto>\d{1,2})\s*节)",
    room_teacher="space_tail_room",
    codelist_re=r"^[0-9A-Za-z;]+$",
    note_words_re=r"(级|本科|公共课|全校)",
)

# ----------------------------------------------------- 内置：平面表（一行一条课）
TABLE = Layout(
    id="flat-table",
    name="平面表格（列名：课程/教师/时间/周次/地点）",
    note="不是星期×节次的网格，而是一行一条课。正方/强智的「课表列表」导出多半是这种。",
    entry_mode="table",
    specificity=10,                 # 靠列名认，跟网格版式互斥，一般不会被误选
    title_re=None,
    weeks_re=None,
    # 「周」字可有可无：表格里周次列常常只写 `2-10(双)` 或 `1,3,5`，要求带「周」字
    # 会把这类整行丢掉（合成 PDF 实测：8 行只解析出 6 行）。
    weeks_token_re=r"(?:第?\s*\d{1,2}(?:\s*[-~—–]\s*\d{1,2})?(?:\s*[(（][单双][)）])?\s*周"
                   r"|\d{1,2}(?:\s*[,，、]\s*\d{1,2})+\s*周"
                   r"|第\s*\d{1,2}\s*周"
                   r"|\d{1,2}\s*[-~—–]\s*\d{1,2}(?:\s*[(（][单双][)）])?"
                   r"|\d{1,2}(?:\s*[,，、]\s*\d{1,2})+)",
    period_re=None,
    period_list_re=r"第?\s*(?P<pfrom>\d{1,2})(?:\s*[-~—–]\s*(?P<pto>\d{1,2}))?\s*(?:节|$|\s)",
    clock_re=r"(?P<h1>\d{1,2})[:：](?P<m1>\d{2})\s*[-~—–]\s*(?P<h2>\d{1,2})[:：](?P<m2>\d{2})",
    room_teacher="space_tail_room",
    meta_labels={"name": "姓名", "sid": "学号", "term": "学期"},
    term_re=r"(?P<a>\d{4})\s*[-–—~至]\s*(?P<b>\d{4})\s*(?P<season>[秋冬春夏])?",
)

BUILTIN: Dict[str, Layout] = {x.id: x for x in (GENERIC, INLINE, TABLE, NWPU)}
# 默认用「通用」：不假设学校。nwpu 那份只是结构家族里的一个具体样例，排在内置表末尾。
DEFAULT_LAYOUT_ID = GENERIC.id


# ------------------------------------------------------------------------ 注册表
class Registry:
    """内置版式 + 用户自己写的版式文件（JSON）。"""

    def __init__(self, layouts: Optional[List[Layout]] = None):
        self._items: Dict[str, Layout] = {}
        for lay in (layouts if layouts is not None else BUILTIN.values()):
            self._items[lay.id] = lay

    def add(self, lay: Layout, replace: bool = False) -> None:
        if lay.id in self._items and not replace:
            raise LayoutError("版式 id %r 已存在（换一份内置版式请用 --layout 指定它）" % lay.id)
        self._items[lay.id] = lay

    def get(self, key: str) -> Layout:
        lay = self._items.get((key or "").strip())
        if lay is None:
            raise LayoutError("没有版式 %r，可用：%s"
                              % (key, "、".join(sorted(self._items))))
        return lay

    def has(self, key: str) -> bool:
        return (key or "").strip() in self._items

    def ids(self) -> List[str]:
        return list(self._items)

    def all(self) -> List[Layout]:
        return list(self._items.values())

    def describe(self) -> str:
        return "  ".join("%s=%s" % (k, v.name) for k, v in self._items.items())


REGISTRY = Registry()


def load_layout_file(path: str) -> Layout:
    """从 JSON 读一份版式。缺字段就用默认值，**多余的字段直接报错**。"""
    import io
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as exc:
        raise LayoutError("读不到版式文件：%s" % exc) from exc
    except ValueError as exc:
        raise LayoutError("版式文件不是合法 JSON：%s（%s）" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise LayoutError("版式文件最外层要是对象（字段名 -> 值）：%s" % path)
    if not data.get("id") or not data.get("name"):
        raise LayoutError("版式文件必须有 id 和 name：%s" % path)
    return Layout.from_dict(data, source=path)


def load_layout_dir(directory: str, reg: Optional[Registry] = None) -> List[Layout]:
    """把目录里的 *.json 都读进注册表（用户可以把学校版式丢在一个文件夹里）。"""
    import glob as _glob
    import os
    reg = reg if reg is not None else REGISTRY
    out = []
    for p in sorted(_glob.glob(os.path.join(directory, "*.json"))):
        try:
            lay = load_layout_file(p)
        except LayoutError as exc:
            raise LayoutError("%s：%s" % (p, exc)) from exc
        reg.add(lay, replace=True)
        out.append(lay)
    return out


LAYOUT_FILE_TEMPLATE = {
    "id": "my-school",
    "name": "某某大学 · 我的课表",
    "note": "先跑 --dump-layout 看自己学校 PDF 的真实结构，再照着改这里",
    "entry_mode": "block",
    "title_re": r"^[^(\s].*?[\s（(]?(?P<code>\d{2})$",
    "weeks_re": r"\((?P<a>\d+)(?:~(?P<b>\d+))?\s*(?:\((?P<parity>[单双])\))?\s*周\)",
    "period_re": r"\((?P<pfrom>\d+)\s*-\s*(?P<pto>\d+)\s*节\)",
    "room_teacher": "space_tail_name",
    "period_presets": {"我校作息": {"1": ["08:00", "08:50"], "2": ["09:00", "09:50"]}},
    "default_preset": "我校作息",
    "semester_hint": {"week1_monday": "2026-09-01"},
    "meta_labels": {"name": "姓名：", "sid": "学号："},
}


def export_template(path: str) -> None:
    import io
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(LAYOUT_FILE_TEMPLATE, f, ensure_ascii=False, indent=2)
        f.write("\n")
