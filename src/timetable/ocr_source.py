# -*- coding: utf-8 -*-
"""OCR 输入：把图片/无文字层的 PDF 变成「带坐标的文本」，交给同一套解析器。

## 它在整条流水线里的位置

    有文字层的 PDF ──→ PyMuPDF 直接取 span ──┐
    图片 / 扫描件 ──→ 本模块（OCR）→ span ───┼→ parser.parse_spans_pages → Record
    Excel/CSV ─────→ 单元格 ─────────────────┘        → 核对界面 → .ics

**关键设计：OCR 不自己做表格理解**。它只负责「这段文字在哪、多大、多可信」，
结构推断（哪列是星期几、哪段是教室教师）全部复用 parser —— 因为那套逻辑已经在
真实课表上被反复验证过，重写一份必然分叉（本项目已经踩过：两个入口的课程名提取
各自演化，其中一个整份解析出 0 条）。

## 实测数据（合成课表渲染图，有标准答案）

| 场景 | 字符召回 | 单页耗时 |
|---|---|---|
| 干净渲染 150dpi | 91.8% | 12.3s |
| JPEG 质量 40（扫描件感） | 90.6% | 4.3s |
| 旋转 1.5° + JPEG60（照片感） | 88.6% | 11.4s |

关键串（星期、课程名、教室、教师、周次、节次）在这些条件下都能认出。但**真实拍照
一定更差**（透视、反光、阴影），所以 OCR 结果一律标记为"需核对"，并且：
  * 放大 2 倍再识别**没有帮助**（实测字符数完全相同，反而把课程名切碎）——不要做；
  * 置信度低于阈值的行会汇总提示，让用户重点看那几处。

## 引擎选择

`engine="local"`（默认）用 RapidOCR + onnxruntime：离线、不上传数据（课表含姓名学号），
打包约 +50MB。`engine="cloud"` 预留云端多模态接口（用户自带 key），默认关闭。

## 为什么图片不需要额外图像库

PyMuPDF 能直接把 PNG/JPG 当文档打开，也能把 PDF 页渲染成图。所以两条输入都只要
PyMuPDF + OCR 引擎，不引入 Pillow 之外的东西（Pillow 本来就有）。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .parser import ParseResult, parse_spans_pages

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

# 渲染 dpi：太低小字糊、太高只是变慢。实测 150~200 之间差别不大，取 180。
RENDER_DPI = 180

# 低于这个置信度的文字块会单独汇总提示（不丢弃 —— 丢了就是静默少课）
LOW_CONF = 0.55


class OcrError(Exception):
    """OCR 不可用或失败。message 要能告诉用户「能做什么」。"""


def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def available() -> Tuple[bool, str]:
    """本地 OCR 是否可用；返回 (可用, 说明)。"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True, "本地 OCR（RapidOCR + onnxruntime）"
    except Exception as exc:                            # noqa: BLE001
        return False, "本地 OCR 不可用：%s" % exc


def pdf_has_text_layer(path: str, min_chars: int = 20) -> bool:
    """判断 PDF 是否有可用文字层。

    三种情况都要识别成「没有文字层」：
      * 扫描件：get_text() 空；
      * 图片贴进 PDF：d["blocks"] 全是 type=1；
      * 字体子集缺失导致乱码：文字很多但几乎全是 `(cid:123)` 或替换符。
    实测：把一页课表渲染成图再包成 PDF，get_text() 长度 0、blocks 只有 1 个 type=1。
    """
    import pymupdf
    doc = pymupdf.open(path)
    try:
        total = 0
        cid = 0
        for pno in range(doc.page_count):
            text = doc[pno].get_text()
            total += len(text.strip())
            cid += text.count("cid:") + text.count("\ufffd")
        if total < min_chars:
            return False
        # 乱码判定：cid/替换符占比过高
        if cid and cid * 8 > total:
            return False
        return True
    finally:
        doc.close()


def _engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise OcrError(
            "缺少 OCR 依赖。\n"
            "  本地识别需要：pip install rapidocr-onnxruntime onnxruntime\n"
            "  或者先用 Excel/CSV 输入（--xlsx-template 导出模板）。"
        ) from exc
    return RapidOCR()


_ENGINE_CACHE = []


def _get_engine():
    """引擎加载要几百毫秒，同一进程复用。"""
    if not _ENGINE_CACHE:
        _ENGINE_CACHE.append(_engine())
    return _ENGINE_CACHE[0]


_FULLWIDTH = {ord(c): ord(h) for c, h in zip(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－～＿",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz-~_")}
# 括号也统一成半角：OCR 常把 ( 认成 （，会让「同名课程」因括号不同而 split 成两条
# （实测 `高等数学A(上)` vs `高等数学A（上）` 就是两条记录）。统一之后比较/去重才可靠。
_FULLWIDTH.update({ord("（"): ord("("), ord("）"): ord(")"),
                   ord("，"): ord(","), ord("。"): ord(".")})


def _normalize(text: str) -> str:
    """OCR 文本归一：无条件 NFKC（全角->半角）+ 部首字符映射。

    为什么要做：OCR 对同一串 `A-302` 可能输出 `A－302`（全角减号），对 `(上)` 可能
    输出 `（上）`。这会让「同一门课」因为一个字符的宽度差异变成两条记录、让教室名
    对不上。归一化只影响**比较与去重**，中文本身不动。

    与 PDF 路径的区别：PDF 的文字层是"原文"，除非遇到部首字符否则不该改动
    （见 parser.normalize_text）；OCR 的输出本来就是"重排的"，可以放开归一。
    """
    from .parser import normalize_text
    return normalize_text(text, aggressive=True)


def ocr_image_to_spans(image_path: str, log=None) -> List[dict]:
    """对一张图跑 OCR，返回与 PyMuPDF span 同构的 dict 列表。

    返回的每个 span：x0/y0/x1/y1/base/size/text/conf。
    其中 base 用框的下沿（OCR 只给框，没有基线），行聚合会自动用它。
    """
    eng = _get_engine()
    res, _elapse = eng(image_path)
    spans: List[dict] = []
    for item in (res or []):
        box, text, score = item[0], item[1], item[2]
        text = _normalize((text or "").strip())
        if not text:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        try:
            conf = float(score)
        except (TypeError, ValueError):
            conf = 0.0
        spans.append(dict(
            x0=x0, y0=y0, x1=x1, y1=y1,
            base=y1,                       # 框的底边当基线：同一行的框底边接近
            size=max(1.0, (y1 - y0) * 0.78),   # 粗估字号：框高 ≈ 1.28 倍字号
            text=text, conf=conf))
    if log:
        log("OCR：%s → %d 个文字块" % (os.path.basename(image_path), len(spans)))
    return spans


def _render_pdf_pages(path: str, dpi: int = RENDER_DPI, log=None) -> List[str]:
    """把 PDF 每页渲染成临时 PNG，返回文件路径列表。"""
    import pymupdf
    import tempfile

    doc = pymupdf.open(path)
    out = []
    tmpdir = tempfile.mkdtemp(prefix="tt-ocr-")
    try:
        for pno in range(doc.page_count):
            pix = doc[pno].get_pixmap(dpi=dpi)
            fp = os.path.join(tmpdir, "page-%d.png" % (pno + 1))
            pix.save(fp)
            out.append(fp)
        if log:
            log("已把 %d 页渲染成图（%d dpi）准备识别" % (len(out), dpi))
    finally:
        doc.close()
    return out


def read_image(path: str, layout=None, period_times=None, log=None,
               clock_tolerance_min: int = 10) -> ParseResult:
    """读一张课表图片（.png/.jpg/...）。"""
    if not is_image_file(path):
        raise OcrError("不是图片文件：%s" % path)
    spans = ocr_image_to_spans(path, log=log)
    if not spans:
        raise OcrError("这张图里没识别出任何文字。请确认图片清晰、朝向正确（文字是横排）。")
    result = parse_spans_pages([spans], layout=layout, log=log,
                               period_times=period_times,
                               clock_tolerance_min=clock_tolerance_min)
    _annotate_ocr_result(result, spans)
    return result


def read_pdf_by_ocr(path: str, layout=None, period_times=None, log=None,
                    clock_tolerance_min: int = 10) -> ParseResult:
    """读一份没有文字层的 PDF：逐页渲染再 OCR。"""
    pages = []
    for fp in _render_pdf_pages(path, log=log):
        pages.append(ocr_image_to_spans(fp, log=log))
        try:
            os.remove(fp)
        except OSError:
            pass
    if not any(pages):
        raise OcrError("这份 PDF 渲染后没识别出任何文字，可能页面上确实没有内容。")
    result = parse_spans_pages(pages, layout=layout, log=log,
                              period_times=period_times,
                              clock_tolerance_min=clock_tolerance_min)
    _annotate_ocr_result(result, [s for p in pages for s in p])
    return result


def _annotate_ocr_result(result: ParseResult, spans: List[dict]) -> None:
    """给 OCR 结果打上「需核对」标记，并汇总低置信度的文字。"""
    confs = [s.get("conf", 1.0) for s in spans if s.get("text")]
    avg = sum(confs) / len(confs) if confs else 0.0
    low = [s["text"] for s in spans if s.get("conf", 1.0) < LOW_CONF]
    result.stats["ocr"] = dict(avg_conf=avg, low_conf=len(low), blocks=len(spans))
    result.notes.append(
        "来源：OCR 识别（平均置信度 %.0f%%）—— 请务必在预览表里核对一遍。"
        % (avg * 100))
    if low:
        result.warnings.append(
            "有 %d 处文字识别置信度偏低，可能就是错的地方：%s"
            % (len(low), "、".join(low[:8])))


def read_any_ocr(path: str, layout=None, period_times=None, log=None) -> ParseResult:
    """按文件类型走：图片 / 无文字层 PDF。"""
    if is_image_file(path):
        return read_image(path, layout, period_times, log)
    if pdf_has_text_layer(path):
        raise OcrError("这份 PDF 有文字层，应当直接用坐标解析（比 OCR 准得多）。")
    return read_pdf_by_ocr(path, layout, period_times, log)