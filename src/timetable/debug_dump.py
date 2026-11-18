# -*- coding: utf-8 -*-
"""结构转储：把「程序眼里的这份 PDF」打印出来。

为什么需要它：给一所新学校写版式，最难的一步不是写正则，而是**看不见程序看见了什么**。
教务系统导出的 PDF 看着是表格，读出来是一堆带坐标的文字块；哪一行被判成课程标题、
哪一段被当成教室、教师为什么被吃掉，光看 PDF 是猜不出来的。这个工具把中间的几何
与分类结果原样摊开，改版式时照着对，一次就能定位问题。

用法：
    python main.py --cli 课表.pdf --dump-layout                 # 默认（西工大）版式
    python main.py --cli 课表.pdf --dump-layout --layout inline-grid
    python main.py --cli 课表.pdf --dump-layout --auto-layout   # 每个版式都摊开看
"""
from __future__ import annotations

from typing import List, Optional

from . import layout as lay_mod
from .parser import (TABLE_ROLES, _classify_header, _collect_spans, _find_header,
                     _group_into_lines, _parse_detail, _table_record)


def _line(desc: str, val="") -> str:
    return desc if val == "" else "%s：%s" % (desc, val)


def dump_pdf(pdf_path: str, lay: lay_mod.Layout, pages: Optional[List[int]] = None,
             max_lines: int = 60) -> str:
    import pymupdf

    rx = lay.compiled()
    doc = pymupdf.open(pdf_path)
    out: List[str] = []
    add = out.append

    add("=" * 78)
    add("文件：%s    页数：%d" % (pdf_path, doc.page_count))
    add("版式：%s (%s)    entry_mode=%s" % (lay.name, lay.id, lay.entry_mode))
    add("=" * 78)

    for pno in range(doc.page_count):
        if pages and (pno + 1) not in pages:
            continue
        page = doc[pno]
        spans = _collect_spans(page)
        add("")
        add("── 第 %d 页  尺寸 %.0fx%.0f  文字块 %d 个"
            % (pno + 1, page.rect.width, page.rect.height, len(spans)))

        plain = page.get_text()
        meta_fields = {}
        for fname, label in (lay.meta_labels or {}).items():
            import re as _re
            m = _re.search(_re.escape(label) + r"\s*([^\s]+)", plain)
            if m:
                meta_fields[fname] = m.group(1)
        if meta_fields:
            add("   元信息：" + "  ".join("%s=%s" % kv for kv in meta_fields.items()))
        if rx["term_re"]:
            m = rx["term_re"].search(plain)
            if m:
                add("   学期：%s-%s%s" % (m.group("a"), m.group("b"),
                                          " " + (m.groupdict().get("season") or "")))

        if lay.entry_mode == "table":
            out.extend(_dump_table(spans, lay, rx, max_lines))
            continue

        day_indices, centres, header_bottom = _find_header(spans, lay)
        if centres is None:
            add("   ✗ 没找到星期表头（%s）—— 这一页在 %s 模式下读不出来。"
                % ("、".join(x[0] for x in lay_mod.DAY_ALIASES[:3]), lay.entry_mode))
            add("   页面上出现过的短文本（前 40 个，看表头到底叫什么）：")
            shorts = sorted({s["text"].strip() for s in spans if len(s["text"].strip()) <= 6})
            add("     " + "、".join(shorts[:40]))
            continue
        add("   ✓ 表头 %d 列：%s" % (len(day_indices),
                                    "、".join(lay_mod.DAY_ALIASES[i][0] for i in day_indices)))
        if len(day_indices) < 7:
            add("     ⚠ 表头不全（缺 %s）—— 课表可能被裁掉，只打印出一部分"
                % "、".join(lay_mod.DAY_ALIASES[i][0] for i in range(7)
                            if i not in day_indices))
        add("     表头列中心 x = %s" % " ".join("%.0f" % c for c in centres))
        add("     表头底 y = %.1f" % header_bottom)
        gaps = [centres[i + 1] - centres[i] for i in range(len(centres) - 1)]
        add("     列距 = %s（中位数 %.1f）" % (" ".join("%.0f" % g for g in gaps),
                                              sorted(gaps)[len(gaps) // 2]))

        bounds = [(centres[i] + centres[i + 1]) / 2.0 for i in range(len(centres) - 1)]
        step = sorted(gaps)[len(gaps) // 2]
        col_left = centres[0] - step / 2.0
        body_bottom = max(s["y1"] for s in spans) + 1
        for s in spans:
            t = s["text"].strip()
            if rx["body_end_re"] and rx["body_end_re"].match(t):
                body_bottom = min(body_bottom, s["y0"] - 1.0)
                add("   正文区在 y=%.1f 处截断（遇到「%s」）" % (body_bottom, t[:14]))
                break

        def col_of(x):
            for i, b in enumerate(bounds):
                if x < b:
                    return i
            return len(centres) - 1

        body, dropped = [], []
        for s in spans:
            t = s["text"].strip()
            if s["x0"] < col_left:
                dropped.append(("左栏外(节次栏)", s))
            elif s["y0"] <= header_bottom + 1:
                dropped.append(("表头以上", s))
            elif s["y0"] >= body_bottom:
                dropped.append(("备注/页脚区", s))
            elif rx["skip_text_re"] and rx["skip_text_re"].search(t):
                dropped.append(("命中 skip_text_re", s))
            else:
                body.append(dict(s, col=col_of(s["x0"])))
        add("   参与解析的文字块 %d 个，丢弃 %d 个" % (len(body), len(dropped)))
        if dropped:
            seen = {}
            for why, s in dropped:
                seen.setdefault(why, []).append(s["text"].strip()[:12])
            add("     " + "；".join("%s：%s" % (k, "、".join(v[:6]))
                                    for k, v in seen.items()))

        shown = 0
        cap = max_lines
        for ci in range(len(centres)):
            col_items = [s for s in body if s["col"] == ci]
            if not col_items:
                continue
            lines = _group_into_lines(col_items, lay.col_y_tolerance)
            add("")
            add("   【%s】%d 行" % (lay_mod.DAY_ALIASES[day_indices[ci]][0], len(lines)))
            for ln in lines:
                if shown >= cap:
                    add("     …（整页只显示前 %d 行，够用来对版式了；要全看就加大 --dump-lines）"
                        % cap)
                    break
                shown += 1
                kind, extra = _classify_line(ln, lay, rx, body, ci)
                add("     y=%6.1f size=%4.1f  %-10s %s" % (ln["y"], ln["size"], kind,
                                                            _clip(ln["text"], 46)))
                if extra:
                    add("              %s" % extra)
    return "\n".join(out)


def _clip(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def _classify_line(ln, lay, rx, body, ci):
    """复刻 parser 的分类判断，并说明「为什么是这个结论」。"""
    text = ln["text"]
    d = _parse_detail(text, lay, rx) if lay.entry_mode != "table" else None
    if d:
        room, teacher = lay_mod.split_room_teacher(d["rest"], lay.room_teacher)
        if d.get("pfrom") is not None:
            where = "节次 %d-%d" % (d["pfrom"], d["pto"])
        elif d.get("clock"):
            where = "时刻 %s-%s（待用作息表反查节次）" % d["clock"]
        else:
            where = "节次/时刻都没认出来"
        extra = ("周次 %s  %s  →  教室=%r 教师=%r  剩余=%r"
                 % (_fmt_weeks(d["weeks"]), where, room, teacher, d["rest"]))
        return "详情行", extra
    if rx["title_re"] and rx["title_re"].match(text) and not rx["codelist_re"].match(text):
        return "标题行", ""
    if rx["codelist_re"] and rx["codelist_re"].match(text):
        return "说明行", "命中 codelist_re（纯字母数字，像课程代码表）"
    sizes = sorted({z["size"] for z in body if z["col"] == ci})
    if sizes and ln["size"] < max(sizes) - 0.15:
        return "说明行", "字号 %.2f 小于本页最大字号 %.2f" % (ln["size"], max(sizes))
    if "周" not in text and rx["note_words_re"] and rx["note_words_re"].search(text):
        return "说明行", "命中 note_words_re"
    return "续行?", "既不是标题也不是详情：会拼到上一条目（拼错了就去调 title_re）"


def _fmt_weeks(ws) -> str:
    from .parser import compress_weeks
    return compress_weeks(ws)


def _dump_table(spans, lay, rx, max_lines):
    out = []
    lines = _group_into_lines(spans, lay.col_y_tolerance)
    out.append("   平面表模式：共 %d 行" % len(lines))
    hdr_i, roles = None, {}
    for i, ln in enumerate(lines):
        found = {}
        for s in ln["spans"]:
            role = _classify_header(s["text"])
            if role:
                found[role] = (s["x0"] + s["x1"]) / 2.0
        if len(found) >= 4 and "course" in found:
            hdr_i, roles = i, found
            out.append("   ✓ 第 %d 行是表头，识别到列：%s"
                       % (i + 1, "、".join("%s@%.0f" % (k, v)
                                           for k, v in sorted(found.items(),
                                                              key=lambda kv: kv[1]))))
            break
    if hdr_i is None:
        out.append("   ✗ 没找到表头行（要有一行里至少 4 个能认出的列名，且包含课程列）")
        out.append("     认得的列名：" + "、".join(
            n for names in TABLE_ROLES.values() for n in names))
        for ln in lines[:12]:
            out.append("     y=%6.1f %s" % (ln["y"], _clip(ln["text"], 70)))
        return out
    centres = sorted(roles.values())
    bounds = [(centres[i] + centres[i + 1]) / 2.0 for i in range(len(centres) - 1)]

    def role_of_x(x):
        for i, b in enumerate(bounds):
            if x < b:
                return i
        return len(centres) - 1

    inv = {role_of_x(c): r for r, c in roles.items()}
    n = 0
    for ln in lines[hdr_i + 1:]:
        cells = {}
        for s in ln["spans"]:
            k = role_of_x(s["x0"])
            cells[k] = (cells.get(k, "") + " " + s["text"]).strip()
        texts = {inv[k]: v for k, v in cells.items() if k in inv}
        n += 1
        if n > max_lines:
            out.append("     …（后面 %d 行没显示）" % (len(lines) - hdr_i - 1 - max_lines))
            break
        out.append("     y=%6.1f %s" % (ln["y"], "  ".join(
            "%s=%s" % (k, _clip(v, 22)) for k, v in texts.items())))
        if texts.get("course"):
            recs = _table_record(texts, texts["course"], lay, rx, [])
            if not recs:
                out.append("        → 这一行没能变成课程（缺星期/节次/周次，见上面的警告）")
            else:
                r = recs[0]
                out.append("        → %s %d-%d节 第%s周 教师=%r 教室=%r 时刻=%s"
                           % (r.day_name, r.pfrom, r.pto, _fmt_weeks(r.all_weeks()),
                              r.segments[0].teacher if r.segments else "", r.room,
                              r.clock or "（无，按作息表推）"))
    return out
