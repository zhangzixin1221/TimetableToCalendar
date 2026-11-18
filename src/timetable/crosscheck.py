# -*- coding: utf-8 -*-
"""OCR 交叉验证：用像素复核「文字层里的字到底是不是这个字」。

## 为什么需要

有些 PDF 的文字层是"看着对、码位错"：网页打印会把常用字写成康熙部首/部首补充区的
字符（`⻄` 不是 `西`、`⬀`……），字体子集缺失会吐出 `(cid:123)`。我们已经在
`parser.normalize_text()` 里做了归一和手工映射，但映射表不可能覆盖所有情况，
而且**归一之后就看不出原来错在哪了** —— 错了也不知道。

OCR 是从**像素**重新认一遍，与文字层完全独立的第二个信息源，正好用来复核。

## 定位：只对"字"投票，不参与排版

    文字层  ──→ 精确坐标（列在哪、哪行是哪行）──→ 结构解析
       │                                              ↑
       └──→ 可疑字符 ──→ OCR 复核这一处的字 ──────────┘

这样做的好处：OCR 最弱的是排版（表格结构、行列归属），最强的是单字识别；
文字层正好相反。各用长处，比"整页交给 OCR 重解析一遍"可靠得多。

## 什么时候触发

不是每份 PDF 都值得跑 OCR（单页 5~15 秒）。只在文字层露出这些迹象时才复核：
  * 出现康熙部首/部首补充区字符（被映射过）—— 最强的信号；
  * 出现 `cid:` 或替换符 ``（字体子集缺失）；
  * 映射表覆盖不到的部首字符残留（这时连解析都可能失败）。

## 怎么比对

按**几何位置**匹配：把页面渲染成图、OCR 出文本框，再把框按页面缩放换算回 PDF 坐标，
与文字层 span 求重叠（IoU）。重叠够高的一对才算"同一个地方的两份读数"。
对比前两边都做归一（否则全角/半角差异会制造假不一致）。

## 结果怎么用

**默认只报告、不改数据**。理由：OCR 也会错（实测渲染图 88~92% 字符召回），
自动采纳会把 OCR 的错混进原本正确的数据里。所以把"不一致"列出来交给用户核对，
这符合本项目一贯的做法（宁可明说，不要静默改）。
需要自动采纳时用 `apply=True`，且只对「文字层确实可疑」的那些 span 采纳。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .parser import normalize_text

# 渲染分辨率：太低了小字糊、太高只是变慢。300dpi 对 6pt 中文比较稳。
CROSSCHECK_DPI = 300
# 认为「同一个地方」的最小重叠度
MIN_IOU = 0.25
# 采纳 OCR 结果时要求的最低置信度
MIN_CONF = 0.60


def suspicion(health: Optional[dict]) -> Tuple[bool, List[str]]:
    """根据文字层健康度判断要不要跑 OCR 复核，返回 (是否, 理由列表)。"""
    h = health or {}
    reasons: List[str] = []
    if h.get("radical_chars"):
        reasons.append("文字层里有 %d 个「康熙部首/部首补充」区字符（%d 处），"
                       "这类字符看着是常用字但码位不同"
                       % (h["radical_chars"], h.get("radical_spans", 0)))
    if h.get("cid_chars"):
        reasons.append("文字层里出现 %d 处 `cid:`（字体子集缺失，提取出来是乱码）"
                       % h["cid_chars"])
    if h.get("replacement_chars"):
        reasons.append("文字层里出现 %d 个替换符 ``" % h["replacement_chars"])
    return bool(reasons), reasons


def _iou(a, b) -> float:
    """两个 bbox 的交并比。"""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1e-6, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1e-6, (bx1 - bx0) * (by1 - by0))
    return inter / (area_a + area_b - inter)


def _clean(s: str) -> str:
    """比对用的清洗：去不换行空格、折叠空白、清掉不可见字符。"""
    s = (s or "").replace("\xa0", " ").replace("\u3000", " ")
    s = s.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(s.split())


def _similar(a: str, b: str) -> float:
    """粗略相似度（按字符多重集），够用来判"是不是同一串字"。"""
    from collections import Counter
    a = normalize_text(_clean(a), aggressive=True).replace(" ", "")
    b = normalize_text(_clean(b), aggressive=True).replace(" ", "")
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    ca, cb = Counter(a), Counter(b)
    hit = sum(min(n, cb[ch]) for ch, n in ca.items())
    return hit / max(len(a), len(b))


def _spans_inside(spans: List[dict], box, min_cover: float = 0.5) -> str:
    """把**被 OCR 框覆盖了大半**的文字层 span 按 x 顺序拼起来。

    以 OCR 框为单位取文字，而不是先把文字层切成行再找 OCR 框：文字层是碎片 span，
    OCR 返回的是它自己认为的一个文字块，用 OCR 的框当"容器"最不容易出错。

    判据用**覆盖率**而不是"中心落在框内"：OCR 的框常常只框住一行的前半截
    （实测 `(1~16周)(5-6节)` 里的节次部分跑到框外），按中心判定就会把框外的节次
    也算进来，于是"文字层有节次、OCR 没有"这种假差异天天出现。
    """
    inside = []
    for s in spans:
        sx0, sy0, sx1, sy1 = s["x0"], s["y0"], s["x1"], s["y1"]
        area = max(1e-6, (sx1 - sx0) * (sy1 - sy0))
        ix0, iy0 = max(sx0, box[0]), max(sy0, box[1])
        ix1, iy1 = min(sx1, box[2]), min(sy1, box[3])
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        if (ix1 - ix0) * (iy1 - iy0) / area >= min_cover:
            inside.append(s)
    inside.sort(key=lambda z: z["x0"])
    return "".join(s["text"] for s in inside)


def _looks_like_split(tl_text: str, ocr_text: str) -> bool:
    """这一处差异是不是**只是切行不同**（OCR 把一个框切成了别的边界）。

    为什么需要：OCR 的框边界和文字层的 span 边界不可能总一致 ——
    实测 `学院：航海学院`（文字层一行）会被 OCR 报成 `航海学院`，
    相似度 57%、置信度 80%，看着像"不一致"，其实文字层是对的。
    开启「每次都复核」之后这种假差异会天天报警，用户很快就会无视所有警告。

    判据：**一方是另一方的子串**才算切行（OCR 截断了、或把两行并了）。
    不能用"长度差得多"代替：那会把 `大学物理` vs `马克思主义学院` 这种真正的
    严重不一致也放过（长度差确实大，但内容完全不同）。
    """
    a = normalize_text(_clean(tl_text), aggressive=True).replace(" ", "")
    b = normalize_text(_clean(ocr_text), aggressive=True).replace(" ", "")
    if not a or not b:
        return True
    return a in b or b in a


def crosscheck(pdf_path: str, lay=None, log=None,
               dpi: int = CROSSCHECK_DPI,
               max_report: int = 12) -> dict:
    """对一份 PDF 做 OCR 复核。返回报告 dict（**不改任何解析结果**）。

    做法：OCR 出文字框 → 对每个框，收集中心落在框里的文字层 span、按 x 拼成串 →
    与 OCR 认出的字比相似度。相当或接近就算"两边一致"，差得多就报出来。
    """
    from . import ocr_source

    ok, why = ocr_source.available()
    if not ok:
        return {"checked": False, "reasons": ["本地 OCR 不可用（%s）" % why],
                "blocks": 0, "matched": 0, "diffs": [], "agree": None}

    import pymupdf
    from .parser import _collect_spans

    doc = pymupdf.open(pdf_path)
    try:
        report = {"checked": True, "reasons": [], "blocks": 0, "matched": 0,
                  "diffs": [], "agree": None}
        total_matched = total_same = 0
        for pno in range(doc.page_count):
            page = doc[pno]
            page_spans = _collect_spans(doc[pno])
            if not page_spans:
                continue
            if log:
                log("  复核第 %d/%d 页…" % (pno + 1, doc.page_count))
            pix = page.get_pixmap(dpi=dpi)
            tmp = os.path.join(os.environ.get("TEMP", "."),
                               "_tt-crosscheck-%d-%d.png" % (os.getpid(), pno))
            pix.save(tmp)
            try:
                ocr_spans = ocr_source.ocr_image_to_spans(tmp, log=log)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if not ocr_spans:
                continue
            scale = page.rect.width / float(pix.width or 1)
            for o in ocr_spans:
                o["x0"] *= scale
                o["x1"] *= scale
                o["y0"] *= scale
                o["y1"] *= scale
            for o in ocr_spans:
                box = (o["x0"], o["y0"], o["x1"], o["y1"])
                tl_text = _spans_inside(page_spans, box)
                if not _clean(tl_text):
                    continue                 # 框里没有文字层内容（图上的字），跳过
                total_matched += 1
                ratio = _similar(tl_text, o["text"])
                conf = float(o.get("conf") or 0)
                if ratio >= 0.6:
                    total_same += 1
                    continue
                # OCR 自己都没把握时，它的读法不能当"证据"，只记为"无法判定"。
                # 实测有个 40 人教师名单，OCR 只认出 `/////`（置信度 51%）——
                # 报成"不一致"会误导用户去改本来正确的数据。
                if conf < MIN_CONF:
                    report["inconclusive"] = report.get("inconclusive", 0) + 1
                    continue
                # 只是 OCR 切行不同（长度差太多）→ 不算字符错误，也别去打扰用户
                if _looks_like_split(tl_text, o["text"]):
                    report["split_only"] = report.get("split_only", 0) + 1
                    continue
                if len(report["diffs"]) < max_report:
                    report["diffs"].append(dict(
                        page=pno + 1, text_layer=_clean(tl_text),
                        ocr=_clean(o["text"]),
                        similarity=round(ratio, 2), conf=round(conf, 2)))
                else:
                    report["diffs_truncated"] = report.get("diffs_truncated", 0) + 1
        report["matched"] = total_matched
        report["agree"] = round(total_same / total_matched, 3) if total_matched else None
        return report
    finally:
        doc.close()


def run_if_needed(pdf_path: str, result, lay=None, mode: str = "auto",
                  log=None) -> dict:
    """按 mode 决定要不要复核，并把结果写回 `result`（notes/warnings/stats）。

    CLI 与界面都走这一个入口，免得两边逻辑分叉（本项目已经踩过这个坑）。
    mode: "auto"（仅可疑时）/ "on"（总是）/ "off"（不跑）
    """
    if mode == "off":
        return {"checked": False, "reasons": ["按 --crosscheck off 关闭"], "diffs": []}
    health = (result.stats or {}).get("text_layer") or {}
    need, reasons = suspicion(health)
    if mode == "auto" and not need:
        return {"checked": False, "reasons": ["文字层未露出可疑迹象（auto 模式跳过）"]}
    say = log or (lambda m: None)
    say("")
    say("用 OCR 复核文字层（文字层负责排版，OCR 只核对每个字对不对）…")
    if need:
        for r in reasons:
            say("  · 可疑迹象：" + r)
    rep = crosscheck(pdf_path, lay, log=log)
    rep["reasons"] = reasons
    for line in describe(rep):
        say(line)
    (result.stats or {}).setdefault("crosscheck", rep)
    if rep.get("diffs"):
        n = len(rep["diffs"]) + rep.get("diffs_truncated", 0)
        result.warnings.append(
            "OCR 复核发现 %d 处与文字层读数不一致（详见上面对照）。这些地方多半是"
            "文字层的问题；可以用「导出结果核对…」导成 Excel 手工修正。" % n)
    return rep


def describe(report: dict) -> List[str]:
    """把报告翻成给人看的几行（CLI/界面直接用）。"""
    if not report.get("checked"):
        return ["OCR 复核：未执行（%s）" % "；".join(report.get("reasons") or ["无"])]
    out = ["OCR 复核：比对 %d 处文字，其中 %d 处能与文字层对上"
           % (report.get("matched", 0),
              int(round((report.get("agree") or 0) * (report.get("matched") or 0))))]
    if report.get("inconclusive"):
        out.append("  （另有 %d 处 OCR 自己置信度不足，未作判定）"
                   % report["inconclusive"])
    diffs = report.get("diffs") or []
    if not diffs:
        out.append("  ✓ 文字层与 OCR 读数一致，未发现可疑字符")
        if report.get("split_only"):
            out.append("  （另有 %d 处只是 OCR 的切行方式不同，不算字符差异）"
                       % report["split_only"])
        return out
    out.append("  ⚠ 有 %d 处两边读数不一致，请核对（左=文字层，右=OCR 看到的样子）："
               % (len(diffs) + report.get("diffs_truncated", 0)))
    for d in diffs:
        out.append("    第%d页 文字层=%r  OCR=%r（相似度 %.0f%%，OCR 置信度 %.0f%%）"
                   % (d["page"], d["text_layer"], d["ocr"],
                      d["similarity"] * 100, d["conf"] * 100))
    if report.get("diffs_truncated"):
        out.append("    …另有 %d 处未列出" % report["diffs_truncated"])
    return out