# -*- coding: utf-8 -*-
"""合成「西工大教务系统《学生课表》PDF」的 fixture 生成器。

仓库里不能放真实课表（个人信息），而 `src/timetable/parser.py` 是**纯几何**的
（按 span 的 x0/y0/size 分列分行），没有真实排版的样例 PDF 就完全无法端到端回归。
本脚本用 PyMuPDF `insert_text` **一段一段**地画文字，造出与真实导出版面结构等价的
假课表，让 parser 原样解析成功。所有数据都是虚构的（学号 2024123456、张三/李四
这类假名、假课程代码、假教室名）。

用法
----
    python tools/make_fixture.py                     # → fixtures/nwpu-sample.pdf
    python tools/make_fixture.py -o 某路径.pdf        # 指定输出
    python tools/make_fixture.py --dump              # 打印自己造出来的 span 清单
    python tools/make_fixture.py --check             # 生成 + 解析 + 断言（回归入口）
    python tools/make_fixture.py --longname-trap     # 额外造「长课程名换行」陷阱样例

`--check` 全部通过退出码 0；任何一条断言失败都会打印期望/实际并抛 AssertionError。

---------------------------------------------------------------- 覆盖的场景清单
版面骨架
  1. A4 横向 842x595；左侧「节次栏」+ 7 个星期列，列宽 110pt，表头 星期一…星期日
     各占一列中心（parser 用中心点算 bounds，`col_left = 中心0 − 步长/2 = 70`）。
  2. 节次栏文字（`1-2节`…`13节`）整体落在 col_left 左边 → 照实画，验证「靠 x0 丢弃」
     这条路，而不是靠正则跳过。
  3. 表头之上是元信息区（学期/年级/学院/专业/班级/学号/姓名）；`_parse_meta` 用
     `page.get_text()`，所以标签与值之间留了真实空格。
  4. 页脚：`备注：…` 与 `总学分：…`（触发 body_bottom 截断）、`打印日期：…`
     与独立 span 的页码 `1/2`、`2/2`（验证两条显式过滤分支）。
  5. 多页：周六/周日的内容只在第 2 页（第 2 页同样画满 7 个表头），验证跨页合并
     且 meta 只在第 1 页抓；第 2 页没有备注行，所以页码/打印日期只能靠过滤分支挡掉。

课程形态（每条都是 parser/ics 的回归点）
  A.  常规全学期课 (1~16周)，多天多节次分布；另有 (1~8) 前半学期结课的课。
  B.  单双周互补：周二 1-2 节同一格里 `(2~16(双)周) 大学物理B(上)03` 与
      `(1~15(单)周) 概率论与数理统计04` —— 同星期同节次、周次不相交，
      `verify_parse` 必须判零冲突。周日另有一门 `(2~16(双)周)`。
  C.  导出器拆分碎片：周一 11-12 格内相邻两行 `(1~8周)(11-12节)` + `(1~8周)(13-13节)`
      （同课同日同教室）→ parser 出 2 条 Record，`merge_contiguous(mode="artifact")`
      必须合成 11-13 节一条。
  D.  换老师分段：周一 7-8 格内 `(1~8周)…李华` + `(9~16周)…王强` → 同一条 Record
      两个 `Segment`，验证 `teacher_for(week)`（含 8/9 边界周）与 `teacher_desc()`。
  E.  教室名含字母/数字、教师名 2~4 汉字：`实验大楼C座5楼 向礼琴`（3 字名）、
      `制图中心A区 袁斌`、`友谊西外A-302 王小明`、`翱翔体育馆 张伟`（2 字名）、
      `崔明轩`/`唐伯涛`/`陈明远`（3 字名）。
  F.  **多 span 拼一行**（最关键）：教室与教师之间**单独 `insert_text(" ")`**，
      绝不图省事把整行一次写完；教室/教师的边界完全靠这些空白 span 撑开。
  G.  正文行换行续行**三种**（三种断行点各一处，覆盖 rest 拼接的全部路径）：
      ① 多数课用 `break="after_period"`：第一行只有 `(1~16周)(1-2节)`，
         第二行 `教室 教师`（续行内自带分隔空格）；
      ② 周三 3-4 格用 `break="mid_room"`：`(1~16周)(3-4节) 实验大楼C座` +
         `5楼 向礼琴` → 教师名从被截断的续行里补全；
      ③ 周一 5-6 格用 `break="after_room"`：`(1~8周)(5-6节) 教东101` + 单独一行
         `白宇` —— 断行正好落在教室与教师的**分隔空格**上（坑 2 的回归哨兵）。
  H.  长课程名换行（真实写法）：周三 1-2 格两行名字
      `毛泽东思想和中国特色社会主义` / `理论体系概要08`，靠 parser 的残段拼接并回
      一条完整课程（坑 1 已修）。这种条目必须是**所在列的第一行**，原因见坑 1。
  I.  说明行（两种判定机制各一处）：
      ① 小字号 5.8pt：`分级教学B班`、`2024级本科公共课`（< 标题 6.5 − 0.15）；
      ② 与标题同字号、靠关键词：`大物实验`（NOTE_WORD_RE 的 `^大物实验$`）。
  J.  标题两种写法：`高等数学A(上)06`（紧贴两位数字）与 `大学英语 12`（空格 + 数字）。
  K.  备注区 2 门无固定时间课程：`军事理论 HKJS101.01 (1~8)`、
      `社会实践 SJXS220.02 (1~16)`。
  L.  「合法标题行 + 非数字结尾的补充行」（周三 5-6 格 `数据结构与算法实验08` +
      `（分四组轮转上课）`）：第二行进 notes，**不许**被残段拼接当成课程名的一部分
      —— 拼接守卫边界的反向用例。


------------------------------------------------------ parser 契约里真正难踩的坑
坑 1 —— 长课程名换行 vs 新条目（**曾经修不好，2026-11-17 由「残段拼接」解决**）
    parser 判定条目的规则是「行匹配 TITLE_RE ⇒ 开一个新条目」。真实课表里 110pt 宽的
    列塞不下 19 个汉字，长名字必然这样换行：

        行1  毛泽东思想和中国特色社会主义      ← 不以数字结尾，不匹配 TITLE_RE
        行2  理论体系概要08                    ← 匹配 TITLE_RE

    在加残段拼接之前，实测两种坏结果（当时 `--longname-trap` 断言的就是它们）：
      * 这两行是本列开头：行1 被 `not entries` 强行当标题，行2 另开条目并把正文行全部
        抢走 → 行1 成为「只有标题没有正文」的伪条目 → warning「没解析出周次和节次，
        已跳过」，课程名被切成两半；
      * 这两行前面已有条目：行1 被当成上一条课程的续行拼进它的 `rest` → 教师名被吃进
        教室名。
    现在 `_parse_block_column` 在「要开新条目」之前先看上一条目：若它到现在一行
    `(周次)(节次)` 正文都没有，就不可能是一门完整的课，只能是名字被换行截断的前半段
    → 把当前行拼回它的 title（并要求两行字号差 ≤0.15）。fixture 侧对应三件事：
      * 主 fixture 用**真实写法**（场景 H），断言完整课程名 + 零告警 + 代码/教室/教师
        都对；
      * `fixtures/nwpu-longname-trap.pdf` 文件名保留（别的脚本可能引用），但断言
        **反转**成修复后的正确行为，当回归哨兵：它只有一门课，拼接再坏时一眼可见；
      * 场景 L 是这条拼接的**反向守卫**：第二行不以数字结尾时必须仍走 notes，不许被
        当成课程名的一部分。
    仍然成立的两个约束（改 fixture 时别踩）：
      ① 残段拼接只在「上一条目还没有正文行」时触发 ⇒ 被截断的长名字必须落在所在列的
         **第一行**，否则前半段会被拼进上一条课程的 rest；
      ② 拼接是**直接相加不补空格** ⇒ 画进名字里的空格会原样留在课程名里。所以两行
         名字都不许带内部空格，断言 `course` 里不含空格（含了就是缺陷，不要改期望值
         迁就它）。

坑 2 —— 续行拼接曾经不补空格（现在补了，周一 5-6 格是它的回归哨兵）
    旧版 `details[-1]["rest"] += text.strip()`：断行正好落在教室与教师那个分隔空格上
    （第一行 `教西D-101`、第二行 `陈明远`）时拼出 `教西D-101陈明远` ⇒ 教师被教室吃掉。
    现在改成补空格拼接，`after_room` 这种断法解析正确（room=`教东101`、
    teacher=`白宇`）；`mid_room`（名字中间断）不受影响，因为教室名内部的空格最后
    一定会被 squeeze 掉。

坑 3 —— `y0 = baseline − fontsize`（实测 china-s 的 ascent 就是 1.0 倍字号）
    所以同一视觉行里混用字号会让各 span 的 y0 相差 `|Δsize|`，字号差 > 2.5 就断成
    两行 → 同一行的所有段必须同字号。跨行行距取 8.6pt（> 2.5 ✓）。

坑 4 —— MuPDF 会合并「同字号、同基线、几乎相接」的多个 insert_text
    `"教室" + " " + "教师"` 可能回来成 `"教室"` + `" 教师"` 两个 span（空格留在文字里），
    与真实 PDF 的三 span 结构对 parser 等价（它按 x0 拼接同行后再折叠空白）。
    真正要防的是**跨列粘连**：两段文字横向离得太近会被并成一个 span，整段就跑到左边
    那一列去了。所以每行都算总宽度并断言不许越过格子右边界（`_guard_lines`）。

坑 5 —— 打印日期：正则以前只认 `[\\d/]`，现在放宽到 `[\\d/\\-.]+`
    旧版 `打印日期\\s*[:：]?\\s*([\\d/]+)` 遇到连字符日期只会抓到
    `"2026"`（我第一次 --check 就是在这里红的）。parser 已放宽，两种写法现在都能
    抓全。本 fixture 仍用**斜杠形式**（与这条正则最初的写法一致），断言照实写死；
    连字符形式要不要覆盖，得先确认真实导出到底用哪一种，再改这里一行 —— 不要两种
    都画（`_parse_meta` 取第一个命中，两个都在反而看不出问题）。
坑 7 —— `insert_text(china-s)` 每个字符都按**全角 1 em** 推进（含数字/括号/空格）
    内置 CJK 字体走 CID 默认 W 数组：`Font.text_length` 给的是比例宽度，只有实际的
    一半左右（"教西D-101" 实测推进 9 em，text_length 只算出 4.6 em）。照比例宽度
    定位，后一段就压在前一段上 —— 渲染出来是叠字糊成一坨（第一版就踩了）。所以
    `_w()` 一律返回 `len(text)*size`，字号也因此压到 5.2pt：110pt 的列最多放 18 个
    全角字符。比真实导出的 6.5pt 比例字体小一些，这是**外观差异、不影响解析**
    （parser 只看 x0/y0/size）；副作用是 `(1~8周)(5-6节) 教西D-101` 这种 20 字符行
    放不下，所以 after_room 那格换了 5 个字的教室名 —— `_guard_lines` 会把这个约束
    硬性卡住，改数据时超宽会直接 SystemExit 而不是悄悄叠字。

坑 6 —— 空白 span 真的有用（反证过）
    把同一行改成「整行一次写完」（`实验大楼C座5楼向礼琴`，中间不画空格），解析立刻
    退化成 `room='实验大楼C座5楼向礼琴'`、`teacher=''` —— 教师被教室吃掉。所以本脚本
    坚持「教室 / 空格 / 教师」三段式画法，`check()` 里也有「没有教师被并进教室」的
    汇总断言。

本脚本对 parser 的当前状态做**实时**验证：`--check` 直接 import 工作区里的
`src/timetable/`，所以 parser 一改（例如正则、告警措辞变了）就能立刻在这里暴露出来。
"""
from __future__ import annotations

import argparse
import os
import sys

import pymupdf

# ---------------------------------------------------------------- 输出与字体
FONT = "china-s"          # PyMuPDF 内置 Droid Sans Fallback，简体中文实测可用
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DEFAULT = os.path.join(ROOT, "fixtures", "nwpu-sample.pdf")
TRAP_OUT_DEFAULT = os.path.join(ROOT, "fixtures", "nwpu-longname-trap.pdf")
SP = " "                  # 单独成段的空白 span（教室/教师的分隔符）

# ------------------------------------------------------------------ 版面常量
PAGE_W, PAGE_H = 842.0, 595.0        # A4 横向
MARGIN_L = 18.0
LABEL_X = 24.0                       # 节次栏文字起点（必须 < COL_LEFT）
COL0_LEFT = 70.0                     # 第一个星期列左边界 = parser 算出的 col_left
DAY_COL_W = 110.0                    # 列宽（= 相邻列中心间距 step）
CELL_PAD = 4.0                       # 格内左边距
LINE_BUDGET = DAY_COL_W - CELL_PAD - 2.0   # 单行总宽度上限（防跨列粘连）
# 空白段后面额外留 1 em 的空隙：MuPDF 会把「同字号、同基线、首尾相接」的多个
# insert_text 并成一个 span（实测间隙 <0.8em 就合并）。留这一点空隙才能保住
# 真实导出的「多 span 拼一行」结构：'(1~16周)(3-4节) ' + '实验大楼' 两个 span。
SEG_GAP_EM = 1.0

CENTRES = [COL0_LEFT + DAY_COL_W * (i + 0.5) for i in range(7)]
COL_X = [COL0_LEFT + DAY_COL_W * i + CELL_PAD for i in range(7)]

META_BASELINE = 24.0
META_LEAD = 13.0
META_SIZE = 8.0
HEADER_BASELINE = 72.0
HEADER_SIZE = 8.5

BODY_TOP = 88.0                      # 每格第一行的基线
SLOT_H = 62.0                        # 每个节次块高度
LEAD = 8.6                           # 格内行距（> 2.5）
MAX_LINES = int((SLOT_H - 6.0) / LEAD) + 1     # 一块最多几行不溢出

TITLE_SIZE = 5.2                     # 标题行/正文行/续行字号（正文不得小于 标题−0.15）
NOTE_SMALL_SIZE = 4.6                # 说明行①：靠字号判出来
LABEL_SIZE = 5.2                     # 节次栏
FOOT_SIZE = 5.6

FOOT_REMARK = BODY_TOP + SLOT_H * 7 + 6.0
FOOT_CREDITS = FOOT_REMARK + 13.0
FOOT_PRINT = FOOT_CREDITS + 15.0

SLOT_LABELS = ["1-2节", "3-4节", "5-6节", "7-8节", "9-10节", "11-12节", "13节"]
WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# ------------------------------------------------------------------ 行类型
TITLE = "title"      # 标题行（必须匹配 TITLE_RE）
TITLE2 = "title2"    # 长课程名换行的**第二行**（也匹配 TITLE_RE，靠 parser 残段拼接并回前一条目）
DETAIL = "detail"    # 正文行：同时含 (周次) 与 (节次)
CONT = "cont"        # 换行续行（绝不能匹配 TITLE_RE）
NOTE = "note"        # 说明行（小字号）
NOTE_KW = "notekw"   # 说明行（与标题同字号，靠 NOTE_WORD_RE 关键词）
LINE_SIZE = {TITLE: TITLE_SIZE, TITLE2: TITLE_SIZE, DETAIL: TITLE_SIZE,
             CONT: TITLE_SIZE, NOTE: NOTE_SMALL_SIZE, NOTE_KW: TITLE_SIZE}


# ---------------------------------------------------------------------- 行构造
def _wk(weeks, pfrom, pto):
    return "(%s周)(%d-%d节)" % (weeks, pfrom, pto)


def wrapped_name(part1, part2, weeks, pfrom, pto, room, teacher):
    """长课程名被排版换行截断的真实写法（两行名字 + 正文行）。

    `part1` 不以数字结尾 ⇒ 不匹配 TITLE_RE；`part2` 以恰好两位数字结尾 ⇒ 匹配。
    这正是「真实导出里 110pt 宽的列塞不下 19 个汉字」时出现的样子。
    parser 的残段拼接（`_parse_block_column`）会把 `part2` 并回 `part1` 的标题。

    **两行都不许带内部空格**：拼接是直接相加（不补空格），画进去的空格会原样留在
    课程名里。这里照真实排版在汉字之间断行，所以期望值是完整无空格的课程名。

    注意：这种条目必须是**所在列的第一行**。若它前面还有别的条目，`part1` 会被当成
    上一条课程的续行拼进它的 rest（上一条课程已经有正文行了，拼接守卫不会触发）。
    """
    return [(TITLE, [part1]), (TITLE2, [part2]),
            (DETAIL, [_wk(weeks, pfrom, pto)]), (CONT, [room, SP, teacher])]


def body(weeks, pfrom, pto, room, teacher, break_at="after_period", cut=None):
    """一个 `(周次)(节次) 教室 教师` 正文，按真实排版拆成 2 个物理行。

    break_at:
      "after_period" 第一行只有 `(1~16周)(1-2节)`，第二行 `教室 教师`
      "mid_room"     第一行 `(1~16周)(3-4节) 实验大楼C座`，第二行 `5楼 向礼琴`
                     （教师名从被截断的续行里补全）
      "after_room"   第一行 `(1~8周)(5-6节) 教西D-101`，第二行单独 `陈明远`
                     —— 断行正好落在教室/教师那个**分隔空格**上。旧版 parser 在这里
                     会把教师吃进教室（拼接不补空格）；现在靠补空格修掉了，
                     这条就是它的回归哨兵。
    cut：mid_room 时教室名的断点（默认 len(room)-3，即把 `5楼` 挪到第二行）。
    """
    tag = _wk(weeks, pfrom, pto)
    if break_at == "after_period":
        return [(DETAIL, [tag]), (CONT, [room, SP, teacher])]
    if break_at == "mid_room":
        k = len(room) - 3 if cut is None else cut
        return [(DETAIL, [tag, SP, room[:k]]), (CONT, [room[k:], SP, teacher])]
    if break_at == "after_room":
        return [(DETAIL, [tag, SP, room]), (CONT, [teacher])]
    raise ValueError("未知 break_at: %r" % break_at)


def entry(title, *lines):
    """一个条目：标题行 + 若干正文/续行/说明行。"""
    return [(TITLE, [title])] + list(lines)


class Cell(object):
    """格子里的一段内容。`line0` 是它在所属节次块内的起始行号（同格可叠多个条目）。"""

    def __init__(self, day, slot, line0, lines):
        self.day = day
        self.slot = slot
        self.line0 = line0
        self.lines = lines


# ------------------------------------------------------------------ 课表数据
# 周一
MON = [
    Cell(0, 0, 0, entry("高等数学A(上)06", *body("1~16", 1, 2, "教西D-101", "陈明远"))),
    Cell(0, 1, 0, entry("大学英语 12",                                   # J. 空格 + 数字
                        *body("1~16", 3, 4, "友谊西外A-302", "王小明"),
                        (NOTE, ["分级教学B班"]))),                        # I① 小字号
    # 坑 2 的回归哨兵：断行正好落在「教室 / 教师」那个分隔空格上。
    # 教室名特意挑 5 个字的：全角推进下 `(1~8周)(5-6节) 教西D-101` 是 20 字符 = 104pt，
    # 110pt 的列放不下（会压到下一列去），见坑 7。
    Cell(0, 2, 0, entry("文献检索与利用06",
                        *body("1~8", 5, 6, "教东101", "白宇",
                              break_at="after_room"))),
    Cell(0, 3, 0, entry("数据结构与算法08",                              # D. 换老师分段
                        *body("1~8", 7, 8, "教西A-201", "李华"),
                        *body("9~16", 7, 8, "教西A-201", "王强"))),
    Cell(0, 5, 0, entry("模拟电子技术06",                                # C. 导出器碎片
                        *body("1~8", 11, 12, "教西B-305", "赵磊"),
                        *body("1~8", 13, 13, "教西B-305", "赵磊"))),
]
# 周二（B. 单双周互补：两个条目叠在同一格里）
TUE = [
    Cell(1, 0, 0, entry("大学物理B(上)03",
                        *body("2~16(双)", 1, 2, "教西C-202", "孙雅雯"),
                        (NOTE_KW, ["大物实验"]))),                        # I② 同字号关键词
    Cell(1, 0, 4, entry("概率论与数理统计04",
                        *body("1~15(单)", 1, 2, "教西C-205", "高翔"))),
    Cell(1, 2, 0, entry("体育（篮球）02",
                        *body("1~16", 5, 6, "翱翔体育馆", "张伟"),
                        (NOTE, ["2024级本科公共课"]))),
    Cell(1, 3, 0, entry("数字电子技术06",                                # E/F. 多 span 一行
                        *body("1~16", 7, 8, "实验大楼C座5楼", "向礼琴"))),
    Cell(1, 5, 0, entry("复变函数与积分变换07",
                        *body("1~16", 11, 12, "教西A-105", "马俊"))),
]
# 周三 —— 本列**第一行**就是被换行截断的长课程名（残段拼接要求它是列内首个条目）
WED = [
    Cell(2, 0, 0, wrapped_name("毛泽东思想和中国特色社会主义",      # H. 长课程名换行（真实写法）
                               "理论体系概要08",
                               "1~16", 1, 2, "教东B-101", "范知远")),
    Cell(2, 1, 0, entry("操作系统原理06",                                # G② mid_room 续行
                        *body("1~16", 3, 4, "实验大楼C座5楼", "向礼琴",
                              break_at="mid_room", cut=4))),
    Cell(2, 2, 0, entry("数据结构与算法实验08",                           # G① 名字后的补充说明行
                        (CONT, ["（分四组轮转上课）"]),
                        *body("1~16", 5, 6, "计南B-206", "林可欣"))),
    Cell(2, 3, 0, entry("马克思主义基本原理05",
                        *body("1~16", 7, 8, "教东A-101", "谢云"))),
    Cell(2, 4, 0, entry("形势与政策08",
                        *body("1~16", 9, 10, "教东报告厅", "何平"))),
]
# 周四
THU = [
    Cell(3, 0, 0, entry("大学英语 12",
                        *body("1~16", 1, 2, "友谊西外A-302", "王小明"))),
    Cell(3, 1, 0, entry("机械设计基础05",
                        *body("1~16", 3, 4, "教西B-203", "郭永刚"))),
    Cell(3, 2, 0, entry("高等数学A(上)06",
                        *body("1~16", 5, 6, "教西D-101", "陈明远"))),
    Cell(3, 3, 0, entry("工程制图06",
                        *body("1~16", 7, 8, "制图中心A区", "袁斌"))),
    Cell(3, 4, 0, entry("人工智能导论08",
                        *body("1~16", 9, 10, "教西A-305", "崔明轩"))),
]
# 周五
FRI = [
    Cell(4, 0, 0, entry("高等数学A(上)06",
                        *body("1~16", 1, 2, "教西D-101", "陈明远"))),
    Cell(4, 1, 0, entry("创新创业基础07",                                # A. 前半学期结课
                        *body("1~8", 3, 4, "教东C-401", "韩雪"))),
    Cell(4, 2, 0, entry("数据库系统06",
                        *body("1~16", 5, 6, "计南B-206", "唐伯涛"))),
    Cell(4, 5, 0, entry("网络与安全09",
                        *body("1~16", 11, 12, "计南B-301", "白宇"))),
]
# 周六 / 周日 —— 溢出到第 2 页
SAT = [
    Cell(5, 0, 0, entry("劳动教育05",
                        *body("1~16", 1, 2, "农场实践基地", "邓小梅"))),
    Cell(5, 1, 0, entry("大学生心理健康09",
                        *body("1~16", 3, 4, "教东A-102", "冯佳"))),
]
SUN = [
    Cell(6, 0, 0, entry("影视鉴赏11",
                        *body("1~16", 1, 2, "音乐厅二楼", "苏婉"))),
    Cell(6, 3, 0, entry("体育（体质测试）04",
                        *body("2~16(双)", 7, 8, "翱翔体育馆", "张伟"))),
]

CELLS_P1 = MON + TUE + WED + THU + FRI
CELLS_P2 = SAT + SUN

META_LINES = [
    "西北工业大学学生课表（合成样例，数据全部虚构）",
    "学期： 2026-2027 秋    年级： 2024    学院： 计算机学院",
    "专业： 软件工程    班级： 软工2401    学号： 2024123456    姓名： 张三",
]
REMARK_LINE = ("备注： 1. 军事理论 HKJS101.01 (1~8) "
               "2. 社会实践 SJXS220.02 (1~16) 无固定时间地点")
CREDITS_LINE = "总学分： 42.5"


# ---------------------------------------------------------------------- 绘制
def _w(font, text, size):
    """`insert_text(fontname="china-s")` 的推进宽度 = **每字符 1 em**（实测）。

    内置 CJK 字体用的是 CID 字体的默认 W 数组：数字、括号、空格全都按全角推进。
    `Font.text_length` 给的是比例宽度（"教西D-101" 实测只 4.6 em），照它定位会让
    后一段压在前一段上（叠字，渲染出来就是糊的一坨）。所以这里一律 `len(text)*size`，
    版面宽度也以这个为准（真实导出是比例字体，同样的文字更窄，见坑 7）。
    """
    return len(text) * size


def draw_line(page, font, x, baseline, segs, size, lines_out, day):
    """一行拆成多个 span 画；空白段单独 `insert_text(" ")`。"""
    cur = x
    for seg in segs:
        page.insert_text((cur, baseline), seg, fontname=FONT, fontsize=size)
        adv = _w(font, seg, size)          # 每字符 1 em，段与段正好首尾相接
        lines_out.append(dict(day=day, text=seg, x0=cur, x1=cur + adv,
                              y=baseline, size=size))
        cur += adv
    return cur


def draw_grid(page):
    """表格线：纯装饰，让 PDF 看起来像真课表，不影响文字解析。"""
    shape = page.new_shape()
    top = BODY_TOP - 6.0
    bottom = top + SLOT_H * len(SLOT_LABELS)
    right = COL0_LEFT + DAY_COL_W * 7
    head_top = HEADER_BASELINE - HEADER_SIZE - 4.0
    shape.draw_line((LABEL_X - 6, head_top), (right, head_top))
    shape.draw_line((LABEL_X - 6, top), (right, top))
    shape.draw_line((LABEL_X - 6, bottom), (right, bottom))
    for i in range(8):
        x = COL0_LEFT + DAY_COL_W * i
        shape.draw_line((x, top), (x, bottom))
    for i in range(len(SLOT_LABELS) + 1):
        y = top + SLOT_H * i
        shape.draw_line((LABEL_X - 6, y), (right, y))
    shape.draw_line((COL0_LEFT, head_top), (COL0_LEFT, top))
    shape.finish(color=(0.55, 0.55, 0.55), width=0.6)
    shape.commit()


def draw_page(doc, cells, page_no, total_pages, font):
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    lines_out = []

    if page_no == 1:                                  # 元信息（parser 只读第 1 页）
        y = META_BASELINE
        for line in META_LINES:
            page.insert_text((MARGIN_L, y), line, fontname=FONT, fontsize=META_SIZE)
            y += META_LEAD

    for i, name in enumerate(WEEKDAY_NAMES):          # 表头
        x = CENTRES[i] - _w(font, name, HEADER_SIZE) / 2.0
        page.insert_text((x, HEADER_BASELINE), name, fontname=FONT, fontsize=HEADER_SIZE)

    draw_grid(page)

    for i, lab in enumerate(SLOT_LABELS):             # 左侧节次栏（x0 < col_left）
        page.insert_text((LABEL_X, BODY_TOP + SLOT_H * i), lab,
                         fontname=FONT, fontsize=LABEL_SIZE)

    for cell in cells:                                # 正文
        if cell.line0 + len(cell.lines) > MAX_LINES:
            raise SystemExit("格子内容溢出：day=%d slot=%d 需要 %d 行（上限 %d）"
                             % (cell.day, cell.slot, cell.line0 + len(cell.lines),
                                MAX_LINES))
        for j, (kind, segs) in enumerate(cell.lines):
            baseline = BODY_TOP + SLOT_H * cell.slot + LEAD * (cell.line0 + j)
            draw_line(page, font, COL_X[cell.day], baseline, segs, LINE_SIZE[kind],
                      lines_out, cell.day)

    y = FOOT_REMARK if page_no == 1 else FOOT_PRINT   # 页脚
    if page_no == 1:
        page.insert_text((MARGIN_L, FOOT_REMARK), REMARK_LINE,
                         fontname=FONT, fontsize=FOOT_SIZE)
        page.insert_text((MARGIN_L, FOOT_CREDITS), CREDITS_LINE,
                         fontname=FONT, fontsize=FOOT_SIZE)
    page.insert_text((MARGIN_L, FOOT_PRINT), "打印日期： 2026/08/25",
                     fontname=FONT, fontsize=FOOT_SIZE)
    page.insert_text((PAGE_W - 56.0, FOOT_PRINT), "%d/%d" % (page_no, total_pages),
                     fontname=FONT, fontsize=FOOT_SIZE)
    return lines_out


def _guard_lines(lines, page_no):
    """每行总宽度必须留在自己那一列里。

    越界不只是难看：相邻两列同基线的文字一旦靠得足够近，MuPDF 会把它们并成一个
    span，而 parser 只按 span 的 x0 定列 —— 整行就会跑到左边那一列去。
    """
    byline = {}
    for s in lines:
        byline.setdefault((s["day"], round(s["y"], 2)), []).append(s)
    for (day, y), segs in sorted(byline.items()):
        x0 = min(z["x0"] for z in segs)
        x1 = max(z["x1"] for z in segs)
        limit = COL0_LEFT + DAY_COL_W * (day + 1) - CELL_PAD
        if x1 > limit:
            raise SystemExit("第 %d 页 day=%d y=%.1f 这一行超出列宽 %.1fpt：%r"
                             % (page_no, day, y, x1 - limit,
                                "".join(z["text"] for z in segs)))
        if (x1 - x0) > LINE_BUDGET + 0.5:
            raise SystemExit("第 %d 页 day=%d 行宽 %.1f 超预算 %.1f"
                             % (page_no, day, x1 - x0, LINE_BUDGET))


def build(out_path, cells_p1=None, cells_p2=None):
    cells_p1 = CELLS_P1 if cells_p1 is None else cells_p1
    cells_p2 = CELLS_P2 if cells_p2 is None else cells_p2
    doc = pymupdf.open()
    font = pymupdf.Font(FONT)
    l1 = draw_page(doc, cells_p1, 1, 2, font)
    l2 = draw_page(doc, cells_p2, 2, 2, font)
    _guard_lines(l1, 1)
    _guard_lines(l2, 2)
    d = os.path.dirname(os.path.abspath(out_path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    doc.save(out_path)
    doc.close()
    return out_path, l1 + l2


def build_trap(out_path):
    """长课程名换行的**独立哨兵**（单页、只有这一门课，方便一眼看出拼接对不对）。

    2026-11-17 之前这个文件是「parser 修不好长名字换行」的缺陷证据（当时断言的是
    warning + 名字被切两半）；残段拼接修好之后保留下来当**回归哨兵**：
    现在必须解析成一整条课程、零告警。
    """
    doc = pymupdf.open()
    font = pymupdf.Font(FONT)
    cells = [Cell(2, 0, 0, wrapped_name("毛泽东思想和中国特色社会",
                                        "主义理论体系概要08",
                                        "1~16", 3, 4, "教东A-101", "谢云"))]
    draw_page(doc, cells, 1, 1, font)
    doc.save(out_path)
    doc.close()
    return out_path


# ---------------------------------------------------------------------- 读回
def dump_spans(pdf_path):
    doc = pymupdf.open(pdf_path)
    for pno in range(doc.page_count):
        page = doc[pno]
        d = page.get_text("dict")
        spans = [sp for b in d["blocks"] if b.get("type") == 0
                 for ln in b["lines"] for sp in ln["spans"]]
        spans.sort(key=lambda z: (round(z["bbox"][1], 1), z["bbox"][0]))
        print("=== 第 %d 页：%d 个 span ===" % (pno + 1, len(spans)))
        for sp in spans:
            x0, y0, x1, y1 = sp["bbox"]
            print("  y0=%7.2f x0=%7.2f x1=%7.2f size=%4.1f %r"
                  % (y0, x0, x1, sp["size"], sp["text"]))
    doc.close()


def load_modules():
    src = os.path.join(ROOT, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from timetable import ics as ics_mod
    from timetable import parser as parser_mod
    return parser_mod, ics_mod


# ---------------------------------------------------------------------- 断言
def expected_records():
    """期望解析结果，回归的唯一真相来源。

    (day, pfrom, pto, course, code, compress_weeks(all_weeks), room, teacher_desc)
    """
    full = "1~16"
    # 单/双周展开后不连续，compress_weeks 会写成顿号列举（不是 2~16）
    even = "2、4、6、8、10、12、14、16"
    odd = "1、3、5、7、9、11、13、15"
    E = [
        # 周一
        (0, 1, 2, "高等数学A(上)", "06", full, "教西D-101", ["陈明远"]),
        (0, 3, 4, "大学英语", "12", full, "友谊西外A-302", ["王小明"]),
        (0, 5, 6, "文献检索与利用", "06", "1~8", "教东101", ["白宇"]),
        (0, 7, 8, "数据结构与算法", "08", full, "教西A-201",
         ["1~8：李华", "9~16：王强"]),
        (0, 11, 12, "模拟电子技术", "06", "1~8", "教西B-305", ["赵磊"]),
        (0, 13, 13, "模拟电子技术", "06", "1~8", "教西B-305", ["赵磊"]),
        # 周二
        (1, 1, 2, "大学物理B(上)", "03", even, "教西C-202", ["孙雅雯"]),
        (1, 1, 2, "概率论与数理统计", "04", odd, "教西C-205", ["高翔"]),
        (1, 5, 6, "体育（篮球）", "02", full, "翱翔体育馆", ["张伟"]),
        (1, 7, 8, "数字电子技术", "06", full, "实验大楼C座5楼", ["向礼琴"]),
        (1, 11, 12, "复变函数与积分变换", "07", full, "教西A-105", ["马俊"]),
        # 周三
        (2, 1, 2, "毛泽东思想和中国特色社会主义理论体系概要", "08", full,
         "教东B-101", ["范知远"]),                     # H. 长名换行拼回完整课程名
        (2, 3, 4, "操作系统原理", "06", full, "实验大楼C座5楼", ["向礼琴"]),
        (2, 5, 6, "数据结构与算法实验", "08", full, "计南B-206", ["林可欣"]),
        (2, 7, 8, "马克思主义基本原理", "05", full, "教东A-101", ["谢云"]),
        (2, 9, 10, "形势与政策", "08", full, "教东报告厅", ["何平"]),
        # 周四
        (3, 1, 2, "大学英语", "12", full, "友谊西外A-302", ["王小明"]),
        (3, 3, 4, "机械设计基础", "05", full, "教西B-203", ["郭永刚"]),
        (3, 5, 6, "高等数学A(上)", "06", full, "教西D-101", ["陈明远"]),
        (3, 7, 8, "工程制图", "06", full, "制图中心A区", ["袁斌"]),
        (3, 9, 10, "人工智能导论", "08", full, "教西A-305", ["崔明轩"]),
        # 周五
        (4, 1, 2, "高等数学A(上)", "06", full, "教西D-101", ["陈明远"]),
        (4, 3, 4, "创新创业基础", "07", "1~8", "教东C-401", ["韩雪"]),
        (4, 5, 6, "数据库系统", "06", full, "计南B-206", ["唐伯涛"]),
        (4, 11, 12, "网络与安全", "09", full, "计南B-301", ["白宇"]),
        # 周六 / 周日（第 2 页）
        (5, 1, 2, "劳动教育", "05", full, "农场实践基地", ["邓小梅"]),
        (5, 3, 4, "大学生心理健康", "09", full, "教东A-102", ["冯佳"]),
        (6, 1, 2, "影视鉴赏", "11", full, "音乐厅二楼", ["苏婉"]),
        (6, 7, 8, "体育（体质测试）", "04", even, "翱翔体育馆", ["张伟"]),
    ]
    return E


def _only_titles(cells_a, cells_b):
    """cells_a 里出现、cells_b 里没出现的课程标题（`课程名 + 两位代码` 原样）。

    用来证明「第 2 页独有」的课程集合，再拿它去核对解析结果 —— 跨页合并的证据。
    """
    def titles(cs):
        out = set()
        for c in cs:
            for kind, segs in c.lines:
                if kind == TITLE:
                    out.add("".join(segs))
        return out
    return titles(cells_a) - titles(cells_b)


def check(pdf_path):
    parser, ics_mod = load_modules()
    compress_weeks = parser.compress_weeks
    res = parser.parse_pdf(pdf_path, log=lambda m: None)
    done = []

    def expect(cond, msg):
        if not cond:
            raise AssertionError("FAIL: " + msg)
        done.append(msg)

    # ---- 先把解析结果整个打出来，方便对照（断言失败时也能看到现场）----
    md = res.meta
    print("meta：学期=%s 年级=%s 学院=%s 专业=%s 班级=%s 学号=%s 姓名=%s 总学分=%s "
          "打印日期=%s" % (md.term, md.grade, md.college, md.major, md.klass, md.sid,
                          md.name, md.total_credits, md.print_date))
    print("备注课程：%s" % (md.remark_courses,))
    print("warnings：%s" % (res.warnings,))
    print("-" * 96)
    for r in sorted(res.records, key=lambda z: (z.day, z.pfrom, z.course)):
        print("  %s %-5s %-22s %-8s %-16s %-14s notes=%s"
              % (r.day_name, "%d-%d节" % (r.pfrom, r.pto), r.course + "|" + r.code,
                 compress_weeks(r.all_weeks()), r.room,
                 "；".join(r.teacher_desc()), r.notes))
    print("-" * 96)

    # ---- warnings ----
    # 「PDF 共 N 页，将逐页解析并合并。」这类**页数提示**是信息性输出（不同版本 parser
    # 加不加都有可能），当作允许项；真正的解析失败告警是「没解析出周次和节次…已跳过」。
    bad = [w for w in res.warnings if "没解析出" in w or "跳过" in w]
    expect(not bad, "无解析失败告警（实际 %r）" % (bad,))
    noise = [w for w in res.warnings if "PDF 共" not in w and "页" not in w]
    expect(not noise, "除页数提示外没有任何别的 warning（实际 %r）" % (noise,))

    md = res.meta
    for label, got, want in [
        ("term", md.term, "2026-2027 秋"), ("grade", md.grade, "2024"),
        ("college", md.college, "计算机学院"), ("major", md.major, "软件工程"),
        ("klass", md.klass, "软工2401"), ("sid", md.sid, "2024123456"),
        ("name", md.name, "张三"), ("total_credits", md.total_credits, "42.5"),
        ("print_date", md.print_date, "2026/08/25"),
    ]:
        expect(got == want, "meta.%s == %r" % (label, want))

    expect(len(md.remark_courses) == 2, "备注抓到 2 门无固定时间课程（实际 %d）"
           % len(md.remark_courses))
    rc = {c["name"]: c for c in md.remark_courses}
    expect(rc.get("军事理论", {}).get("code") == "HKJS101.01"
           and rc["军事理论"]["week_from"] == 1 and rc["军事理论"]["week_to"] == 8,
           "备注课程 军事理论 HKJS101.01 (1~8)")
    expect(rc.get("社会实践", {}).get("code") == "SJXS220.02"
           and rc["社会实践"]["week_to"] == 16, "备注课程 社会实践 SJXS220.02 (1~16)")

    exp = expected_records()
    expect(len(res.records) == len(exp), "记录数 == %d" % len(exp))
    got = {(r.day, r.pfrom, r.pto, r.course): r for r in res.records}
    mismatch = []
    for (day, pfrom, pto, course, code, weeks, room, tdesc) in exp:
        r = got.get((day, pfrom, pto, course))
        if r is None:
            mismatch.append("缺记录 %s %d-%d %s" % (day, pfrom, pto, course))
            continue
        if r.code != code:
            mismatch.append("%s code=%r 应为 %r" % (course, r.code, code))
        if compress_weeks(r.all_weeks()) != weeks:
            mismatch.append("%s 周次=%r 应为 %r"
                            % (course, compress_weeks(r.all_weeks()), weeks))
        if r.room != room:
            mismatch.append("%s 教室=%r 应为 %r" % (course, r.room, room))
        if r.teacher_desc() != tdesc:
            mismatch.append("%s 教师=%r 应为 %r" % (course, r.teacher_desc(), tdesc))
    expect(not mismatch, "全部 %d 条记录的 星期/节次/课程/代码/周次/教室/教师 "
                         "逐字段与期望一致（差异 %s）" % (len(exp), mismatch))
    extra = sorted(set(got) - {(e[0], e[1], e[2], e[3]) for e in exp})
    expect(not extra, "没有多出来的记录（实际多了 %s）" % (extra,))

    # ---- 专项：单双周互补（同格不同课，零冲突）----
    a = got[(1, 1, 2, "大学物理B(上)")]
    b = got[(1, 1, 2, "概率论与数理统计")]
    wa, wb = set(a.all_weeks()), set(b.all_weeks())
    expect(wa == set(range(2, 17, 2)) and wb == set(range(1, 16, 2)) and not (wa & wb),
           "同格单双周互补：双周 %s / 单周 %s / 交集为空"
           % (compress_weeks(sorted(wa)), compress_weeks(sorted(wb))))

    # ---- 专项：换老师分段（一条 Record 两个 Segment）----
    seg = got[(0, 7, 8, "数据结构与算法")]
    expect(len(seg.segments) == 2, "换老师分段 → 2 个 Segment")
    expect(seg.teacher_for(3) == "李华" and seg.teacher_for(12) == "王强"
           and seg.teacher_for(8) == "李华" and seg.teacher_for(9) == "王强",
           "teacher_for(3/8/9/12) = 李华/李华/王强/王强")

    # ---- 专项：导出器碎片 + merge_contiguous ----
    frag = sorted([r for r in res.records if r.course == "模拟电子技术"],
                  key=lambda z: z.pfrom)
    expect(len(frag) == 2 and (frag[0].pfrom, frag[0].pto) == (11, 12)
           and (frag[1].pfrom, frag[1].pto) == (13, 13),
           "导出器碎片解析成 11-12节 + 13-13节 两条记录")
    m = [r for r in ics_mod.merge_contiguous(res.records, "artifact")
         if r.course == "模拟电子技术"]
    expect(len(m) == 1 and (m[0].pfrom, m[0].pto) == (11, 13),
           "merge_contiguous('artifact') 合成 11-13节")
    expect(len([r for r in ics_mod.merge_contiguous(res.records, "none")
                if r.course == "模拟电子技术"]) == 2, "mode='none' 保持两条不合并")
    expect([r for r in ics_mod.merge_contiguous(res.records, "all")
            if r.course == "数据结构与算法"][0].pto == 8,
           "mode='all' 没有把不相邻的节次乱合")

    # ---- 专项：教室名被截断的续行 ----
    osr = got[(2, 3, 4, "操作系统原理")]
    expect(osr.room == "实验大楼C座5楼" and osr.segments[0].teacher == "向礼琴",
           "mid_room 续行拼接：room=%r teacher=%r" % (osr.room, osr.segments[0].teacher))

    # ---- 专项：长课程名换行（真实写法，靠 parser 残段拼接把两行名字并回一条）----
    long_name = "毛泽东思想和中国特色社会主义理论体系概要"
    ln = got.get((2, 1, 2, long_name))
    expect(ln is not None,
           "长课程名被拼回成一条完整课程（没拼回来时这里会给出实际切分结果：%s）"
           % sorted(k[3] for k in got if k[0] == 2))
    expect(ln.code == "08" and ln.room == "教东B-101"
           and ln.teacher_desc() == ["范知远"],
           "长课程名换行拼回完整名字后，代码/教室/教师仍然正确（%r %r %r）"
           % (ln.code, ln.room, ln.teacher_desc()))
    # 拼接是直接相加不补空格，所以名字里不该有空格。真出现空格就是缺陷
    # （要么绘制时手滑加了空格，要么 parser 改成带空格拼接）—— 不要改期望值迁就它。
    expect(" " not in ln.course,
           "拼回来的课程名内部没有多余空格（实际 %r）" % (ln.course,))
    expect(not any(r.course == "理论体系概要" for r in res.records),
           "名字后半段没有被当成独立课程开出去")
    # 另一种两行条目：第二行是补充说明（不以数字结尾）→ 仍按 notes 走，
    # 不能被残段拼接误当成课程名的一部分（这条同时守卫生效边界）。
    sup = got[(2, 5, 6, "数据结构与算法实验")]
    expect(sup.course == "数据结构与算法实验" and sup.code == "08"
           and any("分四组" in n for n in sup.notes),
           "「合法标题行 + 非数字结尾的补充行」仍走 notes：%r" % (sup.notes,))

    # ---- 专项：断行正好落在「教室 / 教师」的分隔空格上（坑 2 的回归哨兵）----
    ar = got[(0, 5, 6, "文献检索与利用")]
    expect(ar.room == "教东101" and ar.segments[0].teacher == "白宇",
           "断行落在分隔空格上时教师不被吃掉：room=%r teacher=%r"
           % (ar.room, ar.segments[0].teacher))

    # ---- 专项：说明行的两条判定路径 ----
    expect("2024级本科公共课" in got[(1, 5, 6, "体育（篮球）")].notes
           and "分级教学B班" in got[(0, 3, 4, "大学英语")].notes,
           "小字号说明行 → notes（靠 size < entry_size − 0.15）")
    expect("大物实验" in got[(1, 1, 2, "大学物理B(上)")].notes,
           "同字号说明行 → notes（靠 NOTE_WORD_RE 关键词）")

    # ---- 跨页证据：第 2 页不是空页，它真的贡献了课程记录 ----
    # 只出现在第 2 页（CELLS_P2）的标题，按绘制数据本身算出来，再回去核对解析结果，
    # 这样即使以后有人往第 1 页加了同名课，这条断言也不会变成假绿。
    p2_only = _only_titles(CELLS_P2, CELLS_P1)
    printed = {(r.course + r.code) for r in res.records}
    expect(p2_only and p2_only <= printed,
           "第 2 页独有的 %d 门课程全部被解析出来：%s" % (len(p2_only), sorted(p2_only)))
    for name in sorted(p2_only):
        print("  [page2] 第 2 页贡献记录：%s（%s）"
              % (name, "、".join(sorted({r.day_name + "%d-%d节" % (r.pfrom, r.pto)
                                        for r in res.records
                                        if r.course + r.code == name}))))
    expect(res.stats.get("body_spans", 0) > 0, "正文 span 统计 = %s"
           % (res.stats.get("body_spans"),))

    days = {r.day for r in res.records}
    expect(days == set(range(7)), "7 天都有课（跨页合并生效）")
    expect(sum(1 for r in res.records if r.day >= 5) == 4, "周六/周日 4 条记录")
    # 教室/教师边界与完整性：一条汇总断言（失败时把每一条问题都列出来）
    bad_room = ["%s: room=%r" % (r.course, r.room)
                for r in res.records if not r.room or " " in r.room]
    expect(not bad_room, "所有记录教室名完整且无残留空格（问题项 %s）" % (bad_room,))
    bad_teacher = ["%s: teacher=%r" % (r.course, s.teacher)
                   for r in res.records for s in r.segments
                   if s.teacher and not (2 <= len(s.teacher) <= 4
                                         and all(u"一" <= c <= u"鿿" for c in s.teacher))]
    expect(not bad_teacher, "所有教师名都是 2~4 个汉字（问题项 %s）" % (bad_teacher,))
    # 教室没吃掉教师的硬证据：教师名不许作为子串出现在教室名里
    swallowed = ["%s: room=%r teacher=%r" % (r.course, r.room, s.teacher)
                 for r in res.records for s in r.segments
                 if s.teacher and s.teacher in r.room]
    expect(not swallowed, "空白 span 生效：没有教师被并进教室（问题项 %s）"
           % (swallowed,))
    no_weeks = [r.course for r in res.records
                if not r.segments or not all(s.weeks for s in r.segments)]
    expect(not no_weeks, "每个 Segment 都有周次（问题项 %s）" % (no_weeks,))

    chk = parser.verify_parse(res)
    expect(not chk["conflicts"], "verify_parse 零冲突（实际 %r）" % (chk["conflicts"][:3],))
    expect(chk["records"] == len(exp), "verify_parse 记录数 == %d" % len(exp))
    expect(len(chk["courses"]) == len({e[3] for e in exp}),
           "独立课程数 == 期望表里的课程数（%d 门）" % len(chk["courses"]))

    print("--check 通过：%d 项断言全部成立" % len(done))
    for line in done:
        print("  [ok] " + line)
    print("统计：记录 %d 条 / 上课实例 %d 次 / 冲突 %d / 课程 %d 门 / 正文 span %s 个"
          % (chk["records"], chk["instances"], len(chk["conflicts"]),
             len(chk["courses"]), res.stats.get("body_spans")))
    return 0


def check_trap(trap_path):
    """长课程名换行哨兵样例：断言的是**修复后的正确行为**。

    历史（重要）：2026-11-17 之前 parser 没有「残段拼接」，这个文件当时的实际输出是

        warnings = ['「毛泽东思想和中国特色社会」（周三）没解析出周次和节次，已跳过。']
        记录 course='理论体系概要' code='08'          ← 名字被切两半，前半段整条丢失

    那时本函数的断言就是那个**错误现状**，用途是「已知缺陷的证据」。parser 补上
    `_parse_block_column` 的残段拼接之后，缺陷修掉了，于是：
      * 文件名 `nwpu-longname-trap.pdf` 保留（其他脚本可能引用）；
      * 断言**反转**成修复后的正确行为：一条完整课程 + 零告警 + 代码/教室/教师全对。
    留一份「只有一门课」的独立文件，是因为拼接一旦再坏，这里一眼就能看出来
    （主 fixture 有 29 条记录，翻车时不好定位）。
    """
    parser, _ = load_modules()
    res = parser.parse_pdf(trap_path, log=lambda m: None)
    print("== 长课程名换行哨兵样例：%s" % trap_path)
    print("   warnings = %s" % (res.warnings,))
    for r in res.records:
        print("   记录 %s %d-%d course=%r code=%r weeks=%r room=%r teacher=%r notes=%r"
              % (r.day_name, r.pfrom, r.pto, r.course, r.code,
                 parser.compress_weeks(r.all_weeks()), r.room, r.teacher_desc(),
                 r.notes))
    bad = [w for w in res.warnings if "没解析出" in w or "跳过" in w]
    assert not bad, "残段拼接失效了：%r" % (bad,)
    assert len(res.records) == 1, "这个样例应该只有 1 条记录，实际 %d" % len(res.records)
    r = res.records[0]
    want = "毛泽东思想和中国特色社会主义理论体系概要"
    assert r.course == want, "课程名没被完整拼回：实际 %r，期望 %r" % (r.course, want)
    # 拼接是直接相加（不补空格），所以名字里不该出现任何空格。
    # 如果这里报「有空格」：要么是本脚本绘制时在两行里手滑加了空格（改本脚本），
    # 要么是 parser 把拼接改成带空格了（那要评估：真实 PDF 的换行处不该有语义空格，
    # 拼出带空格的课程名属于缺陷 —— 别改期望值迁就它）。
    assert " " not in r.course, "课程名里混进了空格（见上面注释，这是缺陷不是现状）：%r" \
        % (r.course,)
    assert r.code == "08", r.code
    assert r.room == "教东A-101", r.room
    assert r.teacher_desc() == ["谢云"], r.teacher_desc()
    assert parser.compress_weeks(r.all_weeks()) == "1~16"
    print("   [ok] 断言成立：两行截断的长课程名被拼回一条完整课程，零告警")
    return 0


# ---------------------------------------------------------------------- main
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="合成西工大课表 PDF fixture")
    ap.add_argument("-o", "--out", default=OUT_DEFAULT, help="输出 PDF 路径")
    ap.add_argument("--dump", action="store_true", help="打印生成后的 span 清单")
    ap.add_argument("--check", action="store_true", help="生成 + 解析 + 断言（回归入口）")
    ap.add_argument("--longname-trap", action="store_true",
                    help="额外生成并断言「长课程名换行」陷阱样例")
    args = ap.parse_args(argv)

    path, spans = build(args.out)
    print("已生成：%s（绘制 %d 个文字段，%d 字节）"
          % (path, len(spans), os.path.getsize(path)))

    if args.dump:
        dump_spans(path)
    rc = 0
    if args.check:
        rc = check(path)
    if args.longname_trap:
        rc |= check_trap(build_trap(TRAP_OUT_DEFAULT))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
