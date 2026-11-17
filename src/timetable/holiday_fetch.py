# -*- coding: utf-8 -*-
"""联网获取 / 按年份生成中国法定节假日与调休安排。

为什么要这个模块
    holidays.py 里的 DEFAULT_HOLIDAYS 是硬编码的 2026 年安排，只够本学期用。
    换到 2027、2028 学期，硬编码不会报错、只会**安安静静算错**（该扣的课没扣、
    不该扣的照扣）——这正是本项目最不能接受的失败方式。所以按自然年从网上取
    国务院办公厅的正式安排（数据源 NateScarlet/holiday-cn，自动跟踪 gov.cn 公告），
    取不到就抛一条**写得清楚、给足出路**的错误，绝不静默返回空安排。

降级链（fetch_year 的取数顺序）
    调用方 session（内存）→ 模块级 _MEM（内存）→ 磁盘缓存 → 联网
    （主源 jsdelivr 失败自动换备用源 raw.githubusercontent）→ HolidayFetchError。

设计底线（与 doctor.py 同一口径：宁可明确报错，也不要静默算错）
    * 服务端 JSON 的顶层结构、year、每一条 days 的 name/date/isOffDay 全部严格
      校验，任何一条不符就 HolidayFetchError，绝不让 TypeError/KeyError 冒到调用方。
    * 严格区分「404/文件不存在 = 该年度通知尚未发布或镜像缺文件」与「网络不通」：
      两者的出路完全不同。未来年份 404 尤其常见——次年的安排一般要等到头一年
      11 月初才由国务院办公厅公布，届时 holiday-cn 才会多出该年的 JSON。
    * 缓存目录用**可写的用户目录**（Windows 优先 %LOCALAPPDATA%\\TimetableToCalendar\\
      holidays，否则 ~/.timetable2calendar/holidays），绝不依赖程序目录——程序
      完全可能被只读分发（绿色版、网盘同步目录、Program Files）。
    * 只读操作：不改动 holidays.py，不碰任何配置文件；唯一的写是缓存文件本身，
      且用临时文件 + os.replace 原子落盘，崩了也不会留下半截缓存。
    * 联网只用标准库 urllib.request（本项目不许新增第三方运行时依赖），且
      **不禁用代理**：HTTP_PROXY / HTTPS_PROXY / NO_PROXY 照常生效——校园网/公司网
      没有代理反而出不去，禁掉代理等于把最需要联网的环境挡在门外。

与 holidays.py 的关系
    产出的就是 Holiday / MakeupDay 对象，语义完全一致：
      isOffDay=true 的**连续同名**日期合并成一个 Holiday（名称原样保留，可能是
      「国庆节、中秋节」这种复合名；不同名或中间隔着一天就不合并）；
      isOffDay=false → MakeupDay(date, None, "<节日名>调休上班")，即高校通行做法
      「调休日照常按当天星期几上课，不补别的周的课」（见 holidays.py 模块注释）。
    to_holiday_file_text() 可把结果导出成 load_holiday_file 认的纯文本 DSL，
    往返一致，方便用户拿到联网数据后照国务院通知手工微调。

坑与取舍（都是踩过或想清楚的）
    * days 数组服务端是升序的，但我们**不信任它**，自己按日期排序——镜像被
      手工编辑过、或上游改了排序，都必须得到同样的结果。
    * 调休日会打断合并：9-20（国庆调休上班）恰好挨着中秋前的日期，如果不打断，
      理论上可能把不同区间粘在一起。
    * DSL 每行按空格分词，所以名称含空格/「#」的安排**无法**无损往返：与其写出
      一行读回来变成另一个名字（静默算错），不如在导出时直接报错。
    * 磁盘缓存文件名只按自然年（{year}.json）。换了 --holidays-url 镜像后，
      旧镜像的缓存不会自动失效——需要刷新时先 clear_cache() 再取。
    * session 里存的是**原始对象**，返回前一律换成 from_cache=True 的浅拷贝：
      调用方改返回列表不会污染缓存，缓存命中也如实告诉用户「这次没联网」。
"""
from __future__ import annotations

import datetime as dt
import glob
import io
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import List, Optional

from . import holidays as hol_mod
from .holidays import Holiday, MakeupDay

__all__ = ["HolidayFetchError", "YearHolidays", "fetch_year", "holidays_for_range",
           "derive_semester_years", "to_holiday_file_text", "sources", "cache_dir",
           "clear_cache"]

# UA 写得像个正常客户端：jsdelivr / raw.githubusercontent 对自定义 UA 都放行，
# 写空 UA 反而容易被 CDN 的 Bot 规则误伤。
_USER_AGENT = ("TimetableToCalendar/1.0 (+https://github.com/NateScarlet/holiday-cn "
               "data consumer; offline timetable->calendar tool; python-urllib)")

# 主源 jsdelivr（国内可达性通常好于直连 GitHub），备源 raw.githubusercontent。
# 两个都是 NateScarlet/holiday-cn 的 master 分支，按年份一个 JSON 文件。
DEFAULT_URL_TEMPLATES = [
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
]

# 进程内内存缓存：year -> YearHolidays。GUI 一次会话里反复刷新、CLI 一年取多次，
# 都不必再读盘。tkinter 是单线程场景；CPython 下 dict 赋值原子，最坏情况是两个
# 线程各自重复联网取一次同一年的数据，结果一致，不会算错，故不加锁。
_MEM = {}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HolidayFetchError(Exception):
    """取数/校验彻底失败。

    message 是给**用户看**的：写明了试过哪些地址、判断的原因（404 未发布还是
    网络不通）、以及三条出路（--holidays-file 手写文件、界面「导出模板」、
    --holidays-url 换镜像）。CLI/GUI 直接原文打印即可，不要再吞掉。
    """


@dataclass
class YearHolidays:
    """一个（或多个）自然年的节假日安排，字段类型与 holidays.py 完全一致。"""
    holidays: list      # List[Holiday]，按 start 升序
    makeup_days: list   # List[MakeupDay]，按 date 升序
    years: list         # 覆盖的自然年（升序；单年取数时就是一个元素）
    source: str         # 实际取到数据的 URL（跨年合并时用 " + " 连接）
    papers: list        # 国务院办公厅通知原文链接，溯源用（可能多个）
    fetched_at: str     # ISO 8601 带时区偏移，本地时间
    from_cache: bool    # True = 本次命中内存/磁盘缓存，没有联网

    def describe(self) -> str:
        """两行摘要：放假/调休明细 + 数据来源。供 CLI/GUI 直接打印。"""
        first = hol_mod.describe(self.holidays, self.makeup_days)
        tail = "来源：%s；数据年份：%s；抓取于 %s%s" % (
            self.source,
            "、".join(str(y) for y in self.years),
            self.fetched_at,
            "（命中缓存，本次未联网）" if self.from_cache else "")
        return first + "\n" + tail


# --------------------------------------------------------------- 公开接口

def sources() -> List[str]:
    """默认的主/备 URL 模板（--holidays-url 就是用来覆盖它们的）。"""
    return list(DEFAULT_URL_TEMPLATES)


def fetch_year(year: int, *, timeout: float = 10.0, url_template: Optional[str] = None,
               use_cache: bool = True,
               session: Optional[dict] = None) -> YearHolidays:
    """取一个自然年的节假日与调休安排。

    url_template : None 用 sources() 的主/备链自动降级；给单个模板就只用它
                   （含 {year} 占位，支持 http(s):// 与 file:///，后者给内网
                   镜像/离线包用）。
    use_cache    : False 时跳过全部缓存（内存+磁盘）直接联网，也不写磁盘，
                   供「刷新」按钮用；True（默认）走完整降级链。
    session      : 调用方自持的 {年份:int -> YearHolidays} 字典，多次调用共享
                   （如 holidays_for_range 传同一个），同进程内每年只取一次。
    timeout      : 单个源的网络超时（秒）。

    任何缓存都救不了场时抛 HolidayFetchError，错误信息可操作，绝不返回空安排。
    """
    year = _check_year(year)
    templates = _check_templates(url_template)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise HolidayFetchError("timeout 要是一个数字（秒），收到 %r" % (timeout,)) from None
    if timeout <= 0:
        raise HolidayFetchError("timeout 必须为正数，收到 %r" % (timeout,))

    if use_cache:
        # 1. 调用方 session → 2. 进程内 _MEM → 3. 磁盘缓存
        hit = (session.get(year) if session is not None else None) or _MEM.get(year)
        if isinstance(hit, YearHolidays):
            if session is not None and year not in session:
                session[year] = hit
            return _as_cache_hit(hit)
        cached = _read_cache(year)
        if cached is not None:
            _MEM[year] = cached
            if session is not None:
                session[year] = cached
            return _as_cache_hit(cached)

    failures = []  # [(尝试的 URL, 失败种类, 细节)]，种类决定最后一段「可能原因」
    for template in templates:
        url = _fill(template, year)
        try:
            obj, norm, papers = _download(url, year, timeout)
        except _TryFailed as exc:
            failures.append((url, exc.kind, exc.detail))
            continue

        holidays, makeup = _build(norm)
        yh = YearHolidays(holidays=holidays, makeup_days=makeup, years=[year],
                          source=url, papers=papers, fetched_at=_now_iso(),
                          from_cache=False)
        if use_cache:
            _MEM[year] = yh
            if session is not None:
                session[year] = yh
            _write_cache(year, url, yh.fetched_at, obj)  # 尽力而为，见函数注释
        return yh

    raise HolidayFetchError(_failure_message(year, failures))


def holidays_for_range(d1: dt.date, d2: dt.date, **kw) -> YearHolidays:
    """取 [d1, d2] 覆盖到的每个自然年并合并（秋季学期几乎必然跨两个自然年）。

    kw 原样传给 fetch_year（timeout / url_template / use_cache / session）。
    返回的是**整年**安排、按日期升序合并，不做区间裁剪：哪些落在本学期内、
    哪些是别的学期的，交给 doctor.py 去提示（它有学期边界，提示也更有依据）。
    """
    for name, v in (("d1", d1), ("d2", d2)):
        if not isinstance(v, dt.date) or isinstance(v, dt.datetime):
            # datetime 也是 date 子类，但混进来多半是调用方忘了 .date()，
            # 与其悄悄接受一个带时分秒的日期，不如报错让人改。
            raise HolidayFetchError("%s 需要 datetime.date，收到 %r" % (name, v))
    if d2 < d1:
        raise HolidayFetchError("区间反了：%s > %s" % (d1, d2))

    session = kw.pop("session", None)
    parts = [fetch_year(y, session=session, **kw)
             for y in range(d1.year, d2.year + 1)]

    holidays = sorted((h for p in parts for h in p.holidays), key=lambda h: h.start)
    makeup = sorted((m for p in parts for m in p.makeup_days), key=lambda m: m.date)
    papers, srcs = [], []
    for p in parts:
        for s in p.papers:
            if s not in papers:
                papers.append(s)
        if p.source not in srcs:
            srcs.append(p.source)
    return YearHolidays(holidays=holidays, makeup_days=makeup,
                        years=[p.years[0] for p in parts],
                        source=" + ".join(srcs), papers=papers,
                        fetched_at=max(p.fetched_at for p in parts),
                        from_cache=all(p.from_cache for p in parts))


def derive_semester_years(week1_monday: dt.date, n_weeks: int) -> List[int]:
    """本学期（第 1 周周一 + 总周数）覆盖哪些自然年，升序。

    结束日算法与 doctor.term_of 完全一致：week1 + n_weeks 周 - 1 天。
    典型：2026-08-31 起 20 周 → [2026, 2027]（秋季学期跨两个自然年，
    取 2027 年数据时若国务院通知还没发布，fetch_year 会给出对应提示）。
    """
    if not isinstance(week1_monday, dt.date) or isinstance(week1_monday, dt.datetime):
        raise HolidayFetchError("week1_monday 需要 datetime.date，收到 %r" % (week1_monday,))
    if isinstance(n_weeks, bool) or not isinstance(n_weeks, int) or n_weeks < 1:
        raise HolidayFetchError("n_weeks 要是正整数（周数），收到 %r" % (n_weeks,))
    last = week1_monday + dt.timedelta(weeks=n_weeks) - dt.timedelta(days=1)
    return list(range(week1_monday.year, last.year + 1))


_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def to_holiday_file_text(yh: YearHolidays) -> str:
    """导出成 holidays.load_holiday_file 认的纯文本 DSL，供用户手工编辑。

    这是「联网取数」与「手写文件」两条路会师的接口：GUI 取完数点「导出」，
    用户照校历/通知改两行，再用 --holidays-file 喂回来。

    往返保证：本函数写出的 Holiday（含名称）与 load_holiday_file 读回的完全一致；
    MakeupDay 只能保证 (date, follow_weekday) 一致——DSL 一行「调休 日期 [周几]」
    装不下 note 字段，读回来固定是「调休上班」。这一点写在了导出文件的头注释里。
    """
    lines = [
        "# 节假日安排文件（纯文本，UTF-8）——由 holiday_fetch 从联网数据生成，可手工微调",
        "# 用法：命令行 --holidays-file 本文件；格式说明见 timetable/holidays.py 的模板。",
    ]
    for p in yh.papers:
        lines.append("# 依据（国务院办公厅通知）：%s" % p)
    lines.append("# 数据来源：%s" % yh.source)
    lines.append("# 抓取时间：%s%s；覆盖年份：%s" % (
        yh.fetched_at, "（缓存）" if yh.from_cache else "",
        "、".join(str(y) for y in yh.years)))
    lines.append("# 注意：导出时的调休备注（哪个节日的调休）无法写进 DSL，读回来统一叫「调休上班」。")
    lines.append("")
    lines.append("# ―― 放假 ――")

    for h in sorted(yh.holidays, key=lambda h: (h.start, h.name)):
        if not h.name:
            raise HolidayFetchError("导出节假日文件失败：有节假日的名称是空的")
        if re.search(r"\s", h.name) or "#" in h.name:
            # DSL 按空白分词、# 视为行内注释起点，这种名字写出去就再也读不回来
            # （或读回来变成另一个名字）。宁可报错，不产出「看似成功实则错」的文件。
            raise HolidayFetchError(
                "导出节假日文件失败：名称「%s」含空格或 #，"
                "会破坏节假日文件的行格式，请改成不含空格/ # 的名字" % h.name)
        if h.end < h.start:
            raise HolidayFetchError(
                "导出节假日文件失败：%s 的区间反了（%s > %s）" % (h.name, h.start, h.end))
        if h.start == h.end:
            lines.append("放假 %s %s" % (h.start.isoformat(), h.name))
        else:
            lines.append("放假 %s %s %s" % (h.start.isoformat(), h.end.isoformat(), h.name))

    if yh.makeup_days:
        lines.append("")
        lines.append("# ―― 调休上班 ――")
    for m in sorted(yh.makeup_days, key=lambda m: m.date):
        if m.follow_weekday is None:
            lines.append("调休 %s" % m.date.isoformat())
        elif 0 <= m.follow_weekday <= 6:
            lines.append("调休 %s %s" % (m.date.isoformat(), _WEEKDAY_CN[m.follow_weekday]))
        else:
            raise HolidayFetchError(
                "导出节假日文件失败：调休日 %s 的 follow_weekday=%r 超出 0~6"
                % (m.date, m.follow_weekday))
    return "\n".join(lines) + "\n"


def cache_dir() -> str:
    """磁盘缓存目录（可写的用户目录；不存在则顺手创建）。

    优先级：
      1. 环境变量 TIMETABLE_HOLIDAY_CACHE_DIR —— 测试与「便携模式」的后门；
      2. Windows：%LOCALAPPDATA%\\TimetableToCalendar\\holidays；
      3. 兜底：~/.timetable2calendar/holidays。
    绝不放程序目录：可能被只读分发。目录创建失败时不抛错（缓存只是加速手段，
    读写路径都按「可选」处理），真要写文件失败还有第二层兜底。
    """
    env = os.environ.get("TIMETABLE_HOLIDAY_CACHE_DIR", "").strip()
    if env:
        base = env
    else:
        local = os.environ.get("LOCALAPPDATA", "").strip() if os.name == "nt" else ""
        if local:
            base = os.path.join(local, "TimetableToCalendar", "holidays")
        else:
            base = os.path.join(os.path.expanduser("~"), ".timetable2calendar", "holidays")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return base


def clear_cache(year: Optional[int] = None) -> int:
    """删除磁盘缓存（year=None 删全部），并同步丢掉内存缓存（含 _MEM）。

    返回删除的磁盘文件个数。缓存文件本来就不存在不算错误。
    注意：调用方自持的 session 字典不在本函数管辖——里面的旧数据由调用方
    负责丢弃（换新 session 或 session.clear()），这是故意的：内存缓存归调用方。
    """
    if year is not None:
        _check_year(year)
        _MEM.pop(year, None)
        targets = [os.path.join(cache_dir(), "%d.json" % year)]
    else:
        _MEM.clear()
        # 只删形如 2026.json 的文件，别把用户顺手放进这个目录的其它文件误伤
        targets = sorted(glob.glob(os.path.join(cache_dir(), "[0-9][0-9][0-9][0-9].json")))
    removed = 0
    for p in targets:
        try:
            os.remove(p)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise HolidayFetchError("缓存文件删不掉（被占用或只读？）：%s\n%s"
                                    % (p, exc)) from exc
    return removed


# --------------------------------------------------------------- 内部实现

class _TryFailed(Exception):
    """单个源的一次尝试失败。kind: notfound / network / http / data。
    只在模块内部流转，最终汇入 _failure_message 给用户看。"""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _check_year(year) -> int:
    # bool 是 int 的子类：True 会变成 1 年，必须先堵死
    if isinstance(year, bool) or not isinstance(year, int):
        raise HolidayFetchError("年份必须是整数（如 2026），收到 %r" % (year,))
    if not 1949 <= year <= 2100:
        raise HolidayFetchError(
            "年份 %d 超出合理范围（1949–2100），请检查第 1 周周一是否填对" % year)
    return year


def _check_templates(url_template) -> List[str]:
    """--holidays-url 的基本校验：占位符必须在、协议必须认。"""
    if url_template is None:
        return list(DEFAULT_URL_TEMPLATES)
    if not isinstance(url_template, str):
        raise HolidayFetchError("url_template 需要字符串，收到 %r" % (url_template,))
    t = url_template.strip()
    if "{year}" not in t:
        raise HolidayFetchError(
            "节假日数据地址必须包含 {year} 占位符，例如\n"
            "  --holidays-url \"https://内网镜像/holidays/{year}.json\"\n"
            "收到：%r" % url_template)
    low = t.lower()
    if not low.startswith(("http://", "https://", "file://")):
        raise HolidayFetchError(
            "节假日数据地址只支持 http://、https://、file:/// 开头；"
            "本地离线包请写成 file:///D:/path/{year}.json；收到：%r" % url_template)
    return [t]


def _fill(template: str, year: int) -> str:
    try:
        return template.format(year=year)
    except (IndexError, KeyError, ValueError) as exc:
        raise HolidayFetchError(
            "地址模板里除了 {year} 不要有别的占位符：%r（%s）" % (template, exc)) from exc


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _as_cache_hit(yh: YearHolidays) -> YearHolidays:
    """从内存缓存返回时：浅拷贝 + 如实标注 from_cache，防止调用方改列表污染缓存。"""
    return replace(yh, holidays=list(yh.holidays), makeup_days=list(yh.makeup_days),
                   years=list(yh.years), papers=list(yh.papers), from_cache=True)


def _download(url: str, year: int, timeout: float):
    """抓一个源并严格校验，返回 (原始 payload, 规范化 days, papers)。失败抛 _TryFailed。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json, */*"})
    # 刻意用 urlopen 默认 opener：ProxyHandler 会读 HTTP_PROXY/HTTPS_PROXY/NO_PROXY，
    # 校园网/公司网靠这个才能出去。换成自定义 opener 很容易把代理「优化」没了。
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _TryFailed("notfound", "HTTP 404（服务器上没有这一年的数据文件）") from exc
        raise _TryFailed("http", "HTTP %d %s" % (exc.code, exc.reason or "")) from exc
    except urllib.error.URLError as exc:
        r = exc.reason
        if isinstance(r, FileNotFoundError):   # 个别环境下 file:// 走这里
            raise _TryFailed("notfound", "本地/镜像文件不存在") from exc
        raise _TryFailed("network", "网络不可达（%s）" % r) from exc
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise _TryFailed("notfound", "本地/镜像文件不存在") from exc
        if isinstance(exc, TimeoutError):
            raise _TryFailed("network", "读取超时（%g 秒）" % timeout) from exc
        raise _TryFailed("network", "读取失败（%s）" % exc) from exc
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _TryFailed("data", "返回的不是合法 UTF-8 JSON（%s）" % exc) from exc
    try:
        norm, papers = _validate_payload(obj, year, url)
    except HolidayFetchError as exc:
        # 校验不过换下一个源：镜像坏/被截断时直连 GitHub 也许还是好的
        raise _TryFailed("data", str(exc)) from exc
    return obj, norm, papers


def _validate_payload(obj, year: int, where: str):
    """把 payload 规范化成 [(date, name, isOffDay)]（按日期升序）。任何不符 → HolidayFetchError。

    where 用于错误信息里定位是「联网的某个 URL」还是「本地缓存文件」的锅。
    """
    if not isinstance(obj, dict):
        raise HolidayFetchError("%s：JSON 顶层应为对象，实际是 %s"
                                % (where, type(obj).__name__))
    got = obj.get("year")
    if got != year:
        raise HolidayFetchError(
            "%s：数据里的 year=%r 与请求的 %d 年不符——宁可报错，不敢用错年份的数据"
            % (where, got, year))
    days = obj.get("days")
    if not isinstance(days, list) or not days:
        raise HolidayFetchError(
            "%s：days 缺失或为空。空安排一定是数据问题，静默返回空列表等于让"
            "整个学期「没有节假日」，故视为错误" % where)
    seen = set()
    norm = []
    for i, item in enumerate(days):
        if not isinstance(item, dict):
            raise HolidayFetchError("%s：days[%d] 不是对象" % (where, i))
        name, dstr, off = item.get("name"), item.get("date"), item.get("isOffDay")
        if not isinstance(name, str) or not name.strip():
            raise HolidayFetchError("%s：days[%d] 的 name 异常（%r）" % (where, i, name))
        if not isinstance(dstr, str) or not _DATE_RE.match(dstr):
            raise HolidayFetchError("%s：days[%d] 的 date 不是 YYYY-MM-DD（%r）"
                                    % (where, i, dstr))
        try:
            date = dt.date.fromisoformat(dstr)
        except ValueError:
            raise HolidayFetchError("%s：days[%d] 的日期非法：%s" % (where, i, dstr)) from None
        if not isinstance(off, bool):
            raise HolidayFetchError("%s：days[%d] 的 isOffDay 不是 true/false（%r）"
                                    % (where, i, off))
        if date in seen:
            raise HolidayFetchError("%s：days 里 %s 出现多次，数据可疑" % (where, dstr))
        seen.add(date)
        norm.append((date, name.strip(), off))
    norm.sort(key=lambda t: t[0])  # 不信任服务端排序，自己排（合并算法依赖升序）
    # papers 只是溯源信息，不参与任何计算：宽容处理成字符串列表即可
    raw_papers = obj.get("papers")
    papers = [str(p) for p in raw_papers if isinstance(p, str)] if isinstance(raw_papers, list) else []
    return norm, papers


def _build(norm):
    """规范化 days → (Holiday 区间列表, MakeupDay 列表)。"""
    holidays, makeup = [], []
    cur = None
    for date, name, off in norm:
        if off:
            if (cur is not None and cur.name == name
                    and date == cur.end + dt.timedelta(days=1)):
                cur.end = date          # 相邻同名 → 并入同一区间
            else:
                cur = Holiday(date, date, name)
                holidays.append(cur)
        else:
            cur = None                  # 调休打断合并（见模块 docstring）
            makeup.append(MakeupDay(date, None, "%s调休上班" % name))
    return holidays, makeup


# ------- 磁盘缓存：服务端 JSON 原样 + 信封字段（cached_at / cached_source）

def _cache_path(year: int) -> str:
    return os.path.join(cache_dir(), "%d.json" % year)


def _write_cache(year: int, url: str, fetched_at: str, obj: dict) -> None:
    """尽力而为地把 payload 落盘。

    写失败（只读介质、配额满……）**刻意不抛错**：数据已经安全到手，缓存只是
    下次免联网的优化，不影响本次结果的正确性——这与「静默算错」是两回事。
    """
    path = _cache_path(year)
    tmp = path + ".tmp"
    env = dict(obj)
    env["cached_at"] = fetched_at
    env["cached_source"] = url
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(env, fh, ensure_ascii=False)
        os.replace(tmp, path)           # 原子替换：崩溃也不留半截文件
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _read_cache(year: int) -> Optional[YearHolidays]:
    """读磁盘缓存。任何异常/校验不过 → None（未命中），绝不因缓存坏而失败。

    坏缓存不值得让用户换个网络环境就被炸：重新联网取即可。这也是「缓存不可信、
    源码才可信」的姿态——所以读回来照样走 _validate_payload 全套校验，包括
    year 字段必须与文件名/请求年份一致。
    """
    path = _cache_path(year)
    try:
        with io.open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict) or "cached_at" not in obj:
        return None                     # 不是本程序写的缓存
    try:
        norm, papers = _validate_payload(obj, year, "缓存文件 %s" % path)
    except HolidayFetchError:
        return None
    holidays, makeup = _build(norm)
    return YearHolidays(holidays=holidays, makeup_days=makeup, years=[year],
                        source=str(obj.get("cached_source") or path), papers=papers,
                        fetched_at=str(obj.get("cached_at") or ""), from_cache=True)


def _failure_message(year: int, failures) -> str:
    """把所有尝试汇成一条「用户读完知道该干什么」的错误。"""
    lines = ["获取 %d 年的节假日安排失败。已尝试：" % year]
    for url, kind, detail in failures:
        lines.append("  × %s —— %s" % (url, detail))
    kinds = {k for _, k, _ in failures}
    if "notfound" in kinds:
        if year > dt.date.today().year:
            lines.append("该年度国务院通知可能尚未发布：次年的节假日安排一般在头一年"
                         "11 月初才由国务院办公厅公布，届时 holiday-cn 才会有这一年的"
                         "数据文件。")
        else:
            lines.append("服务器上没有 %d 年的数据文件（镜像不完整，或该年度无安排/未收录）。"
                         % year)
    if kinds & {"network", "http"}:
        lines.append("也可能是：本机没有网络、校园网/公司网代理拦截了 GitHub、"
                     "或 DNS 解析失败（检查 HTTP_PROXY/HTTPS_PROXY 设置）。")
    if "data" in kinds:
        lines.append("还有源返回的内容没通过校验（年份不符 / days 异常 / 不是 JSON）。")
    lines.append("三条出路（任选其一，程序不会卡死在这一步）：")
    lines.append("  1) 手写节假日文件并用 --holidays-file 指定：界面「导出模板」会生成"
                 "带格式说明的空文件，照国务院办公厅通知填几行即可（未来年份基本只能靠这个）；")
    lines.append("  2) 有内网/离线镜像时：--holidays-url \"https://内网镜像/holidays/{year}.json\"，"
                 "或离线包 file:///D:/.../{year}.json；")
    lines.append("  3) --holidays off 暂时关闭节假日处理（代价：放假日的课不会被扣除，"
                 "调休日的补课安排也不会生成）。")
    return "\n".join(lines)
