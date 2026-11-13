# -*- coding: utf-8 -*-
"""MemoPen 隨手記 V1.1.4 — PySide6 桌面版／iOS 互動原型。

此為 Windows / macOS / Linux Python 桌面程式，不是可直接安裝的 iOS App。
功能：十階編碼、子樹升降階、常駐圖示、游標安全的事件式自動儲存、
工具列字體調整、自適應窄視窗、可指定預設儲存資料夾。
執行：python -m pip install PySide6 ; python MemoPen_V1.1.4.pyw
"""

# nuitka-project: --enable-plugin=pyside6
# nuitka-project-if: {OS} == "Windows":
#    nuitka-project: --windows-console-mode=disable
#    nuitka-project: --product-name=MemoPen
#    nuitka-project: --file-description="MemoPen - 隨手記"
#    nuitka-project: --product-version=1.1.4.0
#    nuitka-project: --file-version=1.1.4.0

from __future__ import annotations

import base64
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

APP_NAME: Final = "MemoPen"
APP_TITLE: Final = "隨手記"
APP_VERSION: Final = "V1.1.4"
SAVE_IDLE_MS: Final = 2000  # 僅停止輸入後觸發，絕無每 30 秒的週期儲存
MIN_WIDTH: Final = 340
MIN_HEIGHT: Final = 320
RESIZE_MARGIN: Final = 8
WINDOW_BORDER_PT: Final = 2
TOOL_BUTTON_WIDTH: Final = 30
TOOL_BUTTON_HEIGHT: Final = 30
FULLWIDTH_SPACE: Final = "\u3000"
CHINESE_DIGITS: Final = "零一二三四五六七八九"
NUMERIC_LIMIT: Final = 100
LETTERS_LIMIT: Final = 26

# 符號檢查順序不可任意更動：括號國字與括號數字應先於一般型式。
PATTERNS: Final = (
    re.compile(r"^([一二三四五六七八九十百千零]+)、"),
    re.compile(r"^\(([一二三四五六七八九十百千零]+)\)"),
    re.compile(r"^(\d{1,3})、"),
    re.compile(r"^\((\d{1,3})\)"),
    re.compile(r"^(\d{1,3})\."),
    re.compile(r"^(\d{1,3})\)"),
    re.compile(r"^([A-Z])、"),
    re.compile(r"^([a-z])、"),
    re.compile(r"^\(([A-Z])\)"),
    re.compile(r"^\(([a-z])\)"),
)


@dataclass(frozen=True)
class Item:
    level: int
    indent: str
    marker: str
    body: str

    @property
    def prefix(self) -> str:
        return self.indent + self.marker


class NumberingError(ValueError):
    """編碼層級、序號或母階不符合規定。"""


def chinese_number(value: int) -> str:
    """1–9999 的國字序號，支援 十一、一百零一、一千零一。"""
    if not 1 <= value <= 9999:
        raise NumberingError("國字編號已達 9999 上限")
    result = ""
    zero_pending = False
    for divisor, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, value = divmod(value, divisor)
        if digit:
            if zero_pending:
                result += "零"
                zero_pending = False
            if not (divisor == 10 and digit == 1 and not result):
                result += CHINESE_DIGITS[digit]
            result += unit
        elif result and value:
            zero_pending = True
    return result


def marker_for(level: int, number: int) -> str:
    if not 0 <= level < 10:
        raise NumberingError("僅支援 1～10 階")
    if level in (0, 1):
        n = chinese_number(number)
        return f"{n}、" if level == 0 else f"({n})"
    if level in (2, 3, 4, 5):
        if not 1 <= number <= NUMERIC_LIMIT:
            raise NumberingError(f"第 {level+1} 階最多 {NUMERIC_LIMIT} 項")
        return (f"{number}、", f"({number})", f"{number}.", f"{number})")[level - 2]
    if not 1 <= number <= LETTERS_LIMIT:
        raise NumberingError(f"第 {level+1} 階最多 {LETTERS_LIMIT} 項")
    alpha = chr(ord("A" if level in (6, 8) else "a") + number - 1)
    return f"{alpha}、" if level in (6, 7) else f"({alpha})"


def parse_item(line: str) -> Item | None:
    """辨識清單符號；一般段落不受更動。"""
    i = 0
    while i < len(line) and line[i] in " \t\u3000":
        i += 1
    indent, rest = line[:i], line[i:]
    for level, pattern in enumerate(PATTERNS):
        match = pattern.match(rest)
        if not match:
            continue
        # 101～999 不視為有效的阿拉伯數字編碼，避免誤重編。
        if 2 <= level <= 5 and int(match.group(1)) > NUMERIC_LIMIT:
            return None
        return Item(level, indent, match.group(0), rest[match.end():])
    return None


def renumber_lines(lines: list[str]) -> list[str]:
    """依母項唯一識別位置逐階重編；保留普通文字與孤兒階層原文。

    兩個以上連續空白行視為獨立清單區段；同一母階的兄弟獨立計數。
    """
    output = lines[:]
    parents: dict[int, int] = {}
    counters: dict[tuple[int, ...], int] = {}
    group = 0
    blanks = 0
    for index, line in enumerate(lines):
        item = parse_item(line)
        if item is None:
            blanks = blanks + 1 if not line.strip() else 0
            if blanks == 2:
                group += 1
                parents.clear()
            continue
        blanks = 0
        level = item.level
        # 先清掉此階及更深階的舊母項路徑。
        parents = {k: v for k, v in parents.items() if k < level}
        if level and any(k not in parents for k in range(level)):
            # 舊檔純數字 1、系列可能被解析為第 3 階；不可擅自改編。
            continue
        parent_path = (group,) + tuple(parents[k] for k in range(level))
        current = counters.get(parent_path, 0) + 1
        new_marker = marker_for(level, current)  # 達上限即拋錯，不修改原檔
        counters[parent_path] = current
        output[index] = FULLWIDTH_SPACE * level + new_marker + item.body
        parents[level] = index
    return output


def subtree_end(lines: list[str], start: int) -> int:
    """含 start 項子孫與其未編碼說明，回傳不含尾端的索引。"""
    root = parse_item(lines[start])
    if root is None:
        return start + 1
    for index in range(start + 1, len(lines)):
        item = parse_item(lines[index])
        if item is not None and item.level <= root.level:
            return index
        # 明確的雙空白段落不再視為前一項的附屬內容。
        if index >= start + 2 and not lines[index].strip() and not lines[index-1].strip():
            return index - 1
    return len(lines)


def preceding_sibling(lines: list[str], index: int) -> int | None:
    item = parse_item(lines[index])
    if item is None:
        return None
    for j in range(index - 1, -1, -1):
        older = parse_item(lines[j])
        if older is None:
            if j > 0 and not lines[j].strip() and not lines[j - 1].strip():
                break
            continue
        if older.level < item.level:
            break
        if older.level == item.level:
            return j
    return None


def change_hierarchy(lines: list[str], first: int, last: int, delta: int) -> list[str]:
    """選取根項與其完整子樹升降階，並重新計算所有相關兄弟序號。"""
    if delta not in (-1, 1):
        raise ValueError("delta 必須是 -1 或 1")
    candidates = [i for i in range(max(0, first), min(last + 1, len(lines)))
                  if parse_item(lines[i]) is not None]
    if not candidates:
        raise NumberingError("請先將游標放在編碼項目上")
    roots: list[int] = []
    skip_until = -1
    for i in candidates:
        if i < skip_until:
            continue
        roots.append(i)
        skip_until = subtree_end(lines, i)
    spans: list[tuple[int, int]] = []
    for start in roots:
        root = parse_item(lines[start])
        assert root is not None
        if delta < 0 and root.level == 0:
            raise NumberingError("第一階不可再升階")
        if delta > 0 and preceding_sibling(lines, start) is None:
            raise NumberingError("降階需要同母階的前一個同階項目")
        stop = subtree_end(lines, start)
        if any((part := parse_item(lines[i])) and not 0 <= part.level + delta < 10
               for i in range(start, stop)):
            raise NumberingError("階層超出 1～10 階範圍")
        spans.append((start, stop))
    new_lines = lines[:]
    for start, stop in spans:
        for i in range(start, stop):
            part = parse_item(lines[i])
            if part is not None:
                newlevel = part.level + delta
                new_lines[i] = FULLWIDTH_SPACE * newlevel + marker_for(newlevel, 1) + part.body
            elif lines[i].startswith(FULLWIDTH_SPACE):
                # 附屬文字跟著調整縮排（無編碼的說明段落）。
                if delta > 0:
                    new_lines[i] = FULLWIDTH_SPACE + lines[i]
                else:
                    new_lines[i] = lines[i][1:]
    return renumber_lines(new_lines)


def prepare_new_item(lines: list[str], index: int) -> tuple[list[str], int, bool]:
    """返回新段落、新目標行號、是否插入新行；不直接操作 UI。"""
    current = lines[index]
    part = parse_item(current)
    level = 0
    if part is not None:
        level = part.level
    else:
        # 優先延續上一個已解析的階層（必須具有合法母階）。
        for j in range(index - 1, -1, -1):
            prior = parse_item(lines[j])
            if prior is not None:
                level = prior.level
                break
            if j > 0 and not lines[j].strip() and not lines[j - 1].strip():
                break
    if level > 0 and not has_parent(lines, index if part else index - 1, level):
        # 舊版單層「1、」不可直接視為第 3 階。
        raise NumberingError("找不到母階；舊版 1、清單請先使用「轉換舊版編碼」")
    initial = FULLWIDTH_SPACE * level + marker_for(level, 1)
    if current.strip():
        # 接在目前項目完整子樹之後，不能插入它的子項之間。
        position = subtree_end(lines, index) if part is not None else index + 1
        updated = lines[:position] + [initial] + lines[position:]
        new_index, inserted = position, True
    else:
        updated = lines[:]
        updated[index] = initial
        new_index, inserted = index, False
    return renumber_lines(updated), new_index, inserted


def has_parent(lines: list[str], at_index: int, level: int) -> bool:
    """只接受同一段清單內連續、有效的母階路徑，不跨越其他母項。"""
    if level == 0:
        return True
    parents: dict[int, int] = {}
    blanks = 0
    for j in range(0, min(at_index + 1, len(lines))):
        part = parse_item(lines[j])
        if part is None:
            blanks = blanks + 1 if not lines[j].strip() else 0
            if blanks >= 2:
                parents.clear()
            continue
        blanks = 0
        parents = {k: v for k, v in parents.items() if k < part.level}
        if part.level == 0 or all(k in parents for k in range(part.level)):
            parents[part.level] = j
    return all(k in parents for k in range(level))


def item_number(item: Item) -> int:
    token = item.marker.strip("、().")
    if 2 <= item.level <= 5:
        return int(token)
    if item.level >= 6:
        return ord(token.upper()) - ord("A") + 1
    total = digit = 0
    for char in token:
        if char in CHINESE_DIGITS:
            digit = CHINESE_DIGITS.index(char)
        else:
            total += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
    return total + digit


def continue_numbering(lines: list[str], index: int, split_at: int,
                       item: Item, legacy: bool) -> tuple[list[str], int]:
    """保留目前編碼，在子項之後新增同階項目並順延後續同階序號。"""
    number = item_number(item) + 1
    indent = "" if legacy else FULLWIDTH_SPACE * item.level
    position = index + 1
    limit = subtree_end(lines, index)
    if not legacy and any(parse_item(line) for line in lines[position:limit]):
        position = limit
        while position > index + 1 and not lines[position - 1].strip():
            position -= 1

    current = lines[index]
    new_line = indent + marker_for(item.level, number) + current[split_at:]
    updated = lines[:position] + [new_line] + lines[position:]
    updated[index] = current[:split_at]
    blanks = 0
    for j in range(position + 1, len(updated)):
        sibling = parse_item(updated[j])
        if sibling is None:
            blanks = blanks + 1 if not updated[j].strip() else 0
            if blanks == 2:
                break
            continue
        blanks = 0
        if sibling.level < item.level or (legacy and sibling.indent):
            break
        if sibling.level == item.level:
            number += 1
            updated[j] = sibling.indent + marker_for(item.level, number) + sibling.body
    return updated, position


def convert_legacy_numbering(lines: list[str]) -> list[str]:
    """明確由使用者啟動的舊式純 1、2、3、 -> 第一階國字轉換。"""
    if any((x := parse_item(line)) is not None and x.level in (0, 1) for line in lines):
        raise NumberingError("檔案已有國字母階，為避免誤改不能批次轉換")
    converted = lines[:]
    count = 0
    for i, line in enumerate(lines):
        part = parse_item(line)
        if part is not None and part.level == 2 and not part.indent:
            # 只處理行首未縮排的 1、系列。
            count += 1
            converted[i] = marker_for(0, count) + part.body
    if not count:
        raise NumberingError("找不到可以轉換的舊式 1、清單")
    return renumber_lines(converted)


def readable_text(path: Path) -> str:
    last: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                return file.read()
        except UnicodeDecodeError as exc:
            last = exc
        except OSError as exc:
            last = exc
            break
    raise OSError(f"無法讀取文字：{last}")


def disk_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def atomic_write(path: Path, content: str, expected: tuple[int, int] | None) -> tuple[int, int]:
    """此函式僅於背景工作執行；不得存取或控制 QTextEdit。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if disk_signature(path) != expected:
        raise OSError("檔案已在其他程式被修改，未覆蓋原檔；請先另存")
    from PySide6.QtCore import QIODevice, QSaveFile  # 在工作執行緒內建立與使用
    file = QSaveFile(str(path))
    if not file.open(QIODevice.WriteOnly):
        raise OSError(file.errorString())
    data = content.encode("utf-8")
    size = file.write(data)
    if size != len(data):
        file.cancelWriting()
        raise OSError(f"資料寫入不完整：{size}/{len(data)}")
    if not file.commit():
        raise OSError(file.errorString())
    signature = disk_signature(path)
    if signature is None:
        raise OSError("寫入後找不到檔案")
    return signature


def show_dependency_error(detail: str) -> None:
    message = f"MemoPen 需要 PySide6。\n\n請安裝：py -m pip install -U PySide6\n\n{detail}"
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


try:
    from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QSettings, QStandardPaths, Qt, QTimer, Signal
    from PySide6.QtGui import (QColor, QFont, QIcon, QKeyEvent, QKeySequence,
                               QPainter, QPainterPath, QPen, QPixmap, QShortcut, QTextCursor)
    from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame,
                                   QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
                                   QSizePolicy,
                                   QSlider, QSystemTrayIcon, QTextEdit, QToolButton, QVBoxLayout,
                                   QWidget)
except ModuleNotFoundError as exc:
    if __name__ == "__main__":
        show_dependency_error(str(exc))
        raise SystemExit(1) from exc
    # 允許純編碼模組以 import / 測試方式載入（沒有 PySide6 的 CI 環境）。
    QApplication = None  # type: ignore[assignment,misc]


def ui_font(size: int = 10, bold: bool = False):
    if sys.platform == "darwin":
        family = "PingFang TC"
    elif sys.platform.startswith("win"):
        family = "Microsoft JhengHei UI"
    else:
        family = "Noto Sans CJK TC"
    font = QFont(family)
    font.setPointSizeF(float(size))
    font.setBold(bold)
    return font


def default_storage_folder() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    return (Path(base) if base else Path.home() / "Documents") / "MemoPen"


def error_log_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    path = (Path(base) if base else Path.home() / ".MemoPen")
    path.mkdir(parents=True, exist_ok=True)
    return path / "MemoPen_error.log"


def embedded_app_icon() -> "QIcon":
    icon = QIcon()
    for encoded_png in APP_ICON_PNGS_BASE64:
        pixmap = QPixmap()
        if pixmap.loadFromData(base64.b64decode(encoded_png), "PNG"):
            icon.addPixmap(pixmap)
    return icon


def list_icon(direction: str) -> "QIcon":
    """繪製清單線條與分開的大箭頭，讓縮排方向容易辨認。"""
    image = QPixmap(72, 72)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(3, 3)
    painter.setPen(QPen(QColor("#35455a"), 1.5, Qt.SolidLine, Qt.RoundCap))
    for y in (3, 21):
        painter.drawPoint(3, y)
        painter.drawLine(7, y, 21, y)
    painter.setPen(QPen(QColor("#1466b3"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(4, 12, 20, 12)
    tip, base = (4, 10) if direction == "left" else (20, 14)
    head = QPainterPath()
    head.moveTo(base, 7)
    head.lineTo(tip, 12)
    head.lineTo(base, 17)
    painter.drawPath(head)
    painter.end()
    return QIcon(image)


def toolbar_icon(name: str) -> "QIcon":
    # 用固定座標畫線，避免依賴各平台的中文字型或符號字型。
    strokes = {
        "pin": (((7, 3), (17, 3), (15, 6), (15, 10), (18, 13), (18, 15),
                 (6, 15), (6, 13), (9, 10), (9, 6), (7, 3)), ((12, 15), (12, 22))),
        "numbering": (((3, 6), (5, 4), (5, 10)), ((3, 10), (7, 10)),
                      ((3, 15), (5, 13), (7, 14), (7, 16), (3, 20), (7, 20)),
                      ((11, 7), (21, 7)), ((11, 17), (21, 17))),
        "item": (((3, 6), (12, 6)), ((3, 12), (12, 12)), ((3, 18), (12, 18)),
                 ((15, 12), (22, 12)), ((18.5, 8.5), (18.5, 15.5))),
        "font": (((2, 18), (8, 5), (14, 18)), ((4, 14), (12, 14)),
                 ((16, 11), (19, 10), (21, 12), (21, 18)),
                 ((21, 13), (17, 14), (16, 16), (17, 18), (19, 18), (21, 16))),
        "undo": (((8, 5), (3, 10), (8, 15)), ((3, 10), (15, 10), (19, 12),
                  (21, 15), (21, 19))),
        "redo": (((16, 5), (21, 10), (16, 15)), ((21, 10), (9, 10), (5, 12),
                  (3, 15), (3, 19))),
        "new": (((5, 2), (15, 2), (20, 7), (20, 22), (5, 22), (5, 2)),
                ((15, 2), (15, 7), (20, 7)), ((9, 14), (16, 14)), ((12.5, 10.5), (12.5, 17.5))),
        "open": (((3, 18), (3, 5), (10, 5), (13, 8), (20, 8), (20, 11)),
                 ((3, 19), (7, 11), (22, 11), (19, 19), (3, 19))),
        "save": (((3, 3), (17, 3), (21, 7), (21, 21), (3, 21), (3, 3)),
                 ((7, 3), (7, 9), (16, 9), (16, 3)),
                 ((7, 21), (7, 13), (17, 13), (17, 21))),
        "settings": (((4, 6), (9, 6)), ((13, 6), (20, 6)), ((11, 3), (11, 9)),
                     ((4, 12), (14, 12)), ((18, 12), (20, 12)), ((16, 9), (16, 15)),
                     ((4, 18), (7, 18)), ((11, 18), (20, 18)), ((9, 15), (9, 21))),
        "minimize": (((6, 13), (18, 13)),),
        "close": (((6, 6), (18, 18)), ((6, 18), (18, 6))),
    }
    image = QPixmap(72, 72)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(3, 3)
    painter.setPen(QPen(QColor("#35455a"), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for points in strokes.get(name, ()):
        path = QPainterPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        painter.drawPath(path)
    if name == "more":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#35455a"))
        for x in (5, 11, 17):
            painter.drawEllipse(x, 11, 2, 2)
    elif name == "tray":
        triangle = QPainterPath()
        triangle.moveTo(5, 8)
        triangle.lineTo(19, 8)
        triangle.lineTo(12, 17)
        triangle.closeSubpath()
        painter.fillPath(triangle, QColor("#20252c"))
    painter.end()
    return QIcon(image)


if QApplication is not None:
    class ResizeGrip(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.setFixedSize(22, 22)
            self.setCursor(Qt.SizeFDiagCursor)
            self.setToolTip("拖曳調整視窗大小")
            self.setAccessibleName("調整視窗大小")

        def paintEvent(self, event):  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#35455a"), 1.8, Qt.SolidLine, Qt.RoundCap))
            right, bottom = self.width() - 3, self.height() - 3
            for length in (6, 11, 16):
                painter.drawLine(right - length, bottom, right, bottom - length)
            painter.end()


    class MemoEditor(QTextEdit):
        """Qt 原生文字編輯，保留中文 IME 組字控制。"""
        def __init__(self, parent=None):
            super().__init__(parent)
            self.composing = False

        def inputMethodEvent(self, event) -> None:  # noqa: N802
            self.composing = bool(event.preeditString())
            super().inputMethodEvent(event)

        def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
            if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                    and (event.modifiers() & ~Qt.KeypadModifier) == Qt.NoModifier
                    and not self.composing
                    and getattr(self.window(), "numbering_enabled", False)
                    and self.window().numbered_enter()):
                event.accept()
                return
            super().keyPressEvent(event)


    class DragBar(QFrame):
        def __init__(self, parent, window):
            super().__init__(parent)
            self.owner = window
            self.start = None

        def mousePressEvent(self, event):  # noqa: N802
            if event.button() == Qt.LeftButton and self.childAt(event.position().toPoint()) is None:
                wh = self.owner.windowHandle()
                if wh is not None and wh.startSystemMove():
                    event.accept()
                    return
                self.start = event.globalPosition().toPoint() - self.owner.pos()
                event.accept()
                return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):  # noqa: N802
            if self.start is not None and event.buttons() & Qt.LeftButton:
                self.owner.move(event.globalPosition().toPoint() - self.start)
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):  # noqa: N802
            self.start = None
            super().mouseReleaseEvent(event)


    class SaveSignals(QObject):
        """工作執行緒只發結果，UI 訊號在主執行緒處理。"""
        saved = Signal(int, str, object, str)


    class MemoPenWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"{APP_TITLE} {APP_VERSION}")
            self.setObjectName("MemoPenWindow")
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.settings = QSettings("MemoPen", "MemoPen")
            self.topmost_enabled = bool(self.settings.value("topmost", False, type=bool))
            window_flags = Qt.Window | Qt.FramelessWindowHint
            if self.topmost_enabled:
                window_flags |= Qt.WindowStaysOnTopHint
            self.setWindowFlags(window_flags)
            self.setAttribute(Qt.WA_InputMethodEnabled, True)
            self._border_width = max(1, round(WINDOW_BORDER_PT * self.logicalDpiX() / 72))
            self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
            self.resize(760, 560)
            self._resizing = False
            self._resize_edges = Qt.Edge(0)
            self._resize_start_global = QPoint()
            self._resize_start_geometry = QRect()
            self._closing = False
            self._tray_icon = None
            self._edit_revision = 0
            self._saved_revision = -1
            self._save_generation = 0
            self._known_sig = None
            self._newline_style = "\n"
            self._last_write_error = ""
            self._worker_signature: dict[str, tuple[int, int] | None] = {}
            self.numbering_enabled = bool(self.settings.value("numbering_enabled", False, type=bool))
            self.font_size = max(10, min(32, int(self.settings.value("font_size", 14))))
            folder = self.settings.value("default_folder", "")
            self.default_folder = Path(str(folder)) if folder else default_storage_folder()
            self.default_folder.mkdir(parents=True, exist_ok=True)
            self.current_file_path = self._unique_new_path()
            self._new_file_candidate = True
            self._new_file_candidates: set[Path] = {self.current_file_path}
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MemoPenSave")
            self._save_signals = SaveSignals(self)
            self._save_signals.saved.connect(self._on_save_result)
            self._idle_timer = QTimer(self)
            self._idle_timer.setSingleShot(True)
            self._idle_timer.setInterval(SAVE_IDLE_MS)
            self._idle_timer.timeout.connect(self._submit_autosave)
            self._build_ui()
            self.numbering_check.setChecked(self.numbering_enabled)
            self.topmost_check.blockSignals(True)
            self.topmost_check.setChecked(self.topmost_enabled)
            self.topmost_check.blockSignals(False)
            self._install_shortcuts()
            self._install_resize_filters()
            now = datetime.now()
            self._set_document(now.strftime("%Y%m%d_%H%M") + "\n\n", fresh=True)
            QApplication.instance().applicationStateChanged.connect(self._on_app_state)
            self.update_status("待儲存（停止輸入後自動儲存）")

        def _unique_new_path(self) -> Path:
            stamp = datetime.now().strftime("%Y%m%d%H%M")
            name = f"Memo_{stamp}"
            candidate = self.default_folder / (name + ".txt")
            i = 2
            while candidate.exists():
                candidate = self.default_folder / f"{name}_{i}.txt"
                i += 1
            return candidate

        def _build_ui(self):
            self.setStyleSheet("""
                QWidget { background:#ffffff; color:#20252c; }
                QPushButton,QToolButton { background:#ffffff; border:1px solid #c8cdd3;
                    border-radius:6px; padding:4px 6px; }
                QPushButton:hover,QToolButton:hover { background:#edf2f8; }
                QPushButton:pressed,QToolButton:pressed { background:#dae6f4; }
                QToolButton { padding:0; }
                QToolButton:checked { background:#dcecff; border-color:#4979ad; }
                QTextEdit { background:white; color:#20252c; border:1px solid #c5cbd4;
                    selection-background-color:#cde2ff; padding:10px; }
                QSlider { background:transparent; }
                QLabel { background:transparent; }
            """)
            root = QVBoxLayout(self)
            # 保留外框的繪製空間，避免子元件覆蓋。
            inset = self._border_width + 1
            root.setContentsMargins(inset, inset, inset, inset)
            root.setSpacing(0)

            # 所有操作按鈕統一為 30x30，放在頂部單一工具列。
            self.topbar = DragBar(self, self)
            self.topbar.setFixedHeight(43)
            self.topbar.setStyleSheet("border-bottom:1px solid #d9dde3;")
            heading = QHBoxLayout(self.topbar)
            heading.setContentsMargins(7, 5, 7, 5)
            heading.setSpacing(4)
            self.title_label = QLabel("MemoPen")
            self.title_label.setFont(ui_font(11, True))
            self.title_label.setFixedWidth(77)
            self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.title_label.setToolTip("MemoPen 隨手記")
            heading.addWidget(self.title_label)

            self.topmost_check = self._tool_button(
                "視窗保持在其他視窗最上方", toolbar_icon("pin"),
                self._toggle_topmost, checkable=True)
            heading.addWidget(self.topmost_check)
            self.numbering_check = self._tool_button(
                "編碼：按 Enter 接續編號", toolbar_icon("numbering"),
                self._toggle_numbering, checkable=True)
            heading.addWidget(self.numbering_check)

            self.item_button = self._tool_button("新增編碼項目", toolbar_icon("item"), self.insert_next_number)
            self.up_button = self._tool_button("升階", list_icon("left"), self.promote)
            self.down_button = self._tool_button("降階", list_icon("right"), self.demote)
            for button in (self.up_button, self.down_button):
                button.setIconSize(QSize(25, 25))
            self.font_button = self._tool_button("文字大小", toolbar_icon("font"), self.toggle_font_drawer)
            for button in (self.item_button, self.up_button, self.down_button, self.font_button):
                heading.addWidget(button)

            # 編輯器稍後建立；復原按鈕在下方完成連結，再加入同一列。
            self.editor_holder = QWidget(self)
            editor_layout = QVBoxLayout(self.editor_holder)
            editor_layout.setContentsMargins(10, 8, 10, 6)
            self.editor = MemoEditor(self.editor_holder)
            self.editor.setAcceptRichText(False)
            self.editor.setLineWrapMode(QTextEdit.WidgetWidth)
            self.editor.setTabChangesFocus(False)
            self.editor.setFont(ui_font(self.font_size))
            self.editor.document().setDefaultFont(ui_font(self.font_size))
            self.editor.textChanged.connect(self._mark_dirty)
            editor_layout.addWidget(self.editor)

            self.undo_button = self._tool_button("復原 (Ctrl+Z)", toolbar_icon("undo"), self.editor.undo)
            self.redo_button = self._tool_button("重做 (Ctrl+Y)", toolbar_icon("redo"), self.editor.redo)
            heading.addWidget(self.undo_button)
            heading.addWidget(self.redo_button)
            heading.addStretch(1)
            self.new_button = self._tool_button("新增筆記 (Ctrl+N)", toolbar_icon("new"), self.new_note)
            self.open_button = self._tool_button("開啟 TXT (Ctrl+O)", toolbar_icon("open"), self.open_file)
            self.save_button = self._tool_button("存檔 (Ctrl+S)", toolbar_icon("save"), self.save_now)
            self.settings_button = self._tool_button("儲存位置設定", toolbar_icon("settings"), self.show_settings)
            self.more_button = self._tool_button("更多操作", toolbar_icon("more"), self._show_more)
            self.minimize_button = self._tool_button("最小化", toolbar_icon("minimize"), self.showMinimized)
            self.tray_button = self._tool_button("縮到系統匣", toolbar_icon("tray"), self.minimize_to_tray)
            self.close_button = self._tool_button("關閉 MemoPen", toolbar_icon("close"), self.close)
            for b in (self.new_button, self.open_button, self.save_button,
                      self.settings_button, self.more_button, self.minimize_button,
                      self.tray_button, self.close_button):
                heading.addWidget(b)
            root.addWidget(self.topbar)
            root.addWidget(self.editor_holder, 1)

            # 狀態列僅保留左側狀態、右側檔名和視窗縮放角標。
            self.statusbar = DragBar(self, self)
            self.statusbar.setFixedHeight(27)
            self.statusbar.setStyleSheet("border-top:1px solid #d9dde3;")
            status_layout = QHBoxLayout(self.statusbar)
            status_layout.setContentsMargins(8, 2, 2, 2)
            status_layout.setSpacing(6)
            self.status_label = QLabel()
            self.status_label.setFont(ui_font(9))
            self.status_label.setStyleSheet("color:#6d7580;")
            self.status_label.setMinimumWidth(0)
            status_layout.addWidget(self.status_label, 1)
            self.document_label = QLabel()
            self.document_label.setFont(ui_font(9))
            self.document_label.setStyleSheet("color:#556476;")
            self.document_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.document_label.setMinimumWidth(0)
            self.document_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            status_layout.addWidget(self.document_label, 0)
            self.size_grip = ResizeGrip(self.statusbar)
            status_layout.addWidget(self.size_grip, 0, Qt.AlignBottom)
            root.addWidget(self.statusbar)

            # 文字大小面板由工具列開啟。
            self.font_drawer = QFrame(self.editor_holder)
            self.font_drawer.setObjectName("FontDrawer")
            self.font_drawer.setStyleSheet("QFrame#FontDrawer { background:#f6f9fc; border:1px solid #adb9c9; border-radius:8px; }")
            drawer_layout = QVBoxLayout(self.font_drawer)
            drawer_layout.setContentsMargins(10, 9, 10, 9)
            drawer_label = QLabel("文字大小")
            drawer_label.setFont(ui_font(10, True))
            drawer_layout.addWidget(drawer_label)
            font_row = QHBoxLayout()
            minus = self._button("−", lambda: self.set_font_size(self.font_size - 1), width=30)
            plus = self._button("+", lambda: self.set_font_size(self.font_size + 1), width=30)
            self.font_slider = QSlider(Qt.Horizontal)
            self.font_slider.setRange(10, 32)
            self.font_slider.setValue(self.font_size)
            self.font_slider.valueChanged.connect(self.set_font_size)
            self.font_value = QLabel(f"{self.font_size}pt")
            self.font_value.setMinimumWidth(43)
            font_row.addWidget(minus)
            font_row.addWidget(self.font_slider, 1)
            font_row.addWidget(plus)
            font_row.addWidget(self.font_value)
            drawer_layout.addLayout(font_row)
            reset_button = self._button("恢復 14 pt", lambda: self.set_font_size(14))
            drawer_layout.addWidget(reset_button)
            for w in (minus, plus, reset_button):
                w.setFocusPolicy(Qt.NoFocus)
            self.font_slider.setFocusPolicy(Qt.NoFocus)
            self.font_drawer.hide()
            self._adapt_width()
            root.activate()
            heading.activate()
            self._position_font_drawer()
            # 在視窗開啟前完成隱藏面板的首次版面配置與繪製，避免第一次點擊才載入字型。
            self.font_drawer.grab()

        def _button(self, text, func, width=None):
            b = QPushButton(text)
            b.setFont(ui_font(10))
            b.setFixedHeight(33)
            if width is not None:
                b.setFixedWidth(width)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(func)
            return b

        def _tool_button(self, title, icon, callback, checkable=False):
            button = QToolButton()
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setIcon(icon)
            button.setIconSize(QSize(21, 21))
            button.setFixedSize(TOOL_BUTTON_WIDTH, TOOL_BUTTON_HEIGHT)
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.setCheckable(checkable)
            if checkable:
                button.toggled.connect(callback)
            else:
                button.clicked.connect(callback)
            return button

        def _install_shortcuts(self):
            for shortcut, action in ((QKeySequence.Save, self.save_now),
                                     (QKeySequence.Open, self.open_file),
                                     (QKeySequence.New, self.new_note),
                                     ("Ctrl+Shift+S", self.choose_save_folder),
                                     ("Meta+Shift+S", self.choose_save_folder),
                                     ("Ctrl+Alt+Left", self.promote),
                                     ("Ctrl+Alt+Right", self.demote)):
                QShortcut(QKeySequence(shortcut), self, activated=action)

        def _install_resize_filters(self):
            for w in (self, self.topbar, self.statusbar,
                      self.editor, self.editor.viewport(), self.editor_holder, self.size_grip):
                w.setMouseTracking(True)
                w.installEventFilter(self)
            self.font_button.installEventFilter(self)

        def _toggle_topmost(self, checked):
            self.topmost_enabled = checked
            self.settings.setValue("topmost", checked)
            geometry = self.geometry()
            cursor = self.editor.textCursor()
            scroll = self.editor.verticalScrollBar().value()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
            self.setGeometry(geometry)
            self.show()
            self.editor.setTextCursor(cursor)
            self.editor.verticalScrollBar().setValue(scroll)
            self.editor.setFocus(Qt.OtherFocusReason)

        def minimize_to_tray(self):
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self.showMinimized()
                self.update_status("系統匣無法使用，已最小化")
                return
            if self._tray_icon is None:
                tray = QSystemTrayIcon(self.windowIcon(), self)
                tray.setToolTip(self.windowTitle())
                menu = QMenu(self)
                menu.addAction("還原視窗", self._restore_from_tray)
                menu.addAction("結束 MemoPen", self._exit_from_tray)
                tray.setContextMenu(menu)
                tray.activated.connect(self._on_tray_activated)
                self._tray_icon = tray
            self._tray_icon.show()
            self.hide()

        def _on_tray_activated(self, reason):
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                self._restore_from_tray()

        def _restore_from_tray(self):
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
            self.show()
            self.raise_()
            self.activateWindow()
            if self._tray_icon is not None:
                self._tray_icon.hide()

        def _exit_from_tray(self):
            if self.close():
                QApplication.instance().quit()
            else:
                self._restore_from_tray()

        def _toggle_numbering(self, checked):
            self.numbering_enabled = checked
            self.settings.setValue("numbering_enabled", checked)
            self.update_status("編碼開啟" if checked else "編碼關閉")
            self.editor.setFocus(Qt.OtherFocusReason)

        def _position_font_drawer(self):
            if not hasattr(self, "font_drawer"):
                return
            width = self.editor_holder.width()
            drawer_width = min(310, width - 20)
            anchor = self.font_button.mapTo(self, QPoint(0, 0)).x()
            left = max(10, min(anchor - self.editor_holder.x(), width - drawer_width - 10))
            self.font_drawer.setGeometry(left, 8, drawer_width, 124)

        def toggle_font_drawer(self):
            self.font_drawer.setVisible(self.font_drawer.isHidden())

        def set_font_size(self, value):
            value = max(10, min(32, int(value)))
            if self.font_size == value:
                return
            self.font_size = value
            # 不選取文字、不重新指定 setPlainText、不改變復原記錄。
            old_cursor = self.editor.textCursor()
            old_scroll = self.editor.verticalScrollBar().value()
            self.editor.setFont(ui_font(value))
            self.editor.document().setDefaultFont(ui_font(value))
            self.editor.setTextCursor(old_cursor)
            self.editor.verticalScrollBar().setValue(old_scroll)
            self.font_slider.blockSignals(True)
            self.font_slider.setValue(value)
            self.font_slider.blockSignals(False)
            self.font_value.setText(f"{value}pt")
            self.settings.setValue("font_size", value)
            self.editor.setFocus(Qt.OtherFocusReason)

        def _adapt_width(self):
            if not hasattr(self, "new_button"):
                return
            width = self.width()
            # 窄視窗保留所有編碼按鈕及字體大小，其餘操作可從「⋯」取用。
            heading = self.topbar.layout()
            if width < 365:
                heading.setContentsMargins(5, 5, 5, 5)
                heading.setSpacing(2)
            else:
                heading.setContentsMargins(7, 5, 7, 5)
                heading.setSpacing(4)
            self.title_label.setVisible(width >= 450)
            self.undo_button.setVisible(width >= 550)
            self.redo_button.setVisible(width >= 550)
            self.new_button.setVisible(width >= 665)
            self.open_button.setVisible(width >= 580)
            self.save_button.setVisible(width >= 485)
            self.settings_button.setVisible(width >= 720)

        def paintEvent(self, event):  # noqa: N802
            super().paintEvent(event)
            outer = self.rect()
            width = self._border_width
            border = QPainterPath()
            border.addRect(outer)
            border.addRect(outer.adjusted(width, width, -width, -width))
            painter = QPainter(self)
            painter.fillPath(border, Qt.black)
            painter.end()

        def resizeEvent(self, event):  # noqa: N802
            super().resizeEvent(event)
            self._adapt_width()
            self._position_font_drawer()
            self._update_document_label()
            self._render_status_label()

        def eventFilter(self, watched, event):  # noqa: N802
            typ = event.type()
            if watched is self.font_button:
                if typ == QEvent.Move:
                    self._position_font_drawer()
                return super().eventFilter(watched, event)
            if watched is self.editor_holder and typ == QEvent.Resize:
                self._position_font_drawer()
            if typ == QEvent.MouseButtonPress:
                if self._start_resize(watched, event):
                    return True
            elif typ == QEvent.MouseMove:
                if self._move_resize(watched, event):
                    return True
            elif typ == QEvent.MouseButtonRelease and self._resizing:
                self._resizing = False
                self._resize_edges = Qt.Edge(0)
                return True
            return super().eventFilter(watched, event)

        def _edges_at(self, p):
            edges = Qt.Edge(0)
            if p.x() <= RESIZE_MARGIN:
                edges |= Qt.LeftEdge
            elif p.x() >= self.width() - RESIZE_MARGIN:
                edges |= Qt.RightEdge
            if p.y() <= RESIZE_MARGIN:
                edges |= Qt.TopEdge
            elif p.y() >= self.height() - RESIZE_MARGIN:
                edges |= Qt.BottomEdge
            return edges

        def _start_resize(self, watched, event):
            if not hasattr(event, "button") or event.button() != Qt.LeftButton:
                return False
            if watched is self.size_grip:
                edges = Qt.RightEdge | Qt.BottomEdge
            else:
                p = watched.mapTo(self, event.position().toPoint())
                edges = self._edges_at(p)
            if not edges:
                return False
            handle = self.windowHandle()
            if handle is not None and handle.startSystemResize(edges):
                return True
            self._resizing = True
            self._resize_edges = edges
            self._resize_start_global = event.globalPosition().toPoint()
            self._resize_start_geometry = QRect(self.geometry())
            return True

        def _move_resize(self, watched, event):
            if self._resizing:
                offset = event.globalPosition().toPoint() - self._resize_start_global
                rect = QRect(self._resize_start_geometry)
                if self._resize_edges & Qt.LeftEdge:
                    rect.setLeft(min(rect.right() - MIN_WIDTH + 1, rect.left() + offset.x()))
                if self._resize_edges & Qt.RightEdge:
                    rect.setRight(max(rect.left() + MIN_WIDTH - 1, rect.right() + offset.x()))
                if self._resize_edges & Qt.TopEdge:
                    rect.setTop(min(rect.bottom() - MIN_HEIGHT + 1, rect.top() + offset.y()))
                if self._resize_edges & Qt.BottomEdge:
                    rect.setBottom(max(rect.top() + MIN_HEIGHT - 1, rect.bottom() + offset.y()))
                self.setGeometry(rect)
                return True
            return False

        def _show_more(self):
            menu = QMenu(self)
            menu.addAction("新增", self.new_note)
            menu.addAction("開啟", self.open_file)
            menu.addAction("存檔", self.save_now)
            menu.addAction("項目", self.insert_next_number)
            menu.addAction("升階", self.promote)
            menu.addAction("降階", self.demote)
            menu.addAction("設定預設儲存位置", self.show_settings)
            menu.addAction("選擇本篇另存資料夾", self.choose_save_folder)
            menu.addAction("轉換舊版 1、2、 編碼", self.convert_legacy)
            menu.addSeparator()
            menu.addAction("復原", self.editor.undo)
            menu.addAction("重做", self.editor.redo)
            menu.exec(self.more_button.mapToGlobal(QPoint(0, self.more_button.height())))

        def _set_document(self, text, fresh=False):
            self._idle_timer.stop()
            self.editor.blockSignals(True)
            self.editor.setPlainText(text)
            self.editor.blockSignals(False)
            tc = self.editor.textCursor()
            tc.movePosition(QTextCursor.End)
            self.editor.setTextCursor(tc)
            self._edit_revision += 1
            self._saved_revision = self._edit_revision - (1 if fresh else 0)
            self._newline_style = "\n"
            self._update_document_label()
            self.editor.setFocus(Qt.OtherFocusReason)
            if fresh:
                self._idle_timer.start()

        def _update_document_label(self):
            # 將檔名固定顯示於底部狀態列右側，太長時從中間省略。
            name = self.current_file_path.name
            usable = max(65, min(280, int(self.statusbar.width() * 0.42)))
            shown = self.document_label.fontMetrics().elidedText(name, Qt.ElideMiddle, usable)
            self.document_label.setText(shown)
            self.document_label.setToolTip(str(self.current_file_path))

        def _render_status_label(self):
            text = getattr(self, "_status_full", "")
            available = max(75, int(self.statusbar.width() * 0.56) - 20)
            self.status_label.setText(
                self.status_label.fontMetrics().elidedText(text, Qt.ElideRight, available)
            )

        def _mark_dirty(self):
            self._edit_revision += 1
            self._idle_timer.start()
            self.update_status("尚未儲存")

        def update_status(self, text):
            self._status_full = f"{APP_VERSION}  ·  {text}"
            self._render_status_label()
            self.status_label.setToolTip(text)

        def _on_app_state(self, state):
            if state != Qt.ApplicationActive:
                self._idle_timer.stop()
                self._submit_autosave()

        def _submit_autosave(self):
            self._request_save(force=False)

        def _request_save(self, force=False):
            if not force and self._edit_revision == self._saved_revision:
                return
            # 只複製內容快照，不觸及 caret/selection/focus/viewport。
            snapshot = self.editor.toPlainText()
            if self._newline_style == "\r\n":
                snapshot = snapshot.replace("\n", "\r\n")
            revision = self._edit_revision
            path = self.current_file_path
            expected = self._known_sig
            self._save_generation += 1
            generation = self._save_generation
            self.update_status("儲存中…")

            # 所有寫入依序排入單一工作執行緒；後續寫入檢查上次成功的磁碟版本。
            def work():
                try:
                    # 由先前已執行的工作更新同一個路徑的簽章。
                    known = self._worker_signature.get(str(path), expected)
                    sig = atomic_write(path, snapshot, known)
                    self._worker_signature[str(path)] = sig
                    self._save_signals.saved.emit(generation, str(path), (revision, sig), "")
                except Exception as exc:
                    self._save_signals.saved.emit(generation, str(path), None, str(exc))

            self._executor.submit(work)

        def _on_save_result(self, generation, name, result, error):
            if name != str(self.current_file_path):
                return
            if error:
                self._last_write_error = error
                self.update_status(f"儲存失敗：{error}")
                return
            revision, signature = result
            self._known_sig = signature
            self._last_write_error = ""
            if revision == self._edit_revision:
                self._saved_revision = revision
                self.update_status(f"{datetime.now():%H:%M:%S} 已儲存")
            elif self._edit_revision > revision:
                self.update_status("已有更新內容，等待自動儲存")

        def _flush_current(self, report=True):
            """切換或關閉前先等背景工作結束，再儲存最新快照。"""
            self._idle_timer.stop()
            # barrier：先前排隊中的寫入全部完成，避免舊資料晚於同步寫入落盤。
            self._executor.submit(lambda: None).result()
            if self._edit_revision == self._saved_revision and self.current_file_path.exists():
                return True
            try:
                expected = self._worker_signature.get(str(self.current_file_path), self._known_sig) \
                           if hasattr(self, "_worker_signature") else self._known_sig
                content = self.editor.toPlainText()
                if self._newline_style == "\r\n":
                    content = content.replace("\n", "\r\n")
                sig = atomic_write(self.current_file_path, content, expected)
                self._known_sig = sig
                self._saved_revision = self._edit_revision
                self._last_write_error = ""
                self._worker_signature[str(self.current_file_path)] = sig
                self.update_status("已儲存")
                return True
            except Exception as exc:
                self.update_status(f"儲存失敗：{exc}")
                if report:
                    QMessageBox.warning(self, APP_TITLE, f"無法儲存，為防止資料遺失，已取消離開：\n{exc}")
                return False

        def save_now(self):
            self._flush_current(report=True)

        def new_note(self):
            if not self._flush_current():
                return
            self.current_file_path = self._unique_new_path()
            self._known_sig = None
            self._new_file_candidate = True
            self._new_file_candidates.add(self.current_file_path)
            self._set_document(datetime.now().strftime("%Y%m%d_%H%M") + "\n\n", fresh=True)
            self.update_status("已新增筆記")

        def open_file(self):
            selected, _ = QFileDialog.getOpenFileName(
                self, "開啟 TXT 筆記", str(self.current_file_path.parent),
                "文字檔案 (*.txt);;所有檔案 (*)")
            if not selected:
                return
            path = Path(selected)
            try:
                content = readable_text(path)
            except OSError as exc:
                QMessageBox.critical(self, APP_TITLE, str(exc))
                return
            editor_was_blank = not self.editor.toPlainText().strip()
            candidates_to_remove = set(self._new_file_candidates) if editor_was_blank else set()
            if not self._flush_current():
                return

            cleanup_errors = []
            for candidate in candidates_to_remove:
                if candidate == path:
                    continue
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as exc:
                    cleanup_errors.append(f"{candidate}（{exc}）")
            self.current_file_path = path
            self._known_sig = disk_signature(path)
            self._worker_signature[str(path)] = self._known_sig
            self._new_file_candidate = False
            self._new_file_candidates.clear()
            self._set_document(content.replace("\r\n", "\n"), fresh=False)
            self._newline_style = "\r\n" if "\r\n" in content else "\n"
            self.update_status("已開啟檔案")
            if cleanup_errors:
                QMessageBox.warning(
                    self,
                    APP_TITLE,
                    "舊檔已開啟，但無法刪除下列空白新筆記：\n" + "\n".join(cleanup_errors),
                )

        def choose_save_folder(self):
            folder = QFileDialog.getExistingDirectory(self, "目前筆記另存位置", str(self.current_file_path.parent))
            if not folder:
                return
            # 另存新檔不必先覆寫原檔，因此原檔遭其他程式修改時也能救回內容。
            target = Path(folder) / self.current_file_path.name
            if target == self.current_file_path:
                self.save_now()
                return
            if target.exists():
                QMessageBox.warning(self, APP_TITLE, "目標檔案已存在，為避免覆寫請選擇其他資料夾。")
                return
            self._idle_timer.stop()
            self._executor.submit(lambda: None).result()
            try:
                text = self.editor.toPlainText()
                if self._newline_style == "\r\n":
                    text = text.replace("\n", "\r\n")
                sig = atomic_write(target, text, None)
            except Exception as exc:
                QMessageBox.warning(self, APP_TITLE, f"另存失敗：\n{exc}")
                self._idle_timer.start()
                return
            self.current_file_path = target
            self._known_sig = sig
            self._worker_signature[str(target)] = sig
            if self._new_file_candidate:
                self._new_file_candidates.add(target)
            self._saved_revision = self._edit_revision
            self._update_document_label()
            self.update_status(f"已另存：{target}")

        def show_settings(self):
            question = QMessageBox(self)
            question.setWindowTitle("MemoPen 儲存位置設定")
            question.setText("新筆記預設儲存位置：\n" + str(self.default_folder))
            question.setInformativeText("變更只影響之後新增的筆記，不會搬移或覆蓋現有 TXT。")
            change = question.addButton("選擇資料夾…", QMessageBox.AcceptRole)
            reset = question.addButton("恢復預設位置", QMessageBox.ActionRole)
            question.addButton("取消", QMessageBox.RejectRole)
            question.exec()
            clicked = question.clickedButton()
            if clicked is change:
                folder = QFileDialog.getExistingDirectory(self, "新筆記預設資料夾", str(self.default_folder))
                if folder:
                    self.default_folder = Path(folder)
                    self.settings.setValue("default_folder", str(self.default_folder))
                    self.update_status(f"新筆記預設位置：{self.default_folder}")
            elif clicked is reset:
                self.default_folder = default_storage_folder()
                self.default_folder.mkdir(parents=True, exist_ok=True)
                self.settings.remove("default_folder")
                self.update_status("新筆記已恢復預設資料夾")
            self.editor.setFocus(Qt.OtherFocusReason)

        def _cursor_range(self):
            tc = self.editor.textCursor()
            first = self.editor.document().findBlock(tc.selectionStart()).blockNumber()
            last_pos = tc.selectionEnd()
            if tc.hasSelection() and last_pos > tc.selectionStart():
                # 選取恰好結束在下一段首時，不將下一段誤視為被選取。
                block = self.editor.document().findBlock(last_pos)
                if block.isValid() and last_pos == block.position():
                    last_pos -= 1
            last = self.editor.document().findBlock(last_pos).blockNumber()
            return first, last

        def _document_lines(self):
            # Shift+Enter 的軟換行留在同一個 QTextBlock。
            lines = []
            block = self.editor.document().begin()
            while block.isValid():
                lines.append(block.text())
                block = block.next()
            return lines

        def _apply_updated_lines(self, updated):
            """只修改真正變動行的 prefix，整批視為一次復原操作。"""
            before = self._document_lines()
            if len(before) != len(updated):
                raise ValueError("內部錯誤：文字段落數量不符")
            edits = []
            doc = self.editor.document()
            for i, (old, new) in enumerate(zip(before, updated)):
                if old == new:
                    continue
                a, b = parse_item(old), parse_item(new)
                if a is not None and b is not None and a.body == b.body:
                    old_prefix, new_prefix = a.prefix, b.prefix
                else:
                    old_prefix, new_prefix = old, new
                block = doc.findBlockByNumber(i)
                if not block.isValid():
                    continue
                edits.append((block.position(), len(old_prefix.encode("utf-16-le")) // 2, new_prefix))
            if not edits:
                return
            current = self.editor.textCursor()
            pos, anchor = current.position(), current.anchor()

            def mapped(index):
                shift = 0
                for start, length, replacement in edits:
                    end = start + length
                    if index < start:
                        break
                    if index <= end:
                        new_length = len(replacement.encode("utf-16-le")) // 2
                        return start + shift + min(index - start, new_length)
                    shift += (len(replacement.encode("utf-16-le")) // 2) - length
                return index + shift

            new_pos, new_anchor = mapped(pos), mapped(anchor)
            writer = QTextCursor(doc)
            writer.beginEditBlock()
            for start, length, replacement in reversed(edits):
                writer.setPosition(start)
                writer.setPosition(start + length, QTextCursor.KeepAnchor)
                writer.insertText(replacement)
            writer.endEditBlock()
            restored = self.editor.textCursor()
            restored.setPosition(new_anchor)
            restored.setPosition(new_pos, QTextCursor.KeepAnchor)
            self.editor.setTextCursor(restored)

        def _run_hierarchy(self, delta):
            if self.editor.composing:
                self.update_status("請先完成中文輸入法選字")
                return
            first, last = self._cursor_range()
            try:
                updated = change_hierarchy(self._document_lines(), first, last, delta)
                self._apply_updated_lines(updated)
            except NumberingError as exc:
                self.update_status(str(exc))
                return
            self.editor.setFocus(Qt.OtherFocusReason)

        def promote(self):
            self._run_hierarchy(-1)

        def demote(self):
            self._run_hierarchy(1)

        def _apply_new_item(self, updated, index, inserted=True):
            doc = self.editor.document()
            writer = QTextCursor(doc)
            writer.beginEditBlock()
            if inserted:
                if index == doc.blockCount():
                    writer.movePosition(QTextCursor.End)
                    writer.insertBlock()
                else:
                    writer.setPosition(doc.findBlockByNumber(index).position())
                    writer.insertBlock()
                    writer.movePosition(QTextCursor.PreviousBlock)
                writer.insertText(updated[index])
            self._apply_updated_lines(updated)
            writer.endEditBlock()

            target = doc.findBlockByNumber(index)
            item = parse_item(target.text())
            cursor = self.editor.textCursor()
            cursor.setPosition(target.position() + len(item.prefix))
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()

        def insert_next_number(self):
            if self.editor.composing:
                return
            lines = self._document_lines()
            try:
                updated, index, inserted = prepare_new_item(lines, self.editor.textCursor().blockNumber())
            except NumberingError as exc:
                self.update_status(str(exc))
                return
            self._apply_new_item(updated, index, inserted)
            self.editor.setFocus(Qt.OtherFocusReason)

        def numbered_enter(self):
            cursor = self.editor.textCursor()
            if cursor.hasSelection():
                return False
            index = cursor.blockNumber()
            lines = self._document_lines()
            item = parse_item(lines[index])
            if item is None:
                return False
            legacy = not has_parent(lines, index, item.level)
            if legacy and (item.level != 2 or item.indent):
                return False
            # QTextCursor 的位置採 UTF-16，直接用它取得游標後的文字。
            cursor.setPosition(max(cursor.position(), cursor.block().position() + len(item.prefix)))
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            split_at = len(lines[index]) - len(cursor.selectedText())
            try:
                updated, target = continue_numbering(lines, index, split_at, item, legacy)
            except NumberingError as exc:
                self.update_status(str(exc))
                return True
            self._apply_new_item(updated, target)
            return True

        def convert_legacy(self):
            try:
                result = convert_legacy_numbering(self._document_lines())
            except NumberingError as exc:
                self.update_status(str(exc))
                return
            answer = QMessageBox.question(self, APP_TITLE, "確定將舊式 1、2、3、清單轉為「一、二、三、」嗎？")
            if answer == QMessageBox.Yes:
                self._apply_updated_lines(result)

        def closeEvent(self, event):  # noqa: N802
            if self._closing:
                event.accept()
                return
            self._closing = True
            if self._flush_current(report=True):
                self._executor.shutdown(wait=True, cancel_futures=False)
                if self._tray_icon is not None:
                    self._tray_icon.hide()
                event.accept()
            else:
                self._closing = False
                event.ignore()


def install_exception_hook():
    def handle(exc_type, exc_value, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc_value, tb))
        try:
            with error_log_path().open("a", encoding="utf-8") as logfile:
                logfile.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {APP_VERSION}\n{detail}")
        except Exception:
            pass
        if QApplication is not None and QApplication.instance() is not None:
            QMessageBox.critical(None, APP_TITLE, f"程式發生錯誤：\n{exc_value}")
        else:
            print(detail, file=sys.stderr)
    sys.excepthook = handle


def main():
    if QApplication is None:
        show_dependency_error("尚未安裝 PySide6")
        return 1
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(f"{APP_TITLE} {APP_VERSION}")
    app.setWindowIcon(embedded_app_icon())
    app.setFont(ui_font(10))
    install_exception_hook()
    window = MemoPenWindow()
    window.show()
    window.editor.setFocus(Qt.OtherFocusReason)
    return app.exec()


# notes_Qicon.ico 內的原始 PNG 圖示，保留 16～256 px 的全部尺寸。
APP_ICON_PNGS_BASE64: Final = (
    (  # 16 × 16
        b"iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAADfklEQVR4nF1TXYhUZRh+vu+cM2fOnPk56+wsbo40W2qRhBleRbGC"
        b"Ul6ZS2hGXaQta3VRrnSRKYxLELWFRAvCYkFCFLSSoWwrClYYi8KWazujuSzuj477Mzs/Oz/nnJk53/fGsHnTAy+8V+/7Pj8vA4Ad"
        b"R8a3av5APwNU5lWO/dL/7GiSiP92eDxsmEY/V3xbSbiDI5889TWSSY6+Pon/wLuSk3HVb4zUPYrUPU9zuDmyqftaRx9jUje0IUFK"
        b"l+PY9z3mP93Zm34jeQLoTP6qPhyAziMT3buO3hZEFCEive3dy/VNvZcOZ8ao9aXj09R7amp/2A+seefC7/rbP/6B/4G9/9X0nr/v"
        b"uWdfez6a3/9CjC+UnIipa66u8lr3wGRLvNVXOrZ3PbnS9Q3bV5W0/GvY54t3f5l4c4U1KXx+MHHgrRfjyuidcqwuEX3EMtSgrgbD"
        b"AR7t3b2OzxeElSmgJdESMvdaO31Pxh97JewsfIB9QzyJEwryy5Sp20T5LAm7RHKlQJRdFHJ5iaRwiSpFkrksyWxWkpujxsGFHzxr"
        b"4uhPzfMHxwY1LqUMVF2Aa2DfD19kqckpWGHOFIXY6aFh3JqeY7ofTIXAYgUK/Unsm2H+HL338YFD2w41WH5J1Jif+7QbN7E4dg20"
        b"62Vcvz2BRGID2h7MwcrMIbCnC/NOEB7dwROpM4TzAYaoBtTcD1k+S5JUMGNxAYZdRm3DRuRKVRimgVCxDDY/h9nWp+FV03g0NgCy"
        b"BOiKRcaFiECIq6zJT+FgJRfIlQQUUYfKFZCUiK31w5ZAbeku4tWPwNtNQA9AGHVhXo8pOKedbwaiAQafSgImb4CrCqjpLxikJ+Fk"
        b"Sojc/QJsnQPUTHi6lEHNUGrb8pf1nfarnABbUYCGJ6liC1QdgXKlgWCAgzhH8f4/qKdnUEwH0cjbMsg9NlOiW4XZchdjZ1zOgTIA"
        b"0jSOcEhDyFQRCWkA41A94Nx8BwbsfXAeVMFuluRQzkebp6xU+zPfVXvGejSVgEzQxPrlnGwUV+pcURg8j5im6fD7FGyJG/RtZgfG"
        b"LZVtXhtixwMFvsWdTY0S2Ha0E8tmabuh46wZQLQZDqLVEgKQ1HQKqNir/SWZEldrP1882dr2+pqWntKqVgCW7tHGoCV21xrK4yTR"
        b"TkCIAZxxOIwjqzHMLFM2farw6dRnHSdvPPyj5r5/AZujsti0PHdAAAAAAElFTkSuQmCC"
    ),
    (  # 24 × 24
        b"iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAGrUlEQVR4nG1WfWxVZxn/ve97zrnn3N62t1+0Xdt1hc0xMcCmyKZz"
        b"gBPGRlzQpDUyJeqiiQRcsjAYIF4uzuEAZ1ycC7qgRlttu2SY4RazsqKZcwirayj9pHwVaOlt773t/b7nvO9jzrm0Zsznj3Py5jwf"
        b"7/t7fu/vOQwgBjBataU7YJY1PME5v5uUvGjHo20nXl45DSIGxmjuvXZn3xeF0B8hQElp/7XrhU+9i1CIIxxW+D/G3MD1O0fqSMjj"
        b"hr98GeMaiCTsdGxEI3r8+IHFg6tC3drfw2uctc98+Jxulu7RzBIv2M7Eyckn9rx9cPmBUIh4OMw+VoS7u1LIvWiV1C2zk5OnVHri"
        b"GTs50W0WV90lyf51Xx8ZJ/etxqO7+tcagao9yslmZGpiv0pOHCCyHcMqff6RvR880L+khTU3d4iPHWHjs/0V63b2zazfPRgH8D+H"
        b"5mNX8LVjEniy3F1+7qneQ189lJIrt/ZsmffZ0L4b3+1R2PD2Lnf5yY4O49b82nBEUXWZyhtMGaMX8zvrqnQtmYL9s64BkbJBX//R"
        b"z/euaHp1csfvLq3quRzhG1ZUPfi+QxU3JiV7Y+ji0taLI0qVjK6+/8WXThy8u+XfRMSY27M5IyL92wfP9z+4fYR+3Ban6AwVLENE"
        b"aSKZLyzf7c3Tii09tOmFMXrv3E2fPFEikVC/ufEaPT12mLYPHt7r5gxRiM/3wM5g2/7NTfc0BKU8+taw8+cTNxw7BWciLimSJMRn"
        b"4ExHlLO8UVdPrKnF2dGI/ElrvzMVUc5UXCnKBtg6tV6JrKZ4kb7/2cFDK8IsrOaKaDMJtbGuguOnmxey0+eT4tN3FiOZldAFB+fM"
        b"82GMI+8QvvPwAtxT7xd+n4BP57AlkAehzCjijaLaHhMTzHHYw9Tc3LPvJNxgxYXgZekMUBrQ8fhny1BZrKEkIGCZLoNxE0ZAcAa/"
        b"n2Pt8hLct7AIjnQ57n2FjxGu5AR7KZ1ih5EKsM5O2R+BdwIOQtBN4DiKJXJAPGvjSPtfcG74IiwTYAzwGUB8NoFX2l7Hf0ZvwGFe"
        b"7woXiQGZWeC9lMGXDaao9W/ao/a20PrOlnCemjsEJyCglLsPoEhXGDv1T0yNDGHwwghS6Qxmk0lkcykMX76A6bFLGP1HF3x2HooR"
        b"OCPMxIDLGYbwpRz/1+vApiu4TzP9b9HW0A9YZ4tk0xHKCgaf1AFrYBDWB+8DpUHILz2GU1ev4o/tbfjC/Z/HV9atgfnOO8C1K7Ab"
        b"mpB6aBViEYmZpEB12THU2m+CWmshrwul+YnADYFc7im3gGQM3EVMxKKwzn4Ix/Qjs/RecNOHVDIDy7IATjCHBmBcvwr7rk9gItiI"
        b"mShDjdmOioo3kdeLIBIcvt+Wg13TCEUu7pAsOnUTTPepA4buKRQgXQ4AQgAuhFIBNgEusWYSQDQKVNutqPB1gmpuh9d1Q4GlOLSj"
        b"5STGfYDPSc+fwG1WLisxeikF8qq5IgvPlCQEgwbuqDeRSChEZgUq1RuoSP4Kyl8JfltJgWpuLw0ikRHK+FOVwDA2a4whzxhMj1Ia"
        b"R3m54fm6BedMSoJlCUiHEE0L6NNnUZJ/FVRRCmRtIO8AhgZyFcJmZNTqIrtpfJtVd+QPGhGyQsB0JEhwxsqDH9Urggsb96g6HSOw"
        b"fB6Bc0cxGRMILuHw1zmgeAaodiVcKatI55nJ3G5/3ZFfUndI0xiQZAxBF9t0VqJvYMZLWhgWgC0J9bUWFjZacCRDNJpAOpZEg06I"
        b"9gHkMPjvyIFHU9JsLBXHx6n9y02vHDhz5ns6+0zY1sAQFwL1tg0qsgR76IHyj04kBtgOkMsRdAYMxgx0Rr+PHZUdWGoOITYcgDAz"
        b"KKM4bR2rw8vXfUNEIc5OFvapERBjDMQ5kMkq9PYn5iXCTe44hJoFJupqTY9Jfk0imjfw/PQ3sav4CBZjAHJsEb5VWYXfZ4pwp5WL"
        b"MhZW6C6InYvMadMEUwqO0ICaKtNLWLug8L6txkRJsVZoOgeWNJRi4/IgKoIBnCr7BtHSJ9XQvc85nbyKPaYTdlRXdnmSvdrlFMCm"
        b"pqheMJwOlqMmHvV477KU3Gk9x1bvHrj3AkAsDjg2mCPBdRMI+oHXVC/O6F1YFOf7frj46XBzR7PobOn0Ijwyjo/TkpJi/EJKrAQh"
        b"YBiAC9mtvZizVApIZ0CCOZO98uz1LtV13srwtvDi7cfcOeDOg/k4IuKMFf4GMhla5OTQpAi3E6FGKZQzBosIggh5zjHLGSKahmt+"
        b"E5dn9dkLpax0ei5ZKBTi4Vt+X/4LVW9W8tCZjK0AAAAASUVORK5CYII="
    ),
    (  # 32 × 32
        b"iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAKK0lEQVR4nI1Xe5AU5RH/fd/M7M6+bncP7gEH6mFAJcgRkJePwhco"
        b"pZLS5Ih/pNAylYTS+ArBPEzVcqbAWJIYgpZimTJlClPh1FBKKKJGpBCCgAjIoZwoYjDea2/fs/P6vk59s3fAXWGSrtrZnZne6e5f"
        b"96+7hyEQYgAj9WvRT4+1aTqfI4k0Ah14fc1Fe2sqxMBqOkoyGeIdHUwuuON5MzLxqmsZsVaSfpZ73ttb107rGb6P/yGs9mBgQaYr"
        b"FnH19cTYdxjjEXVRSumCYatH9vK3Hp3eO+zE8MMXrjh4rWbG1kGKaUzTQVICjPVI31nz+mPT1re3b9I6O5eK/+pAJkP86NFOVmid"
        b"ujUcH7vIc4pgjANBrAQ9FINj5Q6GzNQVW/CsvQBX8x0d1/iLHjoyh+uhXUzT9UBT+mBcBwkfhlkHpzJw3//jBFeR5Fun3mpEU4tc"
        b"Ky9Jyg+kqH5L+tbNvnB3O9U8GZHkDN/O/ZBWraKr0cCJiBFoLddCuvQcSV71Kea710nPWiGkX3LtgiSSa27NHG8MjCvkvkL0oTx8"
        b"UyFOIKt3ML/k0Ib5nwV3pz65IzyntRtGtFnapSWMTXsCgLu55/2WppRxGTgjX/gv/+PxS3809Ly3It/9m8PMyJMExIVjLWinTa90"
        b"rerUjgLuORGAOnA2hjGNg0TvwWfmDW7ckk9v252vp657SNPESaaDQmGqo35KqM/sKbGJBIQAzurj+lEapOTqF3uaDh/Op1ONdrcl"
        b"+mVVqwgt4o7tZEvF0Y6lboYy/Jw1QERs3n2HHmsY27CyUsy6P196QW5hW4Jli6qgOMtbTsqX0EIavGQkXNA4kCt72gPPHa/PW5JN"
        b"bg5X1941qWRoHFVPwvIc81jlX7G3rN3Ih3v7kmZ0R6lgrXmq7WcfZDIZ3tHRMYIZTB1e2JZd3fmu9QurnBeJmKndcW0TFs9MwPWH"
        b"IFIFyWpklQTEwsDqv5zCtgNZ1EVDmDIhjvtvmYCWMQyeD5gMKAkbz5deQm84C+6wkuv4i56YsmKPQqKDnXGCf/GFM/P2q+pXLp4e"
        b"prIteO9ghdZt/ox6Bn2SAmS7BMcHHI/guiDPlVSxQHde14zWpggqtkfbD/bRxu2nSAPIdiTlXQFTmrg5fAN5ecdlOk+QFH9oP5IJ"
        b"rcKqoXCGHDBDoWXcgLHsmgb5k1vPY5Oao2zJ3LEsHdeYJDDOFfUVvYNvxjlnngQbk9Dx6LJJuL6tnn3jwjp2XVs980Sgz3SmwQZh"
        b"vNHAJhgtoXKxKBOR8NTJRmQGY4zaN7Xzs1lwoe+DHA+4ZXYa17elYWgIoBRSggeGR7KIM0Ahk4xqeOi2CQFC6j+2q+7VdAkEDoZG"
        b"PYluR9BJCapauIAymf1XN5xBQCegnii4wMpVUoyA7QE6B1J1HFYV8H2FwBkHiADDYIiYQNlSTQhw5CgdACYHTto6/pyvUEkzGQwr"
        b"wjoekTfW32sA8GsMBNK1DhrQEUQEgxNs28Frr+9EoViGYQBCqH5CEFLA0IFiycKWN3bCrtrQNWXu9JioVTcDrALho7KGEpds4WeC"
        b"1u3QbrTv/+Ul2+5f71B7uxboDfbTKSOEFlVgCm0JQjTO8MjvnkHX8W6c39iENStXIhTiEALQNIUI4eHH1+LTni9xUeuF6PjxPahW"
        b"hhgzZDybF+CWhudK+3DB7pfw/eMxgqkzcLjwvAfY+lVPUyajs8EBGtQ0pFUdMJU04SP+4RG8u3snLpk9D/slw6wZM7Hv0AE0NzSi"
        b"p68Ps2fMwnuH3sNlHDi6dw/mzrsclamXAroR5DKXlygWOfRYFS2nnkNqYx9kXKEuBVegh8IMtnU3W//I08oBi3NEpJCgEId54iSi"
        b"u7YD6XqgmIdYeAus5jSe/eNG7Ny3B5fPnI3ldy1DtL8A/e+vAnVJIDcIa/4COJNbke8RKFoaDL2CCcl1MOuOgXY3IvzXBBAOUqW4"
        b"JaEbGlzneyzbTz7n0FR+VXnzSgXx/XuA/CBk/ViUZs0Fi0YRMYBixUcipkMxRlarSBzYCz7QByTTsObMw4AbRbHEYaCICanfwoye"
        b"hPDjQMyDvisGY3MycEKCiHNN0SynEFBd6XT9kkqkT+BWBTIWD86ZCP4EjfMaNZW6zoIVhpfLQDyGXImhUAAMXsb4agbR5l7ISD0g"
        b"fUACFJMjnFD5UCRQfWAEydUOY4QZWCwOiNqeREbA0qGWHJiH5wFCnSfjKBYIhTxB1xyMs1fDFB9ByImqoE6PPFbh8K+oQC06+isJ"
        b"gTpTg2e/qStm1VJwxokDhwsolTxoGkOtRYwcHpIIX784ifqUjkqFkC8QtBDQbP0GEXEYAnXgrgPEoqfnyLAT7uUloZuGhs28G5p7"
        b"p0LAZQyRsx0Ykw4hFtWDJnH29WGKKQkbNVoq45JxpPv+hHjkHQi9HkzBU3WBVHQEvgQSJoU1b3apV5rWzeb833+hHLCHHAj6gFL8"
        b"2qQI1Ng9l6imFbRpAZRKEg5x6LmPIXa9goGxKSSn+tCiDGR5YKo3R0LBCBWSyIwamiDKuf1icXz+Ux9v357RddWwOEf6bCPdn1Rh"
        b"22KoM56JXEhCPKZjXKMZnFft2lxInnwZHgSqWQ3+XkJ6BsGoA2R/BXyCAQFGsZCqJtnnVP0l8dYN79P2jM6u6fBVnEUFNVPpHpJ8"
        b"wUV/1sHA4MhP/4CDoqoNrpxRKHBUbcLuL2MwNA0hw4PvMgweYPDKDFz4cHtKiEGIvkiEP/h5ZG1s3Ia9m45kQsq4sqVSkBvO9XB+"
        b"L2tLfmX+FfSu6gOKIVDDyMX6gZuwP9SC5YlOGJqA52nIHwJSczXUVS2c+jdwU3QiDueolyjD2dsBBQPRiSE7GoFcXsBXy8Co/A9P"
        b"wWhECzYjJbYnYcoS/unNglM2sSLxAvQQg1Mm+B+WsH96GksrE9kJ4kjo1Txjv5KgTWfGMSN8qYyfjcAHHxaQL3rQAxqeid7zCU0N"
        b"YcyanjqdhqZUCKm4id5cFru8ixH3F+Pu2Itw9STs2LexXLg4QSf4JSCaV1f3yfPqrQpd1DH83NwA3RaN4eVyGT5jtTVdORN4dA4Z"
        b"2oyCFA0MAq4NnMrZ2HKgF1VXIBk1cPv4LqTGTcXndefhhsFHvTlNunGlZHt+PaV8xSoAZ++EemoMXs32450xDbgy2x/0BK22BAV4"
        b"BJiMXkaCbwmkk0C/D7SkTbr7+vPhS5DOQVU2kcIA3rDf5DeOhzFLM6t5q3ovYx2yfVNtDzgdEIGYNYAWhLAlmkCb79TarOK64rxU"
        b"Q6DWz0ZDMryp8YpV01eLSzgMRMPAPvExtpa3wCT+fl+xcu+GSx/eNXojrjlApIKl/fspetFk/AAMi4WPVpJoJCARCoGrJWSoUEcg"
        b"oRih1jUp4Sn2MiZ7OcOx99zD3Z3Wa59GKNG1tvXBPUr9XMYDB9Rh9Ks0EYWtLBpcDY2MoUEI1EMiwRjCUkK9jnokUYWGgg5koaOf"
        b"CH2JBAZUMKONfJVxdfM/l/o00q3CEAMAAAAASUVORK5CYII="
    ),
    (  # 48 × 48
        b"iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAARe0lEQVR4nJ1aCZBdVZn+/nPufe++tdckJCRAAgGTQIiIIJZIoiRE"
        b"ZZgCqmPhUgMKpEqUTUi0UDuNjBtaMjDOQBQZRXFIY4kjo7I4BBHZQiAJ2UmAhJB0utPr2+5yzj/139evt3SSxlO53a/fPefcf/n+"
        b"7//PfwOMGS0trIc+37QntfS2HSdf+I3Ns5e27sgPz1kzNOdoY+S8S77fmYv3uW3HyRe3rkuP97x/ZNDYB7a3LzNLVm46UznJL7O1"
        b"i8DmOGZWRLqLtH7eWnPvE9+d8zSYCRQv53F3rt7nxSs3f1Rp90tgex7baBIRWZA+AKWfQVD6yeN3nrm+tZVVWxvZf0QBNST8Go6F"
        b"X7xi481Ke+u0m7qaSJ3MjAyAFCk1QzveMsfx/m/Jyi3/JsK1tKxRANNYo8SWJ+KLVmz+oZNIPeO43qeJ6ATZR/aTfR3H+wK56ZeX"
        b"fO31lSJ8y5qJeXVcBWLLLyNz4cpNNyQzTT+yJtBBqTc0xjfKcaGcJGwUcFDuC8OgaJKZxusXr9jwn6JwS0v7kBEG91KDhrgrkW36"
        b"ahSUjKyT9bKPchIwJjBBuTe0JkAi1fi9xbdsWNG+TPZ670pQzX0Xrdg8D46zga0Fm5C0m1QgBRNW3iCikEjNUTqBKCxbImVcL+eG"
        b"fuGfnvje3Mdi7y0jU/t90YpNF+lU/s9RpRAyG+24KWVNCGa7FWBH6eRsQZ4JfUvaYaW0siZ4/xPfP2PDe4WTWou1sQUZ9hbHTWu2"
        b"kdFukhjcEZngU929/twnvHmnW+gPGRNu0W5KgRlsDRPbr8va9s2r4jiYu7kaD0z0dVjLzBbaTStrom0APnyeN+903+uayzBLjeX9"
        b"8hxYY7SbJjDdImu3zBsdl8f0gPxY+pUdSZMOdmrtTrdRaHTC06FfuvipO+f/sYZNcfG06548teDzq9YaT57IQBjBnlq+/1N7Fq6C"
        b"XrsK5uLbdkwLo+ANKJUEg5VyAqD4gT9/96wt4iG0t0MgNvmaP11Yhvs4Rz5DO+Lq/QXXzsbqS0rVuJLtJ6jAkts2zeAQApVEDJOo"
        b"8uZTP5g/a/TUVgW0WVzR/gSS2cUIihFcz0Gl/FE83PIs0KKBdnPBTRs+lEqnnrehHznJrBNUBtY+def8ReMK9ZnfboebOhVRRfwW"
        b"gfm01ms27VkL4JlFbdFEFIjhE1YCRVTlRKFGTSrct4/Tr73GmY7XO7L793Pmjy98JdvRwdlsxjNewjGe50ZewolOnpbL8H7O/Pqx"
        b"H+aZOTO9OZnhWEwSB8B1NTNz9tG/ISv3ZS/Z5751T9YhEQRwSsZJcOQmlIHRtm1RWyTCT5SVYqGvvW9f+u3dPbtJu1PYhOxH1t7x"
        b"uZnvnve+rNPVZ+E6w0TTWfAbjZHkUzVmUzbZ42oVWQtKuOB3DgXOLfe/MSmy1ftakbn72tkHT2h20V8CdHUlhTZQO/s6Gjb5W+nV"
        b"ymZYMrahzt2pHH42KAU/vWvuyvWt3KraqO2oAU015jj/hg0P5usbPmcq/ZFv2Jk1NY8Vl03HKVMVSn7smOoYu518T5K3qn86Grj5"
        b"p7uxZW8RGU+j6Fu8/+R63HrpdEypB8rBcPZ0BiGwO9yHhwf+B31URMrLIKj4xvrRqh+ddusd4gmJvyNCSJiDmRu/c+XJZ4ZBhS1D"
        b"JR3Crn19dvk9r/Ovn+6UaOVy2bLvg/2QURlx+QHi74MAXK5YhgV/8uwmBBHHpk65hPU7u/mqu17nx1/pZcU2nucHlgsBozewOAHH"
        b"4/PpK5AIkrbQ2x8Z31C6OfftGzZ/77Y4P6yR+DqCAsK5hzrxnY8syJzxqbOytrcUKTF3xtPKcTT95q+d1NUfUdJVsa0lVNSIK/43"
        b"eDlaUcln+tj8Oixe0IiugTC+kUs7FERMv1p7kCohxfOIFMl6hxSKMJjm1OMTmY+rCJFDzFTqLkSJVOLbt2z7wfz2Ze1G4DSuAt3d"
        b"fAKR/Xxfj+UvLp6qPnvBFBTKEQYqBmXfIJ1USDiEQUhPYJBwK2685HgsPasRvcUIRdkrsMimNLSW4B49NDRKzDg9+T5M0ZMRkiE2"
        b"DDedJANcF09aO1z2jByOtViYzah0oQBrFdTypcfhzJkZ/P7FQyiUDT63cArqMjrGbuyDY4lPgLFx8GLl5TPwwdk5/OmV7thbX1wy"
        b"FcIHFXP4XhaMNCnMcI/H/koHNBwV+SFrwoelMGwjGpdWZb8TtZbkWTVMoQKce2oOZ5+Si62u5YETFH6sEiYAFi+ox8LT64dIwA+P"
        b"vBcByKs8BPBpRRQZSz5T85c2r8r8B1CoVbijFCBC88iyWjYv+gwa/CoY/E4GM8eYnqgSMr9QjgOnun7EXuMNBlCnU+izjPVlH28a"
        b"g0Jg8nVw68S2WLVKVo9WgBmTahQ4rMTwU2iE4I4j+OZR98cblqtzHC3z7ZACR1vFABIK6Kgk8N89JYTWIyiBAFIDjexJ7lg9bb9e"
        b"PobIFRhN1h55f1Eu4VIMpUKxjJRXO8ccQRAGvGR1QrHsI50aN/YOG0oB/f3Awb4EhIHhxdHN0wJNV663p4hDly9fHXLLaEqV3ev5"
        b"CLlOLO9qi47OLnz19lZ8+ktX494HH4JSsoDj+zXnyWdrLZIJYNdbe3HdN1bgiuuuxm8e/QOSrlSvR06oREBPL6G7D2hSCbCncOE+"
        b"4NnHibY9avn+F/BbvumOv/OXv/kJam833NrqDCtAyA9S5GF2lUd6dQr3rnkYOw+8iysu/zRWP/RLvPjqRqRT4hXJBYAxFq5LICWw"
        b"AX60+l6UfR8Xf/IS3PlfP8O2vQeQzKpYwfGE7+4BBgbECwxLdbhqm8YTTwEfOQDkQpCyyECp85BM/ZGv+9ZV1NYW8bXXurJeNMmM"
        b"axwFqEoA/dJOnB1WcP4HP4hLFi9Gd18PGvJ1MVS6+/pRl8uhqUHh7X39SHkp2ISLsxcswJyZp+BjjfUIN76CyZvWw9ULUJly/CgE"
        b"EwGHuoFiCXAVY0ApLCytw788JzzswCQZ4mySpwW+jS3keT/n678Fuvv2B8QTdKiTuxwHTVEUU+kIL1jk/v5XOO++A+RygIlgwwi8"
        b"9BIUcim4DNx4++0olIo458z5WPPYY2i98VYsPO8sGAJSL70K2roJqG8EKmUgClH80PnwZ8yACjmmo65uoFRiaMUwrDAp+ztMangU"
        b"9pVmuL+tB7mx9BiFU8Gvm9Aol6+kn9z+CyGLpFhzWHjZHFBlH053F5DJwEYGljSUjVB5cxdCKXks48qWZfASCTz57N9w6dJPYsHc"
        b"OSj7jHJ/CPvOHnA6AyNFkuvGD3AP7K9GHWFYeF0Vfkr+YTTnfofIz8GcW0R0eQ8QSOiOALfQmaTbMLRIuA/w9W2LxQO+UkiMptKq"
        b"EpmXX0DirV2A4wLWgL0UBs7/GISZJdULI8m6IATSKaBSASLDgEtIb9wIb+tGwHGq1KQ0Ch++AOFxU9DdyTFstAhvFI7LPIiGuicR"
        b"2TxIMGYBzlo4L6bhPlIPJMTCIzMAGzgJjSh4VRQwUpcdHl2CIovk27vh9ByCTXoITpyFKJ9DnNRJbkt+kOCjOJBJCScMr0/sfRtu"
        b"ZwfYcRDMOAlmUiO6O4WOB2FDCpPLq9HY9BfY3BTAjqgWjqlEfF4y1N0Vk+gRmZ0l1mtuNACJhcckgkEIjr92mBPQ2wX0F6Q8sVXh"
        b"/Z+jIWiHnXoCKONIBhy9wdGVkJ++sNBR06oKRiyKzX349LHC1+BIsraWpAZEeKFeA0MazcGv0BA9ggh1UEYsP0Tto7SmgkJ0Tin+"
        b"c0gJgVDC0/Arz0gpYceF0KBgNj5u1SQ7YiNx9HOlXzeosHwulYHefgwJ3xg8isbgIRhVD0IEhIMHrpEBO1KJokJ0bim+566pt/Ac"
        b"wX8PlL5J1I6IRgexfBbi2Le/gudeOhTX8BMZMktqpXPPasTME1LxuSCKgJ4euSew0ciHz6I5uD9OWKiVAH40vvAjlSgoBGcXWZEC"
        b"/W9DpILocrrn21ulGvXHKhBb3gK5rIs5s3Mxe03kPBPHPTPq69y4nJZ9evuEmSzIUfDC3Zhi7garVBXvVM0H7Ecg8YIrrht/byZm"
        b"x3csnRfoSHVckVx4z9P8dKtwXKxATsqakYlMFMhmNM48fair/h68UPWiH1Tho8SDJoDz/L+jPxUic2oaOmnAYY0cLLjogxrSg4qN"
        b"EZ6ZSZFN5lxd6iovzyxcvWbdfde6tKgtdMAoC07HG6JEIAeCCY4hL1L1IFQsCkoMKKnhbv0zVNd2FFQDKt0R6uYC3mTAyv7i4f4K"
        b"KOcdRhIxwymyXj6pS72VmzIzVq9mEX75alEfDhOKR1JAIBCGQrfHAJAgQQo5p5rYZBgDVKQdI40gP4Te8yTYSUE5BsYHutcT8u9j"
        b"ZE8CrIgSGnBPETQ5h7iernEHwXq5hC73VFZkpt57F3OrQ9QWCx8rAEb/oNJDYVQ9AwB7363g2Re6jhnEMl/mXPjRycjnnCH4GDnM"
        b"JBTcjm3IFXehpOshfRuSip6Bvi0khIjcLIYNCdxXARIOqD4Njg9CMF7G0R094TePm3bvnYPCjzobOxJnpA63fGQQB+NZ8xuOeoAZ"
        b"qUDK00NJLQwFzoSsBp7aX4ewsAiX1b8UGzdkFwoWKgH0bweUA2ROFDgRuKsQF6yqLgUPVl+/N4V7dh33EHOr2PmwulmCuHsQQqOr"
        b"IQbSnsac2ZljMlAtzwll1hSIP8s9Bt7ocfCX4qXYrebg2swaZFURPieh2EK5QN82gk4zvGZRnOB0DiDlB3w1Taf7D6X805r6LEmL"
        b"sfXw3pCcOruOZGEhhBqXH+2qzRkKYq4SgNBvaIFixUeDLmBdMBf/2n8NemwdkhSID4YYp38rIQwU0kp68oSWnkm4vz+LhLLFA5Hf"
        b"F09aVX0PMUoBIhw8mnXL0pQqH+WqGJTKBr4/7N1awq69ApQK1UIjT0W8HU3Fnf1XomhTcCEddWnpKQRFhWBnCe8ohYvMTDyiGtlx"
        b"GAFj4JTUzMKwr0cPYaEDYw/1tSDes6+CZ57vjLsLE4mBJRdMQT5fDWI5WkpScx3CiZPSeH3vANLJBPIo4y0zAz8rteDm3C8RWQ2E"
        b"5ZhPmT+OFTwJf+PtcGBY+u5nZ9Se9e9fHsbwGadTLbXQxlIpTmJqbBA3NSTwkXOaj5rlqwrIwYSQSutB6ADJZNW90shackYztu0r"
        b"YFdHMW7Va+7DU5W5mG0/gH/Ov4j+xjMQzlqC7unnY0fhFxJNUrrxsqYczfODv6yT1yurpI87ThC/9RbWzzoJr2cymFcsxrLGbQux"
        b"YjKpMOvE1HsOYrlSnrRXBIJAPuXgxk/MxNoth7C3u4yEVsilk8g1XIneKZ9BJX8iMhrY7h/Ca8FuTHLS9uKGNE0LovLOSvRA/JBV"
        b"hwtfezY6O/kzzc34dc+huBGXGAuPiY6RZFBjooNdVUqV4tCTg93g6SOuLhgoG8CEBnXQuCX4Pbarl+3SXKPJN2Xcgx29N/1k7tfu"
        b"Oto7AmJmTUSm6yD/qmkSPtvdFftPRIm75xMXf3yF4mq0t+oJiYmatzh+R2hZa+bmpOZXeAfWRO1qZi6n2HPQf6jww7vnrry1hdfo"
        b"djryCw5RYPAdC9RAD+7L5vEFsVipFFtL7GWHe53V9zEjPh9rSGzFv+VFiJyZBwlDuS5UNl09S2/g3fhD+U+wgZEXI1tNENzx41NX"
        b"PDShV0zx7szy4iIGS38PX6YUbjQW56RSSIrr47pEus1m+BJBangfW4rXLkmQUgrJFde9gzQhR99CAWHBDOx9rvzyu0+Xnn8z5Xjb"
        b"HTgvvflKzzPty9qCiQg/pEBNicE3MPGicplP8n3MBjALFjOJMd0AU4nRxIDU2JKikyCIioPNkhgd0uqPQJB3pyUi9En/ihkdpLAX"
        b"wFuOwq6ogjfqt9IeLIohO2ocCzbjKjBCEWEhW/PIeGPPHk7l80hFUXwlUwpOQFAJhmWLyHXghw7KdXUoE1H5aALI/4RZtXaVwkJg"
        b"C+ZxO5bJKXbC1PH/CJIML6ZVcX4AAAAASUVORK5CYII="
    ),
    (  # 64 × 64
        b"iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAZMklEQVR4nL1bCZRU1Zn+7n1brd00zdLNIooCgpAQRRzFBZXEJcYx"
        b"k2lyzL6KyZljJlGiE5007XEdZ+JMxjkxZOZMMk4Wu2OSSYwG0REiMQnRBAI0EQMBZO2mt+pa3nr/Of99VU0BvVSjmf+c11Vd9d69"
        b"9//+/b+3gJGISLS0kDHi91X3tbaSHPO+4R8WLe01zAGgpaW9pvvGS2K4D5mhtjah+P11d3XOJWG+mxBdRKSmEyAlZJeQYouCePa5"
        b"B+Ztriywo2NlVPPMra0SbW16jqu+9LvpFiVuIGAZKJoFEpaQsgckt5NQzz334PwXT2uO0wGgpTzJFXf8uilhTbgfwIdMJ22DFEhF"
        b"IBCEMCCkgSgogUg9h8D9u3WPvP23rDEdHWLMBVYAfuft69KGPasVQtxi2On64eZQkQ9S4ebIK/z9+n98x3NvNQhiOObfefuWiwwn"
        b"1WHYqZlBqZ/VPCS+V5RvJ+IHCYBhJrIiigJPBd6q9f+w8FtjgsDmopnfcpa0Uz+wEunFQWmAGQ95ChKVNREECSKQNO2U5O+isHDP"
        b"cw8tur9WoGshObSuVpLM/Io7ty4yEql1Qhoz/WJfEK9EmMy8IBBfMXQsIiFCdzBEFNhWIvPNFXds/QAvbER7ZbVfA7rq73ZPlbbz"
        b"vGE5i71Cb0AU6TmY2coczDwEGUIIGfrFKApKkZ1svG/FF7fePeocpwcACWANWj6/PylhPCmlVR/5xVAIaTH/RIpMO2VYyXpTX4ks"
        b"Ty74cwhpKhVR5LvKsJx/X7F6x5yOjhY1nGNsxRoGjoyo8E3Tyc4O3FwghLD0h0SKJV2Zw0zWmUIY+nPBYJOSfrE/tJzsfdfcuXM5"
        b"C+utAEHynytaNxhtbW2qz8591k41zA+8QqClTop4EaadFlFQ2hC4A62BN3BX6Od/CCGVYSYES4+lpFTADCQh1QOMTWdnx4nm1U4G"
        b"2/2Ku7bfaCXrrw2K/WHMfCwBy8nKKHRf9d2B+/1S/52hm3sCQIFBiYFmkCI9pqLgK9pcF+yItfFNkIilD7S07rD6S6rTsJKzVeCy"
        b"kQvAIGGYIVTwqXUPnccLGqJr79x+qZJmhyHkVBX57CJE7CJkaECe+9OH5u2p2Hu143vXndueN+3MVYGXL0sWStoJqZR/13MPLHi4"
        b"eo5r7tk5jyLRYRrWosgvKSGFJKKItRHKu/Kn98/f8GadotnS0qFtf9Db9nbDdM6OmY8nspNpwy/m7ln/yKInrmglE9igH8ofzoqf"
        b"Pbxw0/RbfvaBAlnPs6cWLHZQKJ2sFXq5dwF4HBs2sIYpzhXahFDX37G9KSC6OApKDJaew0rWGUEx9431jyx6mJnpWtAieB6eY919"
        b"81874+Prb8qZYotSYUaQIgJIOnUUurn3ANjQsWDysKG8ZgC6ygMookWWlYQKgwhEhjQsIyjlepIwH29vZ6/bQe3tLVqaTEvwivXq"
        b"2iUv4oPffxl26lIEbsS8stbA9xYTkViy6lXxygYSa9ZsMNYQRdd9aec8Q9qpKCgp9qlsOpFXCoVJX2ETWbBjg2hfgwhYruc4r3m7"
        b"3dm2cA8+2PEUnLqPwS9ypBDslhGU3havZLl6UwCgPBnImFiJiiSEMgzbCFWw+8f/MH/wx+WbK1GwTGW1M7ZC2pdCuPxeQEgBhUY2"
        b"WQCBWKtvCtvagBWrt9bbiSQi/o4zKtOSUeh3r/91z148GIc1vq+KOApBIrEF0gYJk52iiF2XmMjvV61dazRTK9pEnFSNl+TQO0He"
        b"0HviAKwghcx+/RWyXn6ZkgcPUoqIkpXrR5u60/yatGWjJVVoGTKwDBFYEmEyaXrle7L8un07Zfi1PuHoccuT6PdCiNRDn5g7Zf9+"
        b"Sm7ZQnpMfe2n5NfX765bd3hdWtl9UxRyIQk/MA0EhqFCYaDAIK9dtSpg5lva29mfjNscxBWtL5ob264Mr1m9/UYzmfkf3xuMBATH"
        b"X3hBFD300bN3Lz4rlewvKGlb7JCPTyIl6MiA2xgq9g/l/AAQadsoNqTtQVW+VxGQtEGvHXLtu761ZwrrsH5eAEWfcMdNM7quXzLB"
        b"7x5QwjblkHdWUEYkQuzL9WZ3lfY5v3c76XB0FA4SwrJlIZUJtgnTeCkqed97dP5d22LZcVDS2lcTmRuxQYvEtIJX3dJgzjLNOqVC"
        b"1jNBJIyOlwfmLjorhSkNEnk3XnQ1ndGYGG7cehKoPwFpAZzdnMCUehOHen3YZgyDIYDv/6pvytJzJ6BpkkS+WG1qDIaNt01qwmI0"
        b"4T3qfPzS/S2eL/ycY9cE20peJmx5WUC0evWeR9eqXHinEKLQ2toqOazXpAHQMbrd6Fi5MmpZs3NTLnCWUVhkezSY2XwpUufOTOOT"
        b"K6Zi0ZkZeNoqjy+SI3QllFYNS7Ii5jJFCqhLQnx93VHxvZ8fRX3aRKQo1gIvommNDn386iZccm49gkhn2zz6kCg5zhqQ4KR4p/8G"
        b"vj3wfQoRKAmpcUw1ZkRpoLSZQvXur8y7o6d1TauoBQRBpNPNiPL0iZ1dweOf+bfXZMIURmViKQTcQMEPFT5z3XT89SWNKLAmjLMA"
        b"ZoYsEzg2EOK2b7wO149gGEJ/ziAEETHYWHnZFNx6bTNc/xSnGwOJCFlhYJu/F0/knoQFNn2t+n5qYsYu9uZ/Xne4cDWWQ7WhjcUz"
        b"qjlIhvngQTqj31f/On+6Za1cNkn2F0KY7NDj8IikLfXVvqkL3blIMxJLqHZiZlh7pk8y8Yl3NqPgaQeoP2cfYRkCEzImnt7cg9cP"
        b"eXCs+POTyYCBQYqwyDkTFyeWoEQuJCSPxcwH6cbs5YPN6dvYMbZuaB0zVZbsMJIOPjdhgkz1DCD86FVN4saLJqEnF8Q2yrpMBMuQ"
        b"WgtKnhq39IcWL4HBEuH6Cybg4yuaMFAIEUXlOcpzRUQouJG+d8RFQ8JThIuTS5EWaa0VZZQNL+8qAq3+7PbWTNvytmisyCCJKEHA"
        b"TaUCh39IPwT+9sbpuOWaaVrKA8UQbkDozvl4x+wspjda8IPh1bMWYpMquISPXDkFq/9qJhK21EB4gUL3QIBzmpOYNz0BNzjV4VaI"
        b"dZNd0SQji7OsWfDJj7WA83A3gJNJNjlW6nJW/3a0jyous68P8wRhlufHGslMs/196MrJuGheHZ7+TQ/+dLSEOdNS+MDlU7Qze7PE"
        b"E3FEefeSBiw+K6Pn6NxfRPNEGx9cPhWOLWsAmWBAYIY1Ddv8zvIn7DaFYvslEhcCeGYHdow6ikmE6Y4Dw/O0BpQLGiBXJJw5xcHn"
        b"b5wGLwQcE1oqYXT60q8mli7PManOwqprm7R/sE2ANbBWDWOG62R26L3J5ZsQSDEfUk7TX8Tly8gACIVG0wQYgOoOEdsjL8r1dSUK"
        b"P4hf3wrmq+cIQnaOHA7HPwcvOCESsIWEI4CuSGG/H4k+ARQGS1P5ns7uzlHdtSkEJpWd2ik38kIqKjGkGm8xibLUxjsH+wG2xqyR"
        b"RH8ksL5Uwi4vQMg2zOHDDSbyaB07FoweBhVh8nh549AYKQVVvsZLlef0GEO1wfioovI518J3+j10uj5CABZBWIpgExpUaytnSZIz"
        b"w5HGMQmYNHqqcNLiiZCwBWwrDl3Mf7EUq25NCydCihtvRmxvbAIlLzaB8RBr7cAg0N9na3doiFBrRODEKCBESsSZoEaYWloM0dFx"
        b"SuPElEBDrfzrpCgh8Pqe/Xj6hefh+R6uvPhSXHT+Iri6jyLGZJ6ff3XbTqx/aSNMw8R1V12N8+achaJbOwjMfG8/UBoEMtJGHSz0"
        b"Sg6MEn+9B/LGAxFmF4wZSz9/7zMW5MaiP/CE+LdHDlFLuyFO6h6Jnm5an05jRaHAEaSqPD558UrBsYG9Bw7j9vvW4EjXUUhpwHEc"
        b"tH3hi7j8ovNRKHKSNPwQrPIs+c1btuPuRx5EsVjkZisaGybikbtbMW/2GSh5zNzoWRZjxMwP5gFLAi75uNT/KtL5AXxvk4WrD5TF"
        b"ycOkHEAaQOB3IQhWicfafkStraZoa2NrGbqtbsy0lsBNDDhZiWd+sQl/fGM/bv3krbh39Zc0I089+3ScGnOzhjv5VZcqv1Yk98N1"
        b"z6Kvvw/3fO4O3Hbrbdh39Ah+snEjrIzUcXikHDt2yEBPL5DPx1klG2EkbVzUn8CGdYSrDwBhAggdIOJ2q+9GcAsBQFNgWz+gv/ny"
        b"+5l5uuUWqzocZ9iOR9Q+bsKZgBF4kFt2Ye5gP86fMRM3zZiOd11yIZYuWYKzZs6KF8lhyRH6cuz4YpXn/yu8nTljJi58xwW45rJl"
        b"+MsZ07Fk5kzMLQ3C+N1rkJ4LYs92EgaVZ4/1AIViDCTHgAACGdqPZ54fwHm9JqIkwVSxY4urOcGexkIYsMclWPZ3NQhr1wasCSib"
        b"wF7bxizfjxOhE5knkCFg5PPI/nIj5MAA4DjxjZ6LqHEyBi+5AsKxoUKFRELiP9ufwq4/7cFnP/xRGIbE4//9BKZNbcKqD94Mj50d"
        b"9wH8AJlfb4J59DDgJGLuPA8qk0X+kssR1tdD6L0ocQLzpXIVKhAhVAYyzmuYPulfYW2XEB2TIaWKdXo4JWI1lAZBcvLh3yweu/dJ"
        b"BoHzgOSoJiCB5B92xMynUqAo0vkyJZIwuo8isXsXigsX8haCXuzh7i48/fw6bO3coZ3avkMH8N5rri+3eBQiaSC5dw/MwwdBqTSE"
        b"UnEGlkpBFgaR3LkdgxcvO0XyJzNfl/w9ptV/DQgVvAsMWLIX8nsNMffDgcAeWmn/R7Cd79Jtf++LtrYfmiA4owLA45WKAKeLcfdj"
        b"SN35M1ksHO8beITPf/LTOGPaNPziN5s16O+7/ga03PAeBEEsUX1voQCYxoljsR2aFmSppNutvN5hmScD9clX0Tzh6yDefBIWZF4h"
        b"XFzSY1ljglCWlLCeoM/de6HoPUYFKZEaNp9hSVsCid17kHr1l4DtVLWCInAtm7/kSvjTmiAC3tAT2qmkkoDrxvMnEzEDXPay02Ib"
        b"t7qPIfvSC+U0sFyyazNwUVq8FKVz50L6hO4+AcbjBObtzWie+A2QbkNWcakASiuYW5IxCOwERjaHCE7CgOc+yT7AlxLWaFrAQCf/"
        b"+BqcP+7SjooXrpJJlM5dCO+MWRAcVIb2dOOQZ5TDGWd7rB1DOULZqToHDiC58/eQxWIMtO3AnT0H7rz5vMOCY70AfzXEvDBQ721C"
        b"U/ZrQMOEuCl/cu+zdhBiLVDUxwBwhBuzxUEWm0IAyTFICETZLMg2IDj/GG+ZwCDwlmigYAzmtB+I0lmolAUZAr19QL5wnPlIGMiG"
        b"v0RT8WFgSgNEfSbWwOFofJpAZq3LFz6bg4WwkW0M4B0/VvvTKg9F5VmJkKVZHs8ICX394gTmWfJ14W/Q5D3CB0cgwCYzSv3AzxXk"
        b"WD4h1gDCAEeB2jhgp8RVVhn4uA98asyuYZgT3uhwp5MkgYFBgVz+RMlnwq1o8h7mXaF4vorkTyjexwkCp6C2wz7gRW6IjJoCD40p"
        b"j/fu3gyxMzzB3/DkMk5wBnLHkxxmPhntQrP3YPlGlnykw1783FgLHgEEjuGcjISBBzLaGFbtA0ZzgiysYinCYD580w2RhnobJidD"
        b"5fl4PN8H+vorfCkobnBEB9Hs3qe7fwRb7xMxkR+e4vtqB2ECJ0MRUpYJt7hKPHbvVjaBQIiRo0DcrQV+sbkHbxwqxWXwaaiBbov7"
        b"CucvasCFi+vhlfv+PBYXN9xr5PMxvJtmqEE0ew/AQA6E1PF9WI6xfsSbCHElROMFQYTWM00W8sW7xWP3fktngkTwuY3GkWg4f8Af"
        b"8CLnzM6gLmvG20WnSZxrzJiWHGqs6po+p7NgDbI+HWYINPv/AlvtQ4Q6iJPPQkUK5AYQdjmFRs0gBNZlZPniyKPOZY8+QC+2muLK"
        b"tpBNwBtLrXnhs2elcM6ZqVF9z2hUeY6ZZz/AzIdhXNbye/ZLZEskdj0Fcl+GOqsBUoSgqhxjiPIekE3UPjchSE5OWN5R95uJy7/2"
        b"BWYevGeAuCdYqsWug/Ke4FtBldKWw128MaKgTAmjdx/MHU+iL6qD3R2hbi7gNALKrwKBHXEpgOAWdXKE7aOTmW9MWG6f+/1k09c+"
        b"zp0hZl4fswArB6GoPe+bd/A1MT2USetW2nE/wAzaf/guhHIhHQPBANDzisDgbkBHQM3Nca6onzNIjM38xITl9ntP/2p7581ErRLt"
        b"7Rz1hp40CcjL8iJG0wRLH2YbIaMalfOYWZZ0hXg+7v6wCfA+BhkSsu8NGF2/BZlpHes10wTkXhMIC0D9gnLuospaUPDBuyuCTSHS"
        b"xzhPoIgQpScmLD/nPZs42v2+5cs38tGbU84OmNxYFWNkAawhr+8p4EiXO7SjWyPvug9x9pkZNE91ygzHX7Dj0zvD7Pok4BzeBApK"
        b"EE4dQJwslOe2geIBNgOBhrfzscQqEI4VIHg3hXdtqkAIFalM1jSYebvXeC/O6wiAViGGOUZjEtA/0r7AECMC2PtGEQcOFmHZshKS"
        b"a9wRVsikTcxodnTbWk9EceyPwZCa32L3Hkw1AxTjWHB8V5tBsgG3C+jbKtCwuCqj47b60RzktHpdXjMIXJRmk1Js6peHD79svW/l"
        b"yke99vYWY+XK2OkNB0DfWIywCl+6tBH5Qn1cp9fGfwwCNx2zpm5/V9s/b7FVUuDBUohHD96Ay2Qd/jL9kjYLj2zISs5fBUL/doGG"
        b"t5EGTQ8YRFCHBiCm1iFyLGQRUHuxXn54X+pYsLKtxMeahTi1HT4EgBToGSsKsMRsW2JykjMyjJs49FVnfuwPGIRKktWXD9DtWviu"
        b"uAGd4VzcmmnHZKMPBUrAOAmE0iHATEFHCB0d2BSCCOHBAdQ1OvgvMZE+lmuAgDsQtz5GD9ySCMdqsWleMG9asiTHe508PgMSN2BI"
        b"A8Bb8HxYvEHmsT04B/fmbsWfwmnIiBKi6jKlDEJ+j9DawCeZNZBCog4BvtqVwEcPT9CgGAK9mvuOlaN6OEkCx8rdoNEPEkg+xRGb"
        b"2niuSsOnmqp9CE9a8EIdziMYyIgieqJ6PJj7FHaHM5AWLtTJtZooR4dA6ow4gxB3UzM+hxncGKX4qKLo0fdOXjD69rhQ6GbvPBoA"
        b"Wm0VIe9WnZGrkfj+ZHL0kyrVO0Is8YTwUFBJ/GPuY/hy/ePaHDyydMTQBZ0p4Ocl3D1FBHMlPunPwrfRAANR+YwAj0QxAGOQSRLH"
        b"hm2Jl4nVl6X40q9OoxjSlZ7C4oUTsPi8uqECSO8L6pxC6Lpm5qQkHFMHRP0YSzwhfPSqejw2eDPuqV+rj0JxlaiXGQVA1A9ZuAD/"
        b"hIX4Nu2AJeKDMrwRM1EKLEk7u57jk/AANo4GgONgb+ChzzQxMQhOBaKS/DQ2OLqaqy5la+AfYUSoz1plm48B5QYzX5xe84GIWZMc"
        b"LD2nAf+74xga0la5Z2EgK0vYFc3GU6V34SOpHyEfmhDKByUaEcy5Cfk570en/wIM8DHdLJSKMMUyjfeZks5xEr9iAJYvhxoVgGxW"
        b"dPUco23JJC4Pgvi088k3sdNatCCLhfOzp5UJMlWfLGF/kkzGuQC/Z0f5/oub+WQqNu/uH8pK4xQ5h58ES7E00YkzJxkoTl2GaPoy"
        b"qEwjBkNgW7gLkbARkcLZjhm9d2q9lCXvt7f/V25H+dToqFmLGYsF3zFtXFEuiUcuhjiXHyf/1eGvQqwN2TTAWwoMDKuuKSU+fdUZ"
        b"+Is5E/GHQ3kNRsoxMCFloTGbgNNwL/KZFCIj7orVBxE2RQfxx+gwpLBwSdrGspStLNs0Bovew/yLtI4155XbSCOTYJSOHEHKMdGZ"
        b"SGImb9ry7+LwZ6ZKPdDNrqoicXaYVqU8Pg4aRw3ug7AmCuJeIZCCgZsL/419YieuzUzADBOBaMhYxZ780/88/4vvaaVWWcsJclE5"
        b"KdrTRdfV1eGZQhFhFGnH/P8Dggv09JU3TnSKW3GFJy20/DegCI2GgWfVNnyHfoCLElnFP64TE1JWkPe2qRItr+vI6QZbLUdlpT4m"
        b"S2Q0ThHP9vfjjvp6mIahEyQOjn/WElmfIk8AU6cAmUwMyNDR48ql/b7+GQKZVkQz6wzVnT0Qve6sC5clUpG0pTQa0lZY8F7AYLTi"
        b"0YW399bKPNOQZVY0obubbkkn8ajjIMX7oWUgKvfqq+ZWeo1U6ROwM/Q9EL/qDSkJFXejIVM2ZMIGtqrd+B/3Z4jYt0USoR/sUVH0"
        b"1a+cc8dX+ZlaVX9oblRRBYTDh2lhNoUvK8KNmQz41I0uZdkR8mu5tc2NBf0jnmpG4t9UHv//xPFP/HeYtUjThOAQyf0HfuUdft8D"
        b"9uW68IvSq32vlH53xJbWXktanaHEz0UufOGfFq8unM5vBZhOkWQFBH4/MEBzhMDVpHCBijCXCDMBTDFNpBOJ+PT3kKfgJI2vcuEz"
        b"1KmqqkWqu0K6BK/MzmlwFIdF19Vd6mMCOKSAvVJgW1+Y2/xY/9p9a8P/OJqbv7P3ZCbbqd1YKU7vl2NiuA9J7ztrXT9BlYjI6u7G"
        b"JNPEZKnQFAJNgjBVGpjIvgkCfGyTrwRvu8e/oIqrd87fIMD1Gx/JLEBiEEA/CD1SoEsKHCUDRywLR5JJHOMfPoy0aFZzbIDsXH4e"
        b"dWAl5+d/Hl9FRHyY2ixff/aocNLcOkKV5zZa47XEvxh7C+n/AEfu6/OwZZf6AAAAAElFTkSuQmCC"
    ),
    (  # 128 × 128
        b"iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAA4I0lEQVR4nO19CXydVZn3/5x3uWtulqbpThcKpWnZdxfasovCMCMp"
        b"i4IgIzp+g8sng4COaWAcRBxHFFFhAJdRZxocXGYYqGCLICKUpVughZa2lC7Zk7u+2znf7znve9M0vffmprlJI59Pf/eXNPddzjnP"
        b"c579PA9DJaG5mTctWsHaN4H5f1gz8FVqTxWLT0vKpVgqWlogASYxUUFK1rS8lbc3TmbA0gPmkf9/Q1uHbF3ZJMAm8DzGBaRkS5pX"
        b"6/RzJLc1rZRac7PkmDAgWVOT1JqaVmojuYuup7nQ/fgzBDaqXdIK3rqcefk/ffC2zTMc2zmW6WiUYDOlRA3g6Qw8CSnauRbazDSx"
        b"KaPtfe3plmXuwHOWt/LW1uUDzxlvaFoptcHzuLB5S0JkvcXgWCylmMukrJcMIUDmGGMdYHw7k9gY8iZv/PXdk5NDniNoVng3EwDt"
        b"lNZWf8HO+8KrDZoRvkwy+WEpxamaHq7imhlcKdU/Rq9hDFJ48Ky0BOObGWOrhCf/c9XXFz5HVxI3aFlBF48fS22mdwbi6OQb1hr1"
        b"9fGLIOQVgDyLcX06N8JgjBGRHjgPKSGcHKRwdwP892CslaW2/s/j37nI2v9cRQjvNgIgNunv1iX/d219OBT/HIBP6Ga0QQgPwslC"
        b"CE8wFJ68hGSMMY3rIWh6GJ6Tob+tgifvfOKuhWvyLHU8uMHgXX/Bba9dC/DPa5p5HBiH52YhXUdKgOYiC84D4Fw3GdfDikCEZ20S"
        b"EvesMn/2IFpaxFCu8udPACTjadqAPPeWjVfqzLhLM6OzXDsF6Xmu/y3JdP+ikk+S6mECkJoeijMpXAghHnL7e29+6rund5FOMSAi"
        b"xgDyyDn/iy8fz3j025oRPUt4Njwnq9g3Idff+mXMBFDErhlhTdNDRNR/dB37M0/efdzawZxyokJZSlhzczMtiFzSvFq74JbX7jPN"
        b"+M/AMMvJ9rrSc4k36gzQykA+AXEBTvfQb46V8jzHEroZ/bhRXfP8eV949XRCvlIsxwAUUgj5N2+8jmvx5zQ9dJaT6/MI+TQu4lBl"
        b"IT8/E8Y0+nhOTtjZXo9p5pmaGXn2gls2fYqQP1Klcrxh+IkS8ltaxHlfeDXGDPMXRrj6AjvT7Q0gslIgpasZEV1IkXWszOW/+5cT"
        b"flNpTpB/3nn/sGGFEU00e1aaRJankF4hkFJ6jHHNCFfBySXvXHXXotsmsnI4DAFI1twM9qfuNwwRcx4zwtVnO5keB4wZYzEYWjyu"
        b"GRpjmuu6uQ89+fVjn6gUG80j/9wvrv9KKFLX4mT7XCkF7d7Km29KMjDPjNbqdrr7zlV3H3vbWIu1Q4WSO3hJ8xqNtFkvYj1oRmrO"
        b"tscQ+QS0E4VrCyk9kqetF9y8aREhf7T+AiIitfNveuXaULhGIR8YI+QT0HOZ1Oxsn2NEa2497+b1n6T3+/6CiQVFF5ZkV8AuP21G"
        b"az9CyGcjQr6ynYSUUvg7ojxgnHPhOYJrepVg8udLmt8Kt7W1ki12SMgi4iEiImLiRvR7rpUWkF65+grBoHkoha/MuZBO7Omk43A9"
        b"/O3zbtp4AukeE00n4MUXrUmcc8v6eVw3vu5aKQ9SlqWUSUhPAh7pRlw3SRhyphl5Y9othxiIE7hW2g1Fao4NZZNfIrOwqak8hbXY"
        b"fATz7td0MywEKa18WORLKQnpHo2G64aaB/2k/9PfFUGUMRGycDTNMKHJH5AYaGxskhPJa1hwUf0dx6Tm8a/qZjRGJtKwmrEy7oQ0"
        b"zLhmmDFNSi/rudZ24VhbhOd2MG4wI5LQmaarBRxuYIxBs3P9gnH9pmU3bTiytRVipKKAWC6JsOdyG64ywzXvIaIqR+GTJIOMMDcj"
        b"1XStJzx7t+dam4XnvEMETn/XjAgvbx5EzCnXDNecZmQnX03jIdGKCQIHLWjeEXP2TeuPY7re5BASGC85YMI8002mm1Hm2NnHXSd1"
        b"hQA7xo7EF3b15hbblnM0pHiPa6e+AbBuI1ylDb94jJFzQA9VhXUNNxFBti0ameOqdTlEY/NGU0r5Jc+1lfOh5A2SuJSURiShCeFu"
        b"cu305ziXx1lhtsAOdyyW7qQF0jCOdazsjVLYG4xwgtZF3VPysQD33JzkkLdeeOOW0NMtS5UVhQkAB7P1piagFdA0/ik9FNWcTL8L"
        b"Vpz9SimEvxtEu/Dsv/vtXY3/VeCyXgB/pM9FX9p0r3By3zYiiUucbL8HtSOLrp/mWCnSqK94361bvtK6nHX47HN4d3He2TPT2nC+"
        b"Hkoc41jJ0oQspQTXJNcM7lmZ263Q3jufblmWG3IVafGv0efCGx97wI3O+SLXzBbhOUoq+KKlwNAY466dFUa46ijL7f0AwH45UayC"
        b"IYiVjBZtSfPGOKS81LOzBa4ZfLkUmh7iQog9jkwve+LOxv9SETXSdsl/oJDlf4h9L2mW+mNfXbTj8TuP+SvPSj8UjtRoBuOewTUU"
        b"/Gga0yBENFpTUye9D9Ery2efrf4QBfsoOJcMvLTM5lxohHzPuvaJry1sJuQTknyxM3Qeq/XHv3ORvequxts9L3e1phswNVMYjBee"
        b"B+cwNV2Ymi6jochV9LqGRR0TwidwABtqWrlSa12+3Dv3HzadY4RCTxLVlnD2qCAK10xhO5mzf3f38c/c8ANp3P9J5pR6oRIxpAi1"
        b"QM77u2eetrj+frhZYokFESslXC0UJ33g0X0PnPdhkBY9bKzA5xJNzfvivdn2NzXNnCI8mwJNBdmulNIzItWam+m5Y9Xdx3+lsXml"
        b"2dbS5JTmNJI1NrUaba3L7aM//fQ/ZrTI7dJKemCF50EvYZrJPNfq2JNx5uOnF/WXy83GTQS0b6IECIAz+T6uhySzc7RrChOAhKdH"
        b"qnQ70/cAIZ+iacMhn0AFepQptFxs2/vgjYjXvwhBZhm9qhB+JEcuxeC5p+BjD4fxo+W54MKiC+cHrOB1p/cda5ihKST/i9r8Ugry"
        b"47u5/s1dfc4dikCHRT4Bk22t0lGOqvuW3omP3Hg59NAiuJYgY7bQDYCQ0EKTwd3jADyLplaOVhzWWMEBA21oW+pPmuE4KUQpJYX8"
        b"/5pnZxwzZN5DgaJ5PdvKD38GRMAevX4dg1jFwnHOuOaRiC7wIYOa/APTIaqPUPc3N5dUoPxMHkDjfLFmRCiMW2KRmaDIpBDyey/d"
        b"f4rj31vurmSytX0NY3ja5Vy/l+lhGq8sMg8wrnvMjNCyL/YH6o9zwhAAmVr0U0IeQfarVNG9g4EMPs2IMOG5bY/dcfTrRNsjDuG2"
        b"T2YUVpUcv5YDqnShf2BSSCH1kA7GZ6h72xaVtXASmDOsv4dBd62ky3XjCWLJlLI2onksXSNojIKJ30o7TXJGKz4XNR8AYq5/Lw47"
        b"DEKwL4/89C4kSvk5KJTLuJIer5NfpGnlIThpGkgJYuRjawP5GQqzzfwLaV3JJJ80klcwJieRElEUJATXDAoIdUi7+m0aj58gMgJo"
        b"WeHTL6t9B1Lshab7vsOi71Rf+fNoO/yK4EGLHumeQW5SvSx3N2Qf/d6+aQ05d0b0aWpqUnoRwNMQinkMS0RcaCG65+TaHl7q2UuX"
        b"LlXPZrK061oq/kKRbqRq4l1ky7FmooIRzKO5eYUiT8whE5GlyzPvWQgTBA5a9Kq9R5Hd75QX8JIJKSVfML2KdCw5kk/r8hXqJyBi"
        b"TAvwROy60IdEEfmFpGfRPS/dv9sr9eyW+1rVT8GgUrSKASNk+Zwu9nZ/PwWHZAtxtxHMo6WlRdBOmL19js4ZS6jhkt+k4Dz8vzNI"
        b"p0mu1JY0bjKaVjZpioAOPwEQMiTzQ6+yT/lMiuTnkW5AOgKgHcMYE/d/8hRHNku+erXUST0o53PjPR8x6CeEXCQlt+GJDFzPKvgR"
        b"yMGxrVAo1EX3NK9sKvmee67zv+dAd0kdgIELzyVfVMPR9XVH0T1r10o1rlKf1atXq+c//Nbq8EefuDuG1dB35H4yV7i5Gmll/PF6"
        b"sODJIR+WhQtHZvmOVrbce7qlJde6vJVMR9m8upmeyQ6vHyCIvZ9388ZHjHDVh91ckriBXkwE2K5nf/Wj8x4/fm5sUtZGDWfQRyLU"
        b"OIPc05er7ck4cbIDh3PVzq6L7ouYmiOG2TGeJ1AT4+KR57omff/xvQ1RE7LYPZwBaRu4dtnk3dec3dDXkxJcI9IZBoh3ONIN29LW"
        b"POlkd2Y7Q+s7d9fvdHfKPW47LGkjxEzoTIfwdWsC6blcj8TEzkS18zIYf4Vp/Nn+rtfW3n/K/YrrrpQrteVYrrgKxt0P0LjGT/qT"
        b"WM8Y/3CpG4k5OC7MDbtwyZnHAyJJZtfIBzB3chjzEC7rWskQL2dVhOQwTOCYWdXQ+R6Valzqeg0Cz7+RnX7lMkyPx7ha+XK2YpgC"
        b"pFIH58D0WDXOnHQkclJij7sX66xNWGe1oV/0IcLCStz4uiHps8YCpkcWMM6udLI2qqqP3nzTtm/+3HLtB5ez5bvo2c2ymbewFjGu"
        b"BJB3TzKOZ4VnKVZfbFMKAURDHD9fvc07duY8nHxkjPWmBPQRUgHFkQIhOWyYlMRNWc8E4FjAlARj9QmDdfY7MHRK5y4wDwlEQho2"
        b"be+VP34yLP/2/Cno6hcUCxmRYKZUAYomcMYxFdMw05iG0/hp+H3mebyQe0VdYzJDCko0hisAx0+BJ4s6bC7Qw8YKlmGfuenNb3yr"
        b"4+3k3S2sJae4ARvbDOkhc8ybghvjEUtuBQ81eK5V1B1MaHNcidq4jjs+MhdHTQ+hl/Rg0u0Ps4tDCIl4hOFrj+zCb9d1oyqiq78V"
        b"AwbAdgU+e8ksXHxqDfoyFAeW0A5hInmLn2IDdEKizX4Lv0o9jm6vB2EWHiwS/Ov9RBPBdU7BTzhp+xUvm7v+m4tufYV0g5ZlLWMW"
        b"NBqCWLLppfb72xenetLejyh/v1iOfzBwmDpHV9LFbT95C6s39CMeBqKmv7MmAry3sRpcHe4Y5kIGxb3u+fXb+LfftiuGnYj6grj8"
        b"fKb8o8gM4HClRFoKLDTn4pPVV2O2cQQyMqu+O+B6poaoS9eTud60ww1+ohYNP/P5tjv/mpBPRIAxgoN2dm3PS5wmfNtls+2QweAN"
        b"o5kKKRE2GJIZF19duQP/+NPtWLc9jRgdpMLhA84ZshZw0pFxzJ8WQc7xSnIlSUF7DhgGx49X78XnH9yK/3mxBzonwhg5EQwmhIwU"
        b"qNLiuK76csw35iFbgAj8G5QoNOy05UkhYnok/IvPbfzaZUQEZDaOfATljHEQkGnDGPN275ZXT5uCHzf/+G3x1Ks9PBHVFTss+SAa"
        b"OoC0RQoscO25U3HF+ycjaxUNwo050JhpFz+2thffeHQnyplHnngsWyDrCCxZVI2b/noWDI02w3AZJcWBEgoNxmAJC/f3/Tv2uR3K"
        b"ShgqDvJAygI3NXCNu17WXfbNhTc9Rz4DZTZWEAbIcOVKH/k9PfKEqph4IJOGuOJ9Daw6psP16BR06QepIzISiIc1REIcDzyxB79b"
        b"34dYmBJ7Dg8vIPmdygLnHFeNE+ZVIZXzypLpQkiYBkNdXMfqDb24/4k9IG54KFwgD6RSOlIgykNYXnWpQr4HcgEUBsbBhU0+CphM"
        b"k/9xy447axs3NUp1SKeCEDyMXLMqxmMIDw+GQjyUzkLOnxZiH106Famc8OVoGUA7jC4NGRyPPNeBjKVUYxwuIJxpGsMNF0xHxORw"
        b"g/ENe58EXM9XcH+3vgeb37EQNg5NFOSB2H5WChyhT8I5sSWwpFUq2YoSjLiTtd1QdXRWLs3/hbyObYvaKrqY6u2rV4N2v+jrxsfr"
        b"anFSfxKurkFLZiUuPb0O559Yh960C10r791kIpo6w+4uC7s6bZj64VMKifaICI+ZGcInL5yOdI52XfmsnDOGnC2waWcqmMfoJqIp"
        b"nUDijPCJmKXPUA6jUqOhpNJcX8YzIua1X3j97lNJBFDiDioEKqiydCm8nVJGPIFbMpngcKT/etgu8JmLp+PUo6rQmyqfCOgq2kEZ"
        b"y1PK1eEEYvtEzB86tRbXLJuKvoyj0vdYmVRAKFeEU6G9R/pAiHG8J3I6hBIDpd0f5MbUwwYTkLfQHxqbNlVsOxFqVBAk1o2Lq6sx"
        b"J5uDILNEvTlwWRgax5eXz8ZpRyfQk3IUSx9uMUi1CZscNXEDHuXI4PACKaKprMTHzmnAx86epqwWIXziGP5eoD5hVoyLkSiwJLDQ"
        b"PAqTtXo4cEpzATBup3KSc37RZ1+7cw55CCulC9BD/GlJULIiuf4OmCatD3EBkukrrpyNvzp9MpJZVzmAtCKEQFwia3lYMCOKmZMM"
        b"2JRgdZgpQAXpGEMmB1x3TgP+76Wz1JjyimGh8SlHlydQG9Nxwrw4LMcXCZUADwJxbuBocz5sWZoAlDNGSC+UiIQ1blys/rb00A/K"
        b"DAY6Dk2nXBKQeG82S7Hwg5MaiQgcihGC4XOXTMetlx2BuipD6QXE5mkBB3/SlodoSMPVy6aqRSyVHzHeQOPJi4O7rzsSx82JoT/j"
        b"wnJ8RZfGTxyOfhKHSGY9XHnWFEyv0ytOyLQqRxpzlF4w7BpR/oFQp9POpf+2dbRVZFHVdDo75RmREP6Yy6nSF0WnSPoPvbUqDHT0"
        b"e/jVnzrx5LoetPfa+0NkEphZH8JnPjQDJx8ZRzrnO1gmGpC1Eg0xeB7w1PpeNZete7MDVgzNoyqs4aolU3DZe+uRK55UfEhACNfB"
        b"0COSuK/nwWHFAFnZekjnnu1uq7JTC1sWt9hB1ZpREYLvYvSwIBIBLEtlqOrDOXv6sxJVEQ2fOH8KPnTqJLywJYm39mXV9/OmRnDm"
        b"MdUUjkXampjIJ6AdnrV97nbRKTU4a3E1XnozhbadaWRtgam1Js5YkMDcKaYi4ko7swhzpCfFeQwJLY52txMGM4pyAibBBLFhIad1"
        b"m5EGALuaVzSzFrSMngC4hhkU7lFJPqy8xXM9oC8jURPTccnpteREHvieXLAZQv7h1vyGAR6Mrz8joXOGsxZVYeniKqXs0XeWC+VI"
        b"GisiJoZuMo4oi0GgveS16liKGhiPcIl6IgCsoJzE0Y1BEYCQQZLiCIAIhRK2iRBoAfO4JnIkGTrRkT+UoCUVs8z6+y9/6IB0grHm"
        b"YPT4CA8HVcj2x0/8zIFAeSVEkZ4qmQiFDS6ydoKuaWstLzt6eA4A1FFq3KFwuTwhvBuAjzPV5hEcZUQABH7SiAwQQ7EDAkoy6fMk"
        b"UoJiRBpyljdVfdE0+jEoApAMk4gA5OGL2/x/ClLFCCI84leh8Hc6DDD0CYE2y8V2x8U+V6DfE3BU5oBENGPNprtb12yqDAeQErXK"
        b"WfMX5B8WCLNIwAkY9noS63IWXsu5SJEdqiDQvv0KKhTPoONFFQHKROXdnUjQu/7CAcYfCMUJxQGA1ZkcXsxYsAMt1D97sV8fIPDP"
        b"HLER62zFQN+7FxFTRzXZw38hgPEFJevJ0ypCaO3NodPT1U4nXSSP9ANsPNIThYDJMYNyxGQFnEGkhMbIt0ME8BcRML5AFoadA3Z2"
        b"m+h0xYAyXbQSVcAOHImqio1B01ANhvAoo5xlgyq0LMTAZ7xh8LvleE26CPLTaYZ9XUBMhujkMLxiTiAVtiV5zaCTePBEWCxp1tf2"
        b"nMvlKINCumGgSrgIES5KuYFHC375HSAUYipXgN5EXCdn+UjhY2xwU5YPsdZohEMLoh2OA9/Fm0/HGyegqfYnge4+UvyAODPBoSkC"
        b"GOwLyCPeIyeQQZWrJYMh6aZa9nSLi6f96+iUNdUaYIdQZFt3XSRCBjR3DKvVUBIF5dSZJvDW2/uwbecO2LaDqZMbcMz8+YhFOdKZ"
        b"scscIuRHIgw0x42bt+GdvXtV3v+cmUdg/pzpypll2+OTuUR01tPrEwARAvHAKAvBZDqyko4y+huBEC844JkAs4ETOoCTuxmbk/Mw"
        b"Kcdmf/xzK+4wpdYGU77M7mabqdCEOlq2YgVjdF6xTKCE12pdV7thIA+g0sinfLpkOo1/ffCneOaFPyGTTStuoOs65h0xG9dfcRVO"
        b"O34R0pnKcwJCfizKsGnLNvzgpz/B61vfhONQwVMgHArj1ONPxKc+eg0aJtUiN4bpa+qpDOjuAVJpH/m00xUBwEQIBrLwi59oUsIz"
        b"gKgDfGIjw8e3Asf1qBJVfmIiZwnEw19Wv9uWIz93x7OAvI8x9ohitk1NGmstL3mU9XTKa8MRPJzJVJ4AiO0Tu7WsHG77+l14tW0D"
        b"qqsSMIjiAuSksxnQaZqvfP4LOOvUE5GqIBGQaCGWv/61N/Glr/8zMrkM4tHYwPNdz0NfMqmI8Ou3fhl1tdVKLFQ88BNEF7t6gExm"
        b"f2xB5StS7ES6uDjzHewR3TCkDick8b49wPeeZ1jcFVykBaLAlxFSV6lE6iCPplgrBXMc5zHYuU+z+766Q65cqbHlw4sE4jSJsRK/"
        b"RACRMMNPHv0lXt20AfW1vvlKi97T1w/LthCPxdQCffeHD6GzJwVD5xVRzshapmTQTNbBvT96EFkri0Q8Adtx0NPfj97+fkUg9bW1"
        b"2LZzOx74j58Fx8cqqxjS3Ei/6uw6EPnqu4AIDKYpLsCFgBMGrn6D4alVDIt7ADdMcWD/eo24piBlUAUTyWb06xTalodc1oNhXIRQ"
        b"+Fl545dPJOTL5uEPlNBwqgOCr+jMaSFpQTs603jmT8+hKh6H6/mVYps++Fe46ZM3YNqUqcjlcohEItjTvg9/WPsSIjThShAAyf0w"
        b"w8sb27B1x1tq5+esHGqra/DZ667HNZddDl3TYDkOEvEqvPDqy9ixqxMhs3JEQOtKHtaOLiBbJC+CRIApGSIwIEISN69j+PEz5A6G"
        b"EgOEcNqlpV6jCIHqLeYyLhifCT28Sn7mH09mLS3ucETAIQYIoHLgF9yEHmbY1d2NvmQ/QoaBbDaLM085DZ+7/ko0XXwOPn31dQrZ"
        b"xHJJKSNElXsyd/ghUKYSsG3nW+odmqap3X9t05W46m/Ox6ev+Rucv/QcZLMZmIaBTCaDHe17YYQDAhglESjke0BHp8qzKB5VlAIG"
        b"oSgUxooXgLteZP6O5/6OH9lLuQ7HJrZfDy30eDlEQPmJiYpyPUI+1YbWOHhPEjVd7eqMXMZ1kbRtNM6eC1sCnb3A7BkzEY1EkEqn"
        b"kMlm1e+FoJTfYLBfYeg0aF7RSBTZXA6pTEYR2fzZc9DbD2QcqLHkPA8Zx4FNLc56u8G6+vzCBeahkyHpkWRxtHf6pmZxEauqn6k8"
        b"xYfWZNG8gUOYgVl6qDghTuA6ZRMBJaNVlZsIUg4Q8s13dqP6D6sRenIVFrz9Fj573LGYFolg+THH4BInA/33z0Pv68WkKQnccOXV"
        b"mDF1Gs5//zJcesGFyi8wWAmjX+nMflWcIxLm8AIHDn2oEASxbPquKsbVjs+vG9c4MlmJ8896Py49/yJMqa/H317+URwxdxqQTEH/"
        b"w4s4u7cd1yxahIZQCJ867lic2r0Pxm9/i+pnnkJo+w5f+RrhuqhEUsff+Q5VDSqeYEdVTAFuY6b+fZy6azsED1ONxtFzwMFEwM2S"
        b"RMC6OuRv4jF8KJUerRVA5gkQXf8qQm9u9mdO2r5KGNAgHAec/k+72LboFCbSx58CtmAOckmBUIirrwYvml8eyMMvHv9fvPHWNlx8"
        b"7gU446QFSGeCnL0YsO61nfjFY7/B1IYpuPziS2AapnqOGpEyNX1bN5sViFRziG17EHvpeTArB1VFgsbmuuDEh0lg003Euz0P9ux5"
        b"SJ90aqDGs/KR3+U/ojjyiYg5OM9gRvV9iCbWw9tVh/APa8DSnJSCoBXVKIEKcusGkXEnPOtC9u07XiIiIGIYGHNXh1wTjWFJZjQE"
        b"ELD96MYNCLetB8IBKw9kC8lgyq7xC0tRlIs8IP4i95/xfngzpkPagSsyWDXa6YkYxyOPPYm7f3AvQmYIsUgU111+Bd536mnKlHtp"
        b"/Trc/7OforuvBznLwieuvAY3XPlhJNP7Tcm8PGcmB+/oUpxpgDKEGBhb/qe/KkHNimwG9pELkDr5FDCnNJssF/mUCehRBRPehxm1"
        b"30HY3ArhVAFRG+wdE+YP68aVCCgxlRLSRvMCSJ1B7+lHmHZ+yO+jNxjyCzvA2in7RLnBBOKvbURfwxRVSXPIg5Vpu7ezUzmM6uvq"
        b"lJ7w3R89jJ//6lH1rO7eXpimqczLrp4u7OtoP4hl599JVkHs9Y2A5wJGyOdE2D+2A/L980pgOAJz+1YYM+fAmVJflAjyyCeZH7jU"
        b"SyLf1Lsws+ZbMPVd8EQcTHOBLIec4cC+truyRJAXB7qR1wkuZC0tL+WdRaSvhEZ94oXO1e/bA7gjSJwPdiFP9kHv7YPqpjOIcMg5"
        b"RJ65S849B7NnzEJHd5cihOqqKrXbSbFLxOMIGSa6e3tQW12Lyy76oELEgWOQlLMGLZWB3tNF0Y98abjyQAqYe3cV1QUG7/xykB/S"
        b"92Jm7d0w9d3wZBwsXyqYGFaW+UTwsW7IGInK/Yf0KkIEjBRD43/l3zcfQ8inQBI9PhSs+6h0D26p0vIjByHAKCZaYOfSQYzpUyfj"
        b"a7d+CWeceDJS6bRy4JDvgGx6+r0/lcTiBcfgrtu+jAVHHuG7cw/YzarKIDjJfOLNIwbm6wty9Gw/bOzCrLpvwNA64cnofuTnIU8E"
        b"M31OUHEicJSyMxmc/0J+4W5KAwCVha1IKFjk5f5IgXNIk8RGga8YQzYrMbW+Hl/9hy/ixfXr8eK6ddizb6+S2VMmT8bJi4/DGSed"
        b"rGzpggElEucCECSaVBnXkfJUCRmKFCDQcpHvwZMaIsYOzKi9Bxr10ZZUNawIMQ7mBBUXB9Bh5xxE443IpL7IWlq+wro75R5dx1TH"
        b"KX0qqDQrZ9D6+pFYs6p8EaBOnrrwqmvQv+TcYfpS+NQRjdCRLfLh79fj6Gcmuz/cW+QJSgxUPft76O27AXO/DlASAiwn33cOnIZ6"
        b"MDevxPrWCpl6w+58cESNtzCj5h5wTkEwqhJbxrvpkogEe8eosGJI2q5OzY77YMpGqgdkjIoDKERKuDUJ5OYvBIhd7i+NWvyeIAkx"
        b"23g8JBXiKTEIlRvHmIoW9qUEMjmpSs8kUwKpdKDMlSwA5DOYTOOx1K3Y99KUCoAwlZcF5LKw58z3FcBByKfbO8vZ+Ywj4m3BjPi/"
        b"gGuZ8pFfgBNUThwwBs8BTLMWNruSls0YtSOIFsaVyDQugrVgEeDYPn8Mvjvg44cwFbtJn3Im7GlThjWx8kCmnca5f2BDHdogW5qX"
        b"PT53Ui3Sp78P0jCAHMl1Waier49hKwd77lFIH38iGDXCI10gcO8S8umS0sjXFPKnO3eAc2o1TwXjRrh9x4wIyAJT3rQPkgjIMIZI"
        b"pdzBVDzT3L1HmYR6b7dPCPmHKxdrGA4FgY5aqLhGucivzOB8f4WWSiO85XWYe98By5H8CMYXOK/c6hpY846CNesIv0he0AOEmFbe"
        b"vTss8sWbmJ5ZAW5YYDOnjBz5YysOfHnpuXuJACwqRFSxeECwyLRwWjIJLdkPRtEQcrZEIvASNfBivtKXZ6vjCjRRjSmzk6dt6P29"
        b"4Nm0wq40Q/DiCXiJhCJkysRRSmQgsUjhs4IDpaV3/lZMt1rAvSRkJA4+vWr0sdax0Qkc8gQ6nNP5AFQOAtaqbPvB7Eq11iA336Ad"
        b"dzhgMEfShhjAtNtJQR8QD/64CfkkNYpJHIV8aAiLbZieWwENKQhhgkc0sOk1o44ujgkRSLg0HVUYsqKQ9/xRl1Z70IfYveoNNYyS"
        b"ONYwoI9AjemAMRJXGjwH+Jk8wyKfEfJ3Yrp1u0K+BFVZrXDWc+V0At/NymSP0qUwVjBUwZqIBw9Y8THyIIFzaCbPAbdTqUemIST2"
        b"+MiXfeRaCVhJsPErucMqQQR+uhQlE2+eoOUbDj9wTqlrKnJcwmKkOp8cuujA9FwLNNkZIN87uKxKRQc3SiKgg0e+3fw4sf/xP50x"
        b"wYFzIJmGShwphXw626vLXszI3Q5d7IFAdIi2H+SEjcUBlEMnAr/FrW2nIJ2fqSphE5EzHy7gzJf3PT2lKpwQD6VkqjSmWf8EU+yA"
        b"YLGD3bt0v3+SZ2zE36EQgRQewmEO6T3E7v3qW2QF2JyP0hs49B0TpyiYghF5pz2gvaOUly8/OQfTc/+EqPcyPJYo7tunolNTE2Bx"
        b"cj+P0cKUax1QmTEjxOG5b4OLE5BAr8646hBWsr3aSEGlqU8QrkIeu3IPvkrpH9wgIii8+4MWl+CYZn0LUe8lCFZdHPn5Z6g8zTGE"
        b"cgJIJOqp16OUObju5ey7d3RTTgCdSPSoS2Cldi0t9LYdGSSp39ThLBQUZBvPnB5BdYIqnpeOd3Pua/ylzD2l9DENDda/ocpdA5fV"
        b"FEe+GkPwkypMjjWUIgJPNbPxU6Oz9kfY9+74o6TWva3LPTpw6gQu+kOLBg7uHmIAbVtSePHVbl/JPJyiQHnvJLbuSOPcsxoQDtGB"
        b"k8KXcu6beqU1fk8hv87+JWqcR+ENh/yBcTBI2wOjmrtjvSEKEgGTMLkHI6Qjk/479r07/stPCVvup4RRD4NKsut9nTn/FG6YWgLi"
        b"sAJNK5lyFDeKRc2CPnwWyP2evlLPEcrRU+W+gHrnYQhGx/PLNJ7ofa4HablgsXHopTMks8j4Ya3HRVyHnW5m373j+0OTQskFbFWK"
        b"MGmHHTk7jvYOiypZKRZ8uGhAKeBCYsbUCCbVGUWjd4wBff2lIsR+TD/s7USD9S2q7Bf8fQQzo0upKiURwHhAQARilu3y/5PT8X3c"
        b"y755++0qLbyl5QC2RcGg10wTx1h+hHbUpECHQfuTLtKZypVXP1Sg99fVhPIJwAW/z1l+YkcxjV85zXQLM9O3wnC3QzLKfBqh60Qd"
        b"AOTgM2rGTTumQiKRmrBhZbI/Dzd8/yr50yYNTa2U+X0A5focIMigrsSpLNpJVXEdNQnKNjv8QOMplfzT31/qbr+TVGzrg9BibwDV"
        b"1b68UPGMEQyCrrU9yKwztuZgAEJIN1oXMXL9ucfCq7uulrc1czStUD2Rh15LWMpVkijzSROHlH85BlBsbqpES8bnAAVZv6TwMIex"
        b"8zmIl1ehPVKN2EyB6BGATgdY84r9SNYuaQFEAGMIkpBfG9atZO7ZsO010a5HU7Nq7l3oep06mFSaKR1u1l+uvpIKmlwW/FJj4Ok+"
        b"mBsfDHIIJZJbgexehqp5QGSG7+OXROjlzJdCz1kbLEfNFypodw8etpBupDqkOyn75Vwal4Rn35+RspmzEi1o6Sha5rC38xhnyMt+"
        b"y89TKQB+01zz9Z+DZ/YBnJQ3qX54OaB3I9D9CoNLhaTJhVYuLoWEpJakY7DeUko3kjB1N+dushzxgZrZ3+sZDvkEVKm0P4iCTgSR"
        b"PS5A8yW7X8oirF/n0Lq2QX/7SUgzHmzzIKGFwugGYLUDXS8wZPcwnwiC74flAtSqPFvZzhPUTSQSN3XH8t7s67QurJr2/Xa5skkb"
        b"Dvl5HSCZz9X8c2DdlVIMi8p+AgaYW38F5toqTeyA8C4BrRVV7XKBnvWA3c+QOCoQCcMpiEJC9GTAp1WjUsiPxk3Nsb3tmWTmgvqj"
        b"H94lJSG/vBpBep4DVBIORzCo3DkQ0qlaR2F/vwT1iuV9e6DtewHSiOzf/QUuVe/UgdQ2wE0z1CymlPth9ALVx86GTObAEqocCkaH"
        b"fENzHG9nJpU7r2b2w9tGgnwCXQgkK838ye4e704htKvLJTzy9xc0eqWA5BrM3c+A2UlIs7o4AQzc46sIuXag+yWG2uMlNKIbtwQR"
        b"kLztSoNFjENuTJxHvu2Id3IZ7/yaIx58c6TIJ6CCAf0BEVaEDxDiO7scJNN+Kbbx0CzI9dxQH1I1BoZrUUfmKWX2FuQYjIM5HrQ9"
        b"L0Aqxa9Mhw8RgQE4/UD3WoaaEyWMeAkioL95AqIjdUiiwBNSxNTOF7usvtz51XMf3Lx69RKdsdYRV3vUOUdPJSq2EhHTeQuKBD6/"
        b"tnvcOoX5Ll8oAljynnroqspYkWuD83wFw8OSym9xsO63wZM7Ac0c2c4kkUCHjrJAz8sMtUQEVSWIIC8KulJg9fGyRYEnpYhFDe64"
        b"Yme2N3t+9byHFPKXLXv6kEp9kh+gR82zAvufFnXnLjoCRRW6xi8YRO9t77LQ0+ti6pTCQZ/8dXTimAjmYBEl1SnieNdLYG4GrlkL"
        b"LkcYxg2IgExFIoK6kyT0YYhA9maVGGC1UT9zqAS4EjJucNZly25h4byGeQ9tkbTzDxH5BDpc9FFyeCUOHKmWcdOi2LUnqxpHjgsT"
        b"oLCvB0yqM1FTrQ+b/EEEUPxBwOZ2C0cxHXGWRkqd4qWaPSPnBJ7t+wrqTpHQo8MQQWfKP4BSE9l/ZmIIULZ6XJNiixbRPrw1vHHj"
        b"af+8JZD5oyryqzMDSceBR8fuRqO95w9NzpsTRSymIZlSrc/HBeg9UxsiME1ekgBo5xerYcGoE4ct8IPdZ6AqOwWXx5/CSeZrsKUB"
        b"RzHKkRSV8Es4Kk4QEIGSKF4JIuhI+eljxAmGsM4A+Xjd0nDBvjrstD1bOXla2zBa0On0s8ZVQCjquqOPCNIiT5sSwoypoXHzLLF8"
        b"6tcwuZf5+k+skP6iAXtTjqob2C1m4ev912FJeC2uiP4vangSaRmBNlIiIJ0gBfSu98VB6ZQk3zKgFq1sUly5ookQ6CxNlSbxsmXg"
        b"4l012B3WkNCcNCcnz8qmUXcR1yMR9Nk5FRCinOaKQP5g8HhC/hRXqe/zRDL0QkkhX87QmbSRdSSihq024VO50/GaMxd/G38UJxiv"
        b"Iykp81eUv0MCh5HVBfS95vsJSpqHRAT9OZU8wuvjcCImqpiH57MGLn6nFp0uV6fbc5K9o66f3DhqHsvjcaSphzLF8SvlDi502Gas"
        b"P+VAUJKgWNQXRACe5yd9kuRPsDQ6vVrc1fdx/Ca7BDHmh01GVMkv8BNkdgHpnYHbuNQqBx0r7d19qOrsxe+SBj7wTh06PToaT5lJ"
        b"HILLfZXirqT40e7vU+XL38XRgMD0LvqllEBv+kDWRZlAIeZAZy4eTl+KH6YvhckcpQ+MmAgMILkFsHp90VCKCFzOkWAeftlj4EO7"
        b"a9HrUT0EqRzS1N07BnaIBZmKdg9HspIcYEJCkKdQmMiZ+nt/1jnIPUwMn5aliqUVF7gveYUiAG2kRBAQWX8bKyoGaGiE5ARcPCTr"
        b"cBnmIMuoIEZwfEs1jaLSuHIfKgS+6cfQ+27nAARFHUQIag0FxSoPuk9Jfo5qlsIa6xTcm7rKP143EiIILAO7D0i9dbAoyDOnKni4"
        b"S07G9WJmcPjsgLN7jNiYrXFfB+hYJCvTOFKgiwigkhxgvImp3IMfhW+GEg+W6+cxFvNiegER/ME6XiH/7+M/V0088lyiXFGQ3gmE"
        b"GwCDQg2kmFKFDghQ7/AbxXTcKyeDBy2khkgtRj0FEhy5DvXfVlSEABhHx0i6h5cClUxD1TfGMbSsWOdoU9CY39J9OPCJII1nrJMQ"
        b"YTncEH8EWRke0XvIwZjcxlB3ooRgXO36bnBcJ2bi17Ia6rTOwWqCXwDR9kSIzqATNDVWhgMIAWpMUplGiKpen4dMdpyyglWSBkNN"
        b"whioQVUMio1H+lFgJCK6Kinr72dZkggSLIVVuTNRx/uxPPo4UjJWnrMobxp2MGQ7Garr01jnJnC1PAIbZES5ZomrFBok0zR4rpdy"
        b"HSfQAVbI0faPVwSgaeg41O7hg8ankL9nn4XnXuyC6/mMcexBKvY9a3oEZ55SVzKoodrFFUkB45xh9uQonn+DujMND6QTVLEMHsmc"
        b"h2laO94fegVJGS3PWcQ0SOkg+2YGouFcXA6GzaILBvPgFBm/qrWtcTaHs71vncJ69tcvqYASKIB3AhY6qieSHvHGtpQ6FEK9f+j/"
        b"2hh/qEycaXDs2JVBV7czUDyyWLSykGhiYLBd4NiZVYiH9RG1rCGzkMzDnd5UhJldWilU+WRc5Row00R27mexxfgs9kkXnImCO58N"
        b"KKlSvKc6iisTkQ2MtbhN5AWsgM6mOIAJ7EynB5pYHTpIoL7OxPa304oIxkMR9O17iUTcUDEICgwVHFrg7qX8i6GNHBhFCV1g5iQT"
        b"px5Zg99t6lTigJ5bCgjZxLKTIqaI4JaqBwvjhBBP4GbBpAd36hnILf4YQjUz0JVrR1ImIRQqDrx3IJ1CSrwvFsZ5iTB6etPP058a"
        b"K+AFHCCA/hy2hU3sDYUwzbYVDxtxZDDval0wv0rtyP4UJYSMnyY4e2YUsahWsoAjVaQPhfyI4EFnBKEccLjkpCl4fXcK7f0WoqY2"
        b"LBGQKIixLNbZRyud4OLI00jKuLLdfY3PBXPS6lqv9mjYR/0N3Bln+s2ibIE3BRWOtsBZWJl9g8fjtwgELqyK4ISwofX1ZyWD91t1"
        b"wdLKVHbRV66U2owZLNPVKV8Mh3HxoRIAQX74R88nn/n4gFrmwMmjjoAPEwyKRvzzAEOBBYdEE1ENnzznCHznie3oTtlKJPjtAwqr"
        b"hb7ZSERg479zy3BaqA016IbrCrXbpVkFd+ppcI84R/2UBokA38CjHglrve0D7qb8CtLikxiKc46LE2HMNXTPCRtcZqxXEgtyG6hD"
        b"KGOq4N6oQW9qCnAl8N+c4xKq3j7afD6bCiweBhiO4fi9i6ljKJClrGBWoDyMA8yaFMZNH5yHf//DO2jblVJ/pxZ4qrt3gCeFQqq/"
        b"IEgJpV8cdIg4fsnfg0/UP4VsVSNkw/HwGk6Cl5iusMqI8yhnE4cODb3Swx/cbco3TOSVHw4hf7qh4+KqMGo1jrQnEDN15mbsB1so"
        b"Crhace6KNPtlzc2St7QwkdwjG1wDW7iGRMBG35VJ4oRDOhBCJV9ZsbwBIhQ6UCqBl7b14Y9v9ODt7iwylqeQTRaDTgdHdI6IqaE6"
        b"qqO+ysTUmgjm1umYPwnwItU+0kknUUkewaECv9IAEoxjjbsdH08/AM78gye+u1fihIiJc2IhFRG2BIQZ0plnObs1KRfefcwXU0FK"
        b"cmUCd/47pUYxge5O+YOaGtzQ0wOXqc6U704gDkelYJIlCkLknWIR0xcN3SkXPWmH/DAqkTJsEPI5oiFN/U79CviARzGgorw0HUJp"
        b"5OOrYQyfyLTiSeclaCwKV3qIcIaz42EcFzJUG7uAINxITUy3+9I3fGPBzQ+slCu15WzkXcKHIwBVLay/A/NhYCP1fPT84/3vSi6Q"
        b"h/aOoPZvCZGnGluStq8FZmfgbFJcXw7+fX9qtY/vwktHil6MMbzq7cNV6ftgB82A54cMnB0LoZ7a3VFFF/+snxdKRDSrP/ts4pj0"
        b"krbWNta6fGRp38OBmnqgUPDqBvaG4+Db1TWqfOwEOd87NkBImlTntxAqlRVN3te8gkilfjK2VCe7yGxUJ8UHyg77Jex9y6fUvpHK"
        b"1v7X3JOwhYVaTcMHqyL4cCKCaup1nEe+hNBMjbuW3Q9Pu66FtcjGCrh+D1qHgWFR73mAdXYipnG8HAljfjoNQSX58S4FFpiuVAi6"
        b"dIfPCrxLaW0CNeB4yF6HO+3/xJmRBE4I64hxhtwgDiKFlEzn0gjp3EpnL/nWwlt/07RypdZaRjfwkcLAlIPz42zyZJYUDq6mRtaG"
        b"oSrLvWuDxDJwXzfU++Zh0YyhCrzHEQIRcOzkPXhBX4Xra2vw/pgJ6rKbVWJmwOPncZ3DCBvcSVvXE/KbVzfrY4F8goNPxwUK4b59"
        b"8mOTavHDZBKe5ymP67tWH2DBzMg/0J/c3w3kUP1YA+WBA0WSWvQkwtSgz8JPsj9Fj2gHlybI5TKI6UhCvhE1delJ28051/3rwpt/"
        b"RshvWba/qFOloXBoREqdMea2t8vP1FbjnlRKcQKqLzPqLNSJDJwaUrl+5RA6Pk4u4/yhmYGFGnLcLY9o9VVANBR0IquACmYapkDc"
        b"5LCZhR/3PYKt9naEWUSVmfY1cFWhj8q3aOGaKJys/bqXta7/5sJbnhtr5OenUxBWr5b6smXM7eyU14ZN3K9pMNJpZR6SP+Rdyw0I"
        b"gqamykIgnwG5jvPpZIPbDJFFoAJeZCFQnEH3Ea/S67hv+0co2cLtx8/6H8Xb7i5EWASe9EjhEpIxyTnTzXgYTo58sOL7/T0dX7n/"
        b"lLv6xgP5BCURmSeCffvke2NRPBCLYWFvj1ocGti7nhDYoD5X+c8B36lfBokKVZlVKpNQV0we2GRvE48mH0Of7JcRhJgHwTVDY3rI"
        b"AMl6O22lADwKl3/zGws+/yo9hiJ9lTb3is5xuAvyRLBli0xMa8CXGcffR6OIJJNKc6ZsprzXcDj7510J0g8RDGRvUbsk8gl1I82e"
        b"tp/HS+461fLW1Ax4woObc+E5bhdj7BWma//j5PDLexZ+ngICaJIrtVY0iUp5+cqBshBGAaPly5VTE52dcmHIwI2QWB6NYRLtCjpv"
        b"T/7/wHeQH/wAUQQ75M+WOOT+OFAe2fn/q851FF+gD/H1fXYOa/tfwfP9a3M9bm93VI++A4kdksnNHHwr/bQce/N3G28byMIiE6+x"
        b"aZNUfv5xhvIPufh+ApVGTv9vb5fTwgYukgwfgMQpQmBWLAZOcpBARedc374OijeIIYuXf/cBnGM8xYo8MMA3eGwHEDEhmeY1+KPa"
        b"H9qK+KnU7luOcF9809624bHUU9tfzr66qyE+effSyFmd10y7oEDsEWiWzRxrQB/R0jL+iM/DiBc7cBtTOHJARkkpI6luHCkY5gqJ"
        b"o5nEAg+Yx4GpVK2Vc9REIv7CkYKk3Kdifz1B+tD/g8+Bu6xEet5QM22wRl7o2sDc9v9LfSeZH5/JK3EqmTXINMqfIyACzmaVzkNp"
        b"WHukxC6uYYsQ2CYZtkQ1vBmuxg6ymgovGFgTVvLGNZvUu9s62uTKppUFizYeDjjk3RZwBNX0fTAxDL0muRuT3Aimc4EGAcyAwBFM"
        b"xwwhMI0D9RJKjNTR2UQpETZNaGQ+5ZETpKvvN78Gt+AZ0o5nsAt+gDiC+/JOnjyhkXbvuqpSeo5KBjKJLsbQJSQ6OMduIbCbadgF"
        b"F7u0EPZ4HvbV1bGiJaXJf7IGYB1ola2trWjc1ChbVqygmvUTAtHFoCLsNu9Gzn/WrAGWLiW/wfBJC+Rz6OpSyI8zBqqXQp8qDajy"
        b"PCS4BirNHSfiYAxhyRBm1JONvJhSRUw1kT85RRViBAT1xJQSOc6Vk428rDk6/0jJT9DQzzwkmQEqEtunaejPZJBpaEC6nF1J+tBA"
        b"DsUgnWCi7OgJBUQYwYfTDiGLghBOvwefCaUYyiHjDH5qwd8n1FjfNZAnEkpMySNgCLGoz9q10hj8/zyChn6GXjP0vjxSaSfLALH5"
        b"z+Fei8MB/w/w4JdccMhMTQAAAABJRU5ErkJggg=="
    ),
    (  # 256 × 256
        b"iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAABzZElEQVR4nO19B5xcVb3/95xbpuxsTbLpIYSeUA0iRU1AiorYeBsb"
        b"FtSH7/n+FgQE9b23WSuICJYHiIq9ZZ8+CxZqEgERKQJpEAghIT3bp952zv/zO/fe3U3fcmf3zma+OmR3ZnbmlvP7nV/9/oAqqqii"
        b"iiqqqOLwA0PlgAFS/b91Kdjate37HPv8+S2ybSmkf1bqP3I8DvTwgfTvQetS1oqlGLgnLQDa/fuBpUDb0uA+sOr9qGKokKylZZm2"
        b"qFXqra2Sj+S6tbRIbVHr8uDvg8VaxUjBICVrWeZf0xFdTykZ/S19Bv1cYRvQhES8bgAtkKUrtMVYLNramNjrRXbBVU+npe5O0kR6"
        b"MrhXJ6RISa4zwIPGtKwD2amZbkdDX1O2/eY5xb0/nhTB2gVg7UsgqrvRkMBaWyVbgRV8Zdu57t4vLrzicSPVZGYMTzabkjVJjozg"
        b"MJQNJpkrOboA2clkTWd3d3fhiTtOd/b+DFIIwf0m66BqIRyOCoB2emUuDhL6N1+zrrbIxWkS3iu51E4Ek8dBYh4Y6iRgMsY0xrT+"
        b"z5DCAyBsgDuA7IaU68H5OimwXkrvb066c/XKtnNLB/vOKvqvJlvUukLbW+jPveqJI0wt+SrBxGmQ7ATGcCwgZ0iJFGMw6JaAhcaa"
        b"hPA8wQBbQtoM2CLB1jHG1gshV3FPPnTP1096eW9lsHLpYg+s6iocFgqATPT2dkaSq3DeZ56Zqnv8jeDsQim8xZzr03SzBmAMwrMh"
        b"PJckHVLFAtSGMWjHYEz9HxyM08MA1wxIKeBaWSGBjZzzvzIp/4y8cfdfvnVsn/ozMmuXtPP29iX9x3HYgq5FO3j7koF7cuF16xZC"
        b"eG+SDBcyiVO5kUhrRhLScyGECynonkhI/34MuieM0fJi6h/SC7q6H/Sv55Qg3FIeYE9ByuXg2m/vuf6EJ0MLgFyE9haIqiKYoAqA"
        b"dt/BAnfB5557LXPdD0qISwwz00S7iOcUg8UFtQtJSO6vqD0OffDx+xHC8CfGBKN1ST8xrtHio4VLisRzSlsAfpfk/Kf3fuX4h/2/"
        b"kYyCi4enRUC+/YDgL/rEPxsS6UQLA3uflOIsI1GrCc+BcC1IIQRpYUl3Qir5Zge4H3soaLoRQdSQ/uV0S7ieUErBsXIeA39USPG/"
        b"cO2f3XvTqbsG1klL1V2bMAqAAj9LwRAI2UWfWft2Cf4xBrlYN9JwnQLt8iJYKFxtItEcIy0/Wni0eDnXTa4ZKThWlrTDXYLx2+79"
        b"8vF/DuMEbW303sPDDFW7bSD451/zxAyNp6+QTHxIN2tmSSHgOQWlhJUCVsLu35TRQ5LRQHpEgEGn+6GUQSm7XQI/Eiz/3fuvP+PF"
        b"vY+xigpVAIPN/Qs/vfYCprHruGacR3LuOXkSedruNd9mLDOUvco8tfDMGkhBu5v7J8nk5++9fsGjex/vxIRkra2+xXPBZXfXsFmz"
        b"/x/APqEnaqYLpwjPtencGWEM1glpA2UhaJqhcTMF1y5QHOcbVnHnN1Z+49weUCZHpXgPD8U8gRTAwEK78FOrZsPQvsQ17b2+6ZdX"
        b"lgDZ6OU/jgMcnZQeg+R6opa5TtED5O0ecq33X/+qzsBVURYJJhAGu2AXXfvMxeCJr2hm8iTPLpJvP3aK+CDKmWmarplpctfWC+H+"
        b"573XL2jf+9irGD3KepN9c9o39y+4bk2LxvWbuJGa7RZ7yRkQDIPC+OMMpQgY0/RkHS26Z6VduPKer536F99tWcrQ1jYhYgOhOX3+"
        b"FY/Xa03przDN+Hey7En5BW5XLDJDvocAoRkJle0Rbunnntd9zX03vnpb1SWIDmW72S3LlmntS5Z4i96/PJmc0XwT1xIfpVQdmZYk"
        b"aIglVBzR5UZSp6iVEO6X77l+/ucmys4TujWvu+7JV+q85nuakTzZKWUFnet4WmEHg6Q0DgAjVcc9p7gBjnf53TcueDA4lwlnnU0I"
        b"BRAuNMob63rqJ2Yy8xq71OfRbhrXhbb3oqONUE/UctfO/86yCx9e+fXTOyo3LkBR/nZOCvmCq5+5jJvJWznXal0771GGBJUAKZVi"
        b"ltKz4NifvPvGk29XcYHDKGBbDkQujFTMoYT/yidPSSQy9xmJ9GucYq/LAK0ShJ/gH6fkbinrGomatyTM9P0XXPXUkXReZAmgsqBi"
        b"MCT8r7929XVaMv0TSK/WtQuVI/wExnTPKQkpvISWrL3tgmvXfIWySa2tSymzGA+3pQIR6YULfbMLP736DKYbv2dcnyrsgks3D5UK"
        b"KV0tkdGFU9oAz7747htPea5y3IFBkf5rV33FSNReR7u+FB5njMoqKhAUGmBc6ImM5tm5b919/YKPH26p2ygR2SIIzePzr3nmdE1P"
        b"/JlxPtlzSjH294cBCVczU7rw7M2yVHjdPTcvfKEClABraZGc7smFn37mS3qq/rNOKesxKeMT6BsxVFGHZybrNafU+517bjjp3/z1"
        b"V+3xGC4iMclJA6vg0qeeOpZr5u/5RBJ+AoPu2QWX64k5MJO/p5JlEv7W1tbYujSLWpcrhXzB1U9/Wk8Gwo+JIPwEylswzSlmXSNZ"
        b"/5ELP/3MF3z3LHqXdqJj1ItBqsAe8Lrr1jVxYKVuJBe4VNpZSf7lUCGlqydrddfKP2QmGy5KrX3Yal/WErua9dAVu+iap9/FzZqf"
        b"e57tQXgTRPj3gGTgHllnTqnvP+698ZRbKzdQOz4YpcaUbEk7OPmZmvTuNBKZBZ6Vdyek8BMY0x0r5xipulcXC523kBXQ0t5O1zA+"
        b"gkXW2BLmnXfN2ldAT94hhENteUFXzoQDk9LTXNfyNCN1ywVXrzqHhH+k/BGHI1gkQb+rn/6cUdP4RafY55K5jIkNqmD39ERKd+zs"
        b"5ffecMoPYxMPCHot3lx4tqakyUc0zVzgulTgM0FcsQNASiE0I8WF52zgrHTWX758WgeZZCxmllkcwUe701x09TNnMTPZSh1dqoR0"
        b"4oNRgZrn2pIx8xvnX/PkMdSxFoddh9qaKTVW4O6Nyhpziu5EF34CY5yKhDwjkTlKuPo3ySVbQtciRoZZXDGyRevTOeHMK/+WEhy3"
        b"ca4b0nMnqpm5LxhjwnOEbqbqGNNuGUg/jePpB1bIhVevfr2uJz9CQT9MlCDsEEBup1PKuppZ884Lr3nmrco9a/nVYXP+IwUfzU5T"
        b"l6j7uJlqOEUVlXA+VjugzwYiJZF8eOpB5aJStfqOmclHGQ6yevRE7RsvvHb1uynXPm4LjhTy/DXygqueqoGGr6tWfUlkPGOlkXxG"
        b"ELnPPaH7MXb3RErB1SFo/Ka3tG5sUAHaGGdq4gA+spRfi7jwc6tmA/iMaxWoqafsCy0QciIHYYzrjBsJrhspTTfSmm6mVY8/Y8QP"
        b"qKgnXEUFVP6DYlJ4tPi/uKh1dUYtuMA6Gku0LAFXzUpc+6ierD1BNfaUvepSCbwSdjDOmGYw3UgF90Q96J6QB8IG7l15lQGds3At"
        b"z0jUzysUcp8kV6Bl7YLDwyodIUa4SBi10F+rm7X1QthEAVm2xSYhKbYAEnIjVacTW5AUXq/nWs+5duFB1yksd53iI55nb5DSyzNu"
        b"MHofN5I83JXKueCoPFVP1h1plvCvasG1j20u2lfIEBdc9UIz4+xqzymq+FfZvtC/ph4pWyNZpxlmWoOEI4Wzw3WLT3p2YaVrF1e4"
        b"TvFJz7W3Q0pbT6S5nqrVSXGr++n3/pcL3LVzEpx/7Pxr1s2IS3wmrtBH0t77uuuemccEu9y184pvq3wNOWCGmdE814Lrlv4BO38X"
        b"k95DXJera02zt73tRDt4N3v9x5437YzWYAjrdLvY8xow/hbdTB5PLQiunSd7uFwxCibcElkBV1183TM/bF/CiMiCvmtMTF+fi3+J"
        b"4Mbqj+iJuma72Fu2AiwSfK4Zmm4kyd/Owsr/SUpxv6vhYc32NjVsXl8KsyGUGemZU5+EOX22Z+fPBtPPhRRvMhK1DUQtVrauUAYu"
        b"PNczUvVNrtX3CYBdu3btsqoCOACGJRBhuuuCa5650Ug1XO2WypP2o4WmGUnijocn3N9KJr99jjl/+XD4+lpaV5vZEr9YMvZJride"
        b"65OKOmVZdHS8tBt6du7f775+we1j169O7gaT51/7eD2TiTWabs4QrhO9ReaTdEg9keGuU9gthXeb6/E7l9+0YNNwPubizz430xXe"
        b"+wD8P81Mz3BKOUH84ZErZgnBNJ0CtR0J15p/19dP7xhLpVxJYMNdbBdft6nRFr1rNT0xTbiOiHqxkYlIJJSeU1onJT51z/XH/2Vw"
        b"p2Hz2t3Sr77bPyco+cO75q9ggymtL/rs2g8D/ItcM6e6Vj5yJUDWim6mmGcXn6zfeOKrxqomvb/i79pVH9bM2u86Vp+glFjU58aJ"
        b"wFMzadf+iYD4z/tuOHGzerFV8kVYwX1e/7AvP7wnwa+tS9kiLA7e4yvw867850wtYXxe1xMfFK5iF6b262iPW0hhpGq5U8z+x703"
        b"nnRrlURklApAcba3nete+OlnLtcTtXc61EsORCtIAOVyNdcqLPMYPnr/9fM7KYpLgZzhU3OFTLe+MF50zT+Ok1rND3Wz5kzHorr4"
        b"iJUApNQ0k3me87p7bzjxgTFZcP6uhguvXfVXzaw5x7XyJEhapMKvGxySFaRwP3bPDQvuHBVtdzD4JVTOF1z7zGVcS9zKgFrPtSNV"
        b"ArSWKBDpOcUn6l9ce2aVXXj/GPIFX0EDG5TG0JYE6x1lEX678I17bpj/DhJ+NYKqrU0EfuUwv5BJXwCZpM+5+8YznpOOc75nFe9S"
        b"NNeCwvcBk30ED0h43EhR8uFSjAFUYIsxeeG1q04G+JmeXSSyleiUGgm/ZlAxTbewi5eQ8KuRXq2tPn34SMxpxqQS/mDE2L03nPxT"
        b"YdtvkBIdlMURQojI7gek5jlFOsiFHXOOP5XWQQVyOcREAfhMPvL8a1fPERCvpQvLIjT9lQ+tdv7c7fdcP/+TanG3yv2OoxoJ6HPo"
        b"5t9706n5Ort7iWsX7kumGzQdzNMZR0QPzlwbBtde//7WjUlf+ZQvJUjjuuhfCVxiJGp1CS86a4M6vLhOAluUTuHSe2869QGa0ajO"
        b"KQpuROYrZ/rMe7920sOWU3grJHKmkWA6xRoiuR8aERx6qVQ9qzH0N6nvbaGhpVUMxpAWKN2olW3MvfDaNR/UzfT3HeVHR2P+06AJ"
        b"PZnhrl1cbid3XTjg40fvQ4dZjAuueqq5T4q/eowfB9em7yIGoFF/vopCMI2VhH3umm+ctYKq81C2HgEVk8EF165aqRvp17hOITK3"
        b"JjCfNcfOf/C+r578g/D+owwIP3vRVU+9swD8THiOZBGVlEswT9OTmmMXHv1n45/O9qcU84EBMlUMLYK/cm27f8WEeK0au6Xs/wg2"
        b"NwnyMZnnlHqF5X145Q3+Tl2uaK0KQvmWwC68a9mHYaQfgHQ0v0Algki0lB4StTrs3GK1Sc+fUiYLgKrbmFj06adnQWIhpUkVo29k"
        b"GY1azSn1/cIXfor9lC+WQZ+9aNFyfeVNp/4S7/n1RUhmPgA7T65bBEqA8v+k271T0XvqHIC95F+7icHwHAWGsGgko12stVXqkuNM"
        b"WmyIKPhH2SXNSNHgvy/ef8vJL6rgUrm76ujzFy3X8YslD0HYd8KsUcIUyWeTY0R1S8Cr1O9tftwkaixqXazuWxL8DM1IplU1YiQK"
        b"DMrvd6xCl5TG1XTvKXpfZuZduXLxCqHIVRiuhV3cAU6xBwrejhZ0P6SAkUzAY2eqpxb5164KH4e+GIp0EXjEWjMXEkeqAZ2RDOuS"
        b"xPnO3VLuRel5t1GcQUWWxwKLaVFTBF3/Mux8Fj5/QQSLXNKYI/r3NLR8O+OnAssRB1gcmrin0bxDNR89AtDBakaSSeneet+NJ2yj"
        b"LMqYzEpsaxNtVLL700t3QcqboSdYZEqHSQEtQZb/KwZduiqGqgBa1voKgElvgaYZJmQ0u42/2BK0aX6fgnOqhHasCjWUK9DO8ZM3"
        b"b4aUv4KRUgopmk5hJYuTkThi7mAFGiVWYqk6VmoBolkLalDnqEGBP06B2DwY/y59+vw1Y+gsz1/jK0sj8QNY+U5wLSKlzBho0/Lk"
        b"8erXtnPHn7ehkhTArvn+4pLQjuZmWqW7olpsdrHP9gTax3yxDRwHNQ/9AsqtiSQHTSanhGZq4J6/4CJvRpFqShH55gzyGJraSwOQ"
        b"I/hYT41ih/yzKvQJ2IQxVlDZhXaOH7xxN5j8I/SkH1MZLaguUrpUEX4crnjcCPaeaoNQgEMv+hUr/H8lZkdVsUn9ehrdYLAn7rtx"
        b"wfMkN2M+llsVFjGJtPF3eM5m6AadXAQpLulBM+kkZ6nfyxQINEq1NQyYSSPUVTntKKGiumQECXYvCciiMsyMOCQWqWtFZtTdoNHw"
        b"UShlRkPo6LzkdHgv1kZynBMIh7zAK5W/rAJ205W5GUEEgJroaDY8Y+wR+p3oqzH28HeCO95cAPAPaLQ5RKAAJKNUIP0wDeVA4FJ4"
        b"XmKyAGoiSshIzhW/gQCXj5LUhG7GmGKlCpoS7/+jcEo2fI6JCCxDOhWWhK1PLZdbVqk4tOAN7MyNyk2OJFsmOO1cYPK5wW7GmKN1"
        b"RZjNWKeENorqRtpx/HDCZPX72t2RujatWKr+5RJNkDAi6axVszboUrDOpNBfVM+pnPk4gfVuBWPbQcekRgCM6sPCLK8BwYJ7UuUI"
        b"GKoC6BdMxlk6qvJfqiL0nBIFZrbR781hncF4gbOtKngXiY8TLDiJ1EBwK+r2X8AwtbTPwjT69v+ggImU147e9I4ixg3BefzwAxak"
        b"3O57ADKaD6VLpUn/nlTRjyGb3v2G5ugZb5Q6VpOCBXIYT4TxDcFyAe9IFJYI6zc5y7iTCteLjvSSMen34bDsyqDnY5z4Df1r5WeD"
        b"8uoYIskMhX4S9+9JFSPoBZBUnhnlWiaWHx6PlIzPn1dREOBlGO4nR9bkUymIx2qrQAXAObHf+Zdv9AvEryvgOrFHjm9UdnFQFaKh"
        b"1jeBI9FwVG9O/5TKGXCSXJERRvRhKh1K/9bRZJ3gSYwDBqxMhkxwTJFYnCouw7xxdG8qUwH4Dqby/Xl3EN2OhvFHT0CTbAb9vmu/"
        b"qTK1KMv4YMD2Wv9nKWf4AadIImr+MmYoDgScojxuHwmP9UihAhejvid0MYSfdpuB+WsCPzkcu12Ohy/kMngErpevhAifeiQJyWYF"
        b"cRlEAmILZqyqAIZfChwuOtmjYmRRBMppFVDaTXrHSyn5ta9arEkp9WXLpNa/KFQZbTkfEpj+B8UXwBmfzxnxSDKN4mqjeoBKnAw6"
        b"x051so3dPMrjbg+DignWzRhzoolbUjGx0iVN3SXvSHqqtZWyDeW69mHcwX/4hQxMylYK+3FkujqO4ExOVy9x9T+M4sG4RvdGejW1"
        b"Xnfr8lZ94fn38UGK57DGIbsBW9aCtdMPUnZQqohmYoy2GYjuOhFDSsbODD5PdRiNOaj67IjWpCi6J8LJF+G5tEuMclFID6Kow3J9"
        b"vrw7PhKt5xn04z+2flfnqXMn55nBk3ADQ2DkYEJKT09kNOHmzwawaoDiq3yYv7rFXFtql3gRQi6RHmvzlUMulzsdiQYbtuOBaUGm"
        b"Y6RdvERizDk81pv/0aVr2m4N9M9H7lD/kkJYu3iBbMcSopmbuPGPkSqAQTn6F1UdAGnNUYoIUYkpBSDlwt8s77rsotMaM0UXR3CG"
        b"Zgg0AEiDQVXmlBMag+wtuom/vdiZllL6O/YowRmTrpB8wYz6D8z7rnyr7QiyKiJfWEKAXfODF5KbOiwYfPQrlzPJS6USTjmi5r9+"
        b"0ysvLVrQKPSDiEF8nUxy4TAvkfOyUzzc5rmzXLu03Sr0ejmrG7u8x17eOe35ju1dFst5lnCYwXQYMKARJbykCTBDPyxPMulYmu55"
        b"cE74/I3Xc0O+BIH1uivX3rjgmp2MDfActCwjxqB2tC9pP2zChYdUAGGOnjN9g2cXSXrpb0adfOYQyDsyWXC0n6TrgRRl4aRa2H4a"
        b"fYx0cSqto6VpauRZLwHMpK06kYy+yJGujWkAR0xN4fntBZgpnamZPKOBIuRz8HKnNrPPwszmesCJ0AUfQHA9pIY6r0F9Pj20IArg"
        b"YD4uaATyIott7g687G7Di85L2OnuhiUtGMyAzvxlO5SQjX/8ZNgBMNPXeo6AU7DgMZm76rkb1165/saHOLAiA3Nl27FL+sK/a5HL"
        b"tHY2XB7KysOhby/Rc7UxsejK1UebhlzDiB42go5AymFZjsDMSQl54+VHiaRJNH1hMGhsIaSIXEoZ41TfXBY9Rheqvobj+/ft1H6y"
        b"fCfq0zq80SoAsog4Q1/ekZedN11cfn4z+gqAXqaTUOGkwAdXU918Sn+/rYnkVYKp7gwARSmw3d2BtdbzWGc/i91ehyKjTjAzcA6G"
        b"cIC0YskR8GnINfIsNFODbhpwijY8290mJbuLm/JnN8275q97KYIJaxEMRdjULSGyjp4nVj+t6ckFwilF4CtTdpEhV3TxkdfPwJJX"
        b"T0Zfgca6HfZxmUOCqDNrkgwPrc3iK/+7CQb1BUbTOKvCCXVpDV+7/ChMqTfgUMX2ONwSEuqA3BMUGCRlQIGnPmFjnb0ej5WexGZn"
        b"i3rNVIpgOI6B/+HS393J5tQ0U2dGyoSdp3CUXK5xfvtXf/Sp/0Ub1HzBVhV+mXhMQkO7teQbLVniXXDtqjvNZP3lTrHXBQvssFEv"
        b"OIlMUsNXLz8KM5pMWI5vHVRxYNCqJc6c3X0urvze8yjYIrJrFirli14xCVe/dSbyJZUdGffb4SsECV0JPFCSEqusdXio+Hdsc7cr"
        b"JUA0oMNSA3uFCsEYUd3rRtpUgS7Hdh6B5F+46egr/zxRrYEhmb6L1rQEuVs87BNQRLMn0K5l6BzdORe3/3kbPI8WW+SM4xMOdPFd"
        b"AUyq1TF3ahK2SwqARWhd6LjvqS7c/WQP6tIsEvditCCBpN2etuCCarZiOD05H1c0vA8X1ZwHDRpK0lbvGdHnE4KYmFOwPCtvCd3Q"
        b"z9J09qdPPXfjbz+x7objSPhbZaufQpwgGNLVWtnmF1FqEHe7Vi7HVetYNGKqFlyC47Hn+3DH3TuQMvwJTlUlcHCQUKYSwKnzMipw"
        b"GvVsLXIrbr97Gx57IY96UgJqikI8EAo5xQZ06Dg/fRY+VP9uzNFnoSiLSlmMLlXFNGpYswu2cCxHJGqTbzFM46Grn/vqv7axNhpg"
        b"IkkRYAJgyFeJtB6d+EXXrvqzZmYuot7xKAdRkPotWAJvO3MKrrhoGjUKwXarMYED3w8gYQDrt5fwmR9tAG3SUSoBsvodTyKd0HDt"
        b"pXNwxrE1KkajRCtm+5+AQJJxWNLFvYW/4uHCoypTQIoiinJpCbiaxnU9SQFD52fZXuc/7jj9ut5lcpm2pMJdgiFrscVL/d55wfG/"
        b"NAMj6lg9LWiyBH7zyC7c8JuXkS95yBBhMKUG47P5xAYkhLYLzGtO4tgZadhOtO1BdM0NjaFQ8vClZZvwx8d6UJNgMHTf+oiThUaC"
        b"TjEBDh2X1JyHt2UuVnubB3fELsFgkGvguZ60cyUvUZt8T6ZOv/djq75wFAk/xQVQwRjy1VkZUFybsu43hVzPZk0zqWw30qgoLToK"
        b"CC5/phvX/fhF/P3ZHFImkE4ENQIxWnSxcQNMYNGJDcoNiDpjTddb1xkcV+CW37+Mr/92Kzr6HOUSGNpAzUYcQJzidAmKUuLM1Ml4"
        b"b90SJFkKjnSiUQKMhtUzrdid98y0+cpETc0DV637ykIVF1jeGvmE7LHCsLYMqtVfsoR5H/zq87/e1me83XOiH7JJoJ3MsqkzleGs"
        b"4+tw8emTcNLcGmWWFu1qlmDPawVkCx6uuvMFJZxRpQQHIzT5ySprbjDxhldMwutOacSMJh0l2w9IxiBRsIdLkGYcG5xt+FlfO4qi"
        b"pFyCqLonpZCekVaFKzvtrHXxN0/+zBOVmiEYTgxAY4x5xaz82JYu6+uf/M4GzfKjz2W59UFBiEpx0aJ+xVG1eNdrmrFgTgr50vjk"
        b"puMIipXUpoAfPbALP3lgBzJpXQVWywFSzGQNlByB5joTF57WhLefNRk1Sa4UQbSDyUevBFKM40VnG37a146SsNS8wMiUgJSenjQ0"
        b"eHKHa9sX3nzCdasoMEhBQlQQhiRGy5dL/dxzmduxQ74tkcJvkgZwy2+3yd8+2sHqIqpCOxAovUUpWlIEKZPj/edNw1vPnISSM06c"
        b"NTED7fa6DnRnXVx15wb05BxltpfLNPdLdxlcV6h7cvzMNK58y2wcNT2BgiUjS0dGAQ8CGcaxxn5JWQJ+mWk0gcFQCZg1Cc0pORu4"
        b"JV79tRM/vSMMlqNCcEidTa1UJPzdO+WpiRR+IARksQRxyaumsEm1BhyPZmuW7wAFcYjDjw2QIrj1T1vx85W7le8bF/9zPEHX3nGA"
        b"aY063riwSe3O5RRCv19DQtOYKkF+YUcRrT/fiOe3lpAy/aBtXECFQXkpMN+ci9fXnA9L0tSm6MAY0+y85Zo1iaOkyX7eurE1uaS9"
        b"vaLqBA6qAIITkT09sgk6luk66l0XwnbBj5xq4K1nTkFRDdct//mSlUEmaCal4cfLt+Pef/ao4KAKfh3moMtPJvibzpiEY2amUbK9"
        b"svvkpAjontQkNHRkHXztty+rgi4KDsZJMVMAkAqHzkmdhjOTp6MoSypgGBUYY7rVV3IT9elzewo1X25fssRbQgNOKgQHPlAGtLcr"
        b"F1+6Fv6nrg7HFApwaSy4n7MHLjmjCcfPrEHRogVXfiUQLixT4/jR8h3Y3u2qrrg4LbjxQFjDX5/meM+iqWoXHiuui1AJbNhRxC/+"
        b"uhumHr/2OVXWKyVenzkXc/SZyhKIMo/NAM3qLXhmyvjk1c999XwKBrYsa9EqWgHIX/kR/55O+c66eryztxce81uB+wlkKPjzwfOn"
        b"qSAdmepjseT8VliOHd027n6yy19wcVtx4wAKwOVKEuccX4vXv2KSqucfq8aqUAk8sKobG3bYqkApTq4ACTuNtEkxA2/IXBhpMFCB"
        b"EaUahRcY88Bub914c8OylmWiElwBfiC/Hy0QZPoL4Gu2rdxvtveCK5QkFh5dg5azm5EreWPWNEIHkzQ4Hn62F70FCb0idO0Y7XQe"
        b"8IHXTcW8aSnlno1Vek7XqJXYxd+e7Q2Ucow0wKBioaONGTgjuRAlaUXsCoC7RctL1aeP6ivZ/0WWc3t7/F2BAx6gMv1tfKa+DjNL"
        b"JaJx2fe9xLlWtCTe8ZopOPv4emTHaNdRBSoaw64eG5t3l1R1Wpx2nHENCLrkCmj4fxfPVErSVVyY5f9u2lHp3j+7Ja9qNZSfGDPQ"
        b"EZEr8Jr0mZjEG+GA5ipGqQQ4t7IlwXX9o1etv3H+kpYlopU20xiD73f3p8DfTnm0puGj2ayaPbvfk1DTNCVTO//HL5mFI6emUKB4"
        b"wBgoAVpfliuxrctSpBVVP2CQZWZJnHpkGv/+hhmqn2LM0pEaU65ZyaaAbTxjAS4kmngaZ6VfCUdGqwCUK+B50qwxk0LIz/ocg/4o"
        b"t7hiH8Fub1dVj9LTcGVdHdIU9aciyIPWpDsSk2o1XPP2OWjMGIrpp9xFIcqvE0I1EEVEVjxhQAo4W5K48LQGvGfRNOSKYxCkpWkI"
        b"DMhbHlyqToqfAdC/bmwJnJY4Gc3aZDiIOCDIGLdzlmQab/nk89cvoMKgOFsBfO/dnwJ/WzrlbCZxWS534N1/jw/hTO06x0xP4LMt"
        b"c1TOnpRCeZWA35lGZmdV+PdzTxhDwZa4bPEUvOM1U5EtuUpAyymXdB90rmJhiCtCK6CeJ3Ba8iS4UVsBasaCEIlM0mSe9hF6Ym27"
        b"P88xjthbRNWBpiUuq29A3aF2/8EgQaQo9Mlz0/hMyxEqKlyyRdliAioboDE01VLpa2w3nHEGg+VIfOj8qbj0rCkqUOuP2yvLV6my"
        b"ZLIATapEjPE9UYQqEjgpcSIyLAMv+plh3ClQ5Fy848rVNzVRbUAEE47KrQBUCaNHAzog8W7LGj7zLwl7tihx2rw02t59JKY2JJAr"
        b"lScwSPwUNUkNc5tTsGldx6kbJSZQVVySwXKBj7x+Ot5/3nSVGaBKvqh3aYqoU1XovKlJpBPxYBE6aLYEElO0OhxlHgl7FExC+/18"
        b"NffC85K1qWaeENSbjNYVS2OZq+o/a9VODaB7N87SDSwoEgP4EMz//VoCReD42Ul88bK5WDAng75CYH5GtOZoVAR1C86fU4NpDTpc"
        b"Iq6M5qMnHAL2XVUp+N5zp+CTl8xWwl9yvEgVs+Lr0xgWHl0XywDgfrMWAE5IHBsZccien692VCk99lb1xIqA+jhm2FfAOS7OZJQ8"
        b"eaMRUOrYm9Zo4ovvORJvftVkFawjAtAoFh0taBr3RN1o4dyYKg4MpXiZXyj0xtMb8MXL5mHO5CR6C8TtOvpWXlIoZFkcMz2NhUdn"
        b"UKA0YMwtMhJ6WwJH6kegjtdG7gYwCc0p2jQqfvF/rP3yJMUo3BqhmRER+g8oMP9phM3rLH9QFxv1Lu34qaGPXTwDV711NhpqdFUr"
        b"MJpFR00ofUUPi09swBnHZFRJcszXWizAAkEl62zBESl8+X3zFM8CMQlR1oYEdiQWmp8K9pUMlSGT+V8p/RkeJOq0FGYZM1VKMEo3"
        b"gPwMYhHSTL0pqRln0lOti+PHI8gH5f7R04M5AE4MFMCoD5YHgSEqDLnoFQ248YNH4YJTm1RuOqxSG44vqmIMBU9RYH34Qp83sIqR"
        b"1AkQh4CGK98yE59dMhezJieVmxZaaEO9JaFbR3Ged712Ks48jhQyxRcq465IIj8FMFuf2T+cJFowz6hJkII8S/0aTKOPE5SQr1jh"
        b"/ysEXplKIOl5Q4/+Hwrh6KdsEZhca+Dqt83C5981FyfPpcVCeXxi/sEBF174GoFM1uNmpvDZJXPQWKOXaXTVxAcPmocKJeDVJ9Ti"
        b"qx84Ch++cAbqa3R1jR3X77zcX9rQtyT8e0KFWHlL4LJFU/GeRc0q7RjnFOC+YMrwn65Pg8kSikQk6i8QdKEhT6FflmIp/RKrC6QP"
        b"VkwcODGVBqjfPwoLYG+XgMpUqSP79GMyOGluBo8816saep55KQ/L9ZDQ+R6KgHx92pXotbSp4c1nTFKEINQSHDcGmkpDqJgpVkNE"
        b"K+9eNBmvXVCPe5/qxorVPdjSUVJKgFJ6g4Wasi/kNhBN+OwpSVy2eCrOO6leEbTEbG0PKXPhSqBZn4wkS6jZg1FXBnpEbQd2Quvq"
        b"VpMxZkcxXDdK6IMjlJ7AcWpMfJkOsJ9bLvDbzz2pHuccX4+nX8rjr2t6sPqlPLrzjm/aExWrxjC1wcApczNYfFKjogOjXV9ND6oK"
        b"fySg60hCnS1ITKkzQDMBKTbw8Lpe/P3ZPry4qwiLUoeBn0/K4vhZaZx5bB0Wn9SASXVaP0VbjNb1kCFpQCxLqkDgDregho9GlRFg"
        b"UjLpWwAzdhjGFABbZcw0gB5QGKlyRd6BI4hdRpbZkgt9xFxRqvz96UfXYOFRNegtCOzudfqj0xQ0nFJnquk0tAApuhyaoFVEB5Uk"
        b"IL4/D7ALUs0GfNtZTXjDwiZ05Vzs6nVUUVdCZ2peIDFBJRN+apGEv5Lvh1S7IEejVo+tNGIsUnJln5pNAsmkm5hFCmBJ+xLaumJD"
        b"HtpPZ7z0JZhdGUwnC2DUo3+HiLBpSM1jJE1schw5LQGNJdTvtDPR8eQtvyapkhdaJaCf70/4FgHdn6aMjuZ6vb+egKwz2tRyNCSE"
        b"Vf49EZAwGUOGZ4KdP2CjjYwnQEjN0JnkXrP/ZAt13CB2CqCXI8WARuUCjPFBhItICTxZIMHz6ulhZgqqiM4iIJCwk2Wwx2t0Typd"
        b"8vshoYMhzVLlCAIqR0AzNOa6XhP9Nn/KmlhdOH3p0kDlJdAIBnM8eRxCgY/VFTrMUam+/XCRZMmy+OaMQWiGxlFAUxxTgXxp2K6c"
        b"RKOU46sAqqhiPCCCQCBNGB5tAFAGDzHwkFLjkAyN9Pr2J7bHSp+SC6AOyHXRpOvgoppbr+IwgyQFwJPQ2PB7AkKBV8Hp4KFGCw8k"
        b"RVlKOQKigX6Znp0eqy22PwbABBqNBGB50dcAVFFFXMGCmYLkAnDVHjR0oad365SuDgqKLClRFBK9nocsDbMREiUhFF2Nm3eOUsH1"
        b"xVQM1IbYWQCahkai2C6VFAlIFVUcNpCkAMgCAFdNQQeKBQTlKSprQDskkYzucgW2OB52ugK7XA+9QireQd+QoH+lSqukHHHkE49/"
        b"h9Ip1IlMXxELS0B/4gn/bIVEk276QYtAuVVRxWEBKX0XgDMOV5IC2Ov1wCROMgZPSmxxXDxvu9hoe+j0qEhqUN4qtPvVjwO/6xyZ"
        b"nzXnaMONdjzRKKEvXNiviVSQoooqDiewYCtOIgEdOmzY/fMDw50wQfRqUuL5koNVloOXHCL4CTz/oHYirB4Ihan/Z9rtlVUgav60"
        b"2Y3dGHG9/zglmqSf7606AFUcVpBqh9eQYgnkZb5feFPEcyAkHis5+GfJRidVSAVlcmpo7SBBP7A974uTLaA/pxdiJ1tUB6COXRUB"
        b"qZzFmIz6q6KK2ECqyD1HkqfgeQLJgGRmjeXgkYKN3Urww/JYX/CHVTLkD7hNzIE+aTPQN5A3GH/obW0sODs0VgqRQxVVlKMzMMkS"
        b"SDCg25NYni/heSJTHNTENqKoXcCYwjWeSBiYDWAjsDTCeuPRQfkkUkqtqwNNgQKIh2qqoooxgqRyYMZQq6XwVM7GI3kHBc/rL4eW"
        b"o+8IkswwGJeYqp7x+Tdisd2G+X5DAk2qEWisxspWUUVMIJlEEsCqnIb7+wooEudkMG8iom1aUBrAYtIvB44RfCbgbiQ4UF9OLoAq"
        b"qojzaPWeLqCrYIIxag6KeHsmXaJx2J6oRczgWwAlNFCQotoHUMXhBCX8LtDRqQrg0MATUe76g79JCqLL95ShAdTGpx/Az0sm0QAP"
        b"RlUBVHG4gNz7kgV0dgftzhyoAfFQaIojIFJI36LI0cTNmMFXAC7qNWoEikVYoooqyi/8RIja1a2IcJUlQDqgltMGXQYZZVLV12qQ"
        b"jcrCiFFDUBgErNN1P10ZFRtwFVXElQMxVwA6u/wS4MEpvgxZ6EEtfBQI5rGAE/Od5yHJtOlCSta6GNQhFCMLgKGeFMBI5gHGHVSy"
        b"SbXaPpMNMQ6z/T43USEEGbRE8sj7z3N/zx0uwt+XBXp69wx2s6Cbr1ZR0ZEFMLINOhB2P/VPj2B7lSqdKFHSMYnGhfVX37Ys0zBf"
        b"MQQJRpODxk0BaKgzaEJCTIoTogAJOT0MgyOZ8MkZbdufYkPU46bpP0d+oOsKMCUMmDAgIdc0jpo0p05Pde4U7VbMvkn/ORoAQ2Pc"
        b"VSnsRDr5/YBOr7vHVwD7Y5SWEsiwpCIIdYOOwKEKQyj0HjEsk0QFtJ+GA6Q8oNGWzCgINDps+j++9KWp2GRn2R1tBda+pJ9sTX1E"
        b"yzKO9iV+//DYFgKhjusTpxOQFr9pcCXk23b04qm1q7Bh8yZ0dHXCdhykEgk0T56C4+YdhZNPOBGTGpMoWoAXDMSoZJDSo109U8OR"
        b"yws8+tQ6rF3/HLbv2oV8IQ9N09BQ34C5s2bj1Pkn4sjZU5UfXLIE+ATmWid/P5ffv/CzYOerRUK1BLugCkA2dMEPhD5hAWfsAhbv"
        b"Ak7qYZjfJzG9SEqAc+432pwA4WxAkvXKjy19AcBaMPEPCGMF+/Z/bUSgEMgyGKwcym8BCNRjgoCEn3a9Hbt78Os//REr//43dPZ0"
        b"w3VdtfhpJyDzX3gChmFgRvNUXLToPFxywYXIpE0US5UrCDT22zAZDabEnx54GL+758/YuGUTSiVLnZMqblHMvp4y/+tqa3HGKaeh"
        b"5U2X4Lh5s5Ev+JbQRIG61wLo7AFKxYPPkhAUA2BJGEyDIqE+BDQ5IPgLOoH3vAT8y2aGY7JBw2/IXx8GAoJDAuc1YKwGmjYDmvZa"
        b"eN6/wbaL8pNfuB/wfolu+Wv2oyUlerNsbeXldg3UoXV2yP9pasJHuzrhMjbAElSpwv/IE6vw7R99H1t2bEMqmYKh64FJN3BnaZek"
        b"9zuOg5JlYf6xx+HKD1+BY+bORqFYeUqAhD9hMnT39eJbP7gTD/7jUei6joRpKmHf49yDa+F5HgqlEuoyGVy+5F146wWvQ9GaGEpA"
        b"RfYF0NUFZd0dzLCTwU6Yly4uzn8Tu2Q3SAz2Rw9Gu74kG90ATugCrnoWuGwjQ6IU2M4a4A6Se+XxDz4uVXmsoo9+IEYNKmYcRjCR"
        b"wHXXAriZ3fLf31PH1tKisfZ2r9wK4CeN9bisp1fZPhWpAEiYM2mO+x5+FDfe/j9ql08mEn7A6yAFDorbnnPk83k0NjSi9ZNX48Tj"
        b"5qFQrBx3gM7VNBi6unvRevONWPf8etTX1qp1JuTBNxCNa3BdBwWrhA/8y7vw/n95q7KCKlkJKOH3gI4uP/ZxqPCGDNx2Bx7enr8N"
        b"G8UOMOw7IUjt+kGM8Op1wOdWMzQUqJAecLn/GSTwI1g1pFaEysEZOgcnLWL/FRKfZt9ofZQsAbS1Kb2DiBHe5dqwFRgVCNr90imO"
        b"p9a9gK9/93Yl1LTz0Q53MOEn0Ov0vpp0DXr7enH9rd/Etl3dQZCwAmKiNMaJS2XJ3PidW/HsC+vRUFcHTxBTzaGtR+UOaBpq0zX4"
        b"0f/+En984K/KiiLFWYkgYafpVrs7hib8g0GcADXBbKC9/0wTgGcAswrAvQ8w3PgPhgYHcBO+RaBT/8DIhJ9ANoOmmEgcV8AqedDN"
        b"14Lx5fLjSz8RugES0Y8XDz8wQ9HxioSUKqKdK9i4/ac/gmXbMHRjvws43O33l/oiQUilUti6Yxvu/NXP1TDTSgDdt3SS49d//gse"
        b"e/qfqK+tg0vb3zDOXWVMiPkmkcAPfvVzbHx5NxKJylMCZLRYNrC70x9EO1ThZ4PcgBpKBe6l+Em4vQTwyt3A8nuA87cAbnJA8CPd"
        b"NUkJMApElDxIkUIieYv8eOvtvlJqE1ErAS6l5EwiU6lsQMTRlk4y3P/ww8r0TSdTSpgHI8z127aNfKGgdktyu/b5LM9DpiaDh/7x"
        b"dzy5ej1SST9OEFfQOjUMP9Nx1313I5VKqp1/byiuO/L3i0UV7yDsrQhICZiGoQKmv737T9C1CiztLfl1/SNpapPUrwMgrWoBBsph"
        b"SMBpl3/Dy8A99zEcnWXqd50qCFFGkBIg07ZUcpFIfQQf//wvZGurqZQAuQQRgQfnWFupFgDl9AslieV/e1AFvfb222ihk1BYloW5"
        b"s+fgrIWnY1rzVBRK5LztC8a5siJWPPIg4g4pPTWk8+HHH8Wuzt3K8tnbbaHzp3Otz9TijFNPw/xjjvWDn66zjxIQnodUMolHnnwM"
        b"O3ZnVQ1FJbhBdBqFou/zh6W9I4FGFkDgAih+fxJ+E3jfeuC3K3yTnyL/JPxjAp9sUPeVQPId6GE/V0qg7fORKQF9G6CngExwnyvK"
        b"AqDgF0W+N23ZgY0vb1Y72OAdO4z061zDRz94OV53zjnQNB2WXUL7H/+EX/3hNzANc49FLoVQ8YNVz65Db9ZGKmEqMzuOoEAdmbxP"
        b"P7tmv6Y9mfzFUhHnnv1a/Ou73omGukZIKfD0unW45fvfQVd3t0qFhuevqLE0DV09PVj7/HN43Tmnw3HizRGnSnvzfpEPYaSHKhUt"
        b"GJDmZv90XBL+q1cBNz7OlHYQmq8UxhxKCRRdJJOXoqf0c9n63+9mbZ+3o0gT8lS3IgPJxNjSPTCkBJUwb966Re3we6fuaOGSyXvp"
        b"Gy9By8WLVJzF80hpJHHFu96OxWeeo8ziwX9HwkBC0N3bjd2dHdBUj4SMrfVTLDp4edvWfXZ/Oic692PmHoVPffgKTGlqVOdOcype"
        b"e8YC/PtlH+inrh8M+jvbtrB568sqtjLaUVnlBN22bA7oCoR/NBBqSChQK02ACzg6cMNjwI2PMQhaAxTlH89LEVoCJikBsgT+OxJL"
        b"gDoAM4xDrzgFoEp9BTQm0ZPtU3794J1KFVir6H4aZy98paqKk8LfzWzHU9bDWa94pSqX3XuNqxHZnoee3l4VDIyjAlAVfyroZSGb"
        b"y+1X+ZFSPOPUV6AuoyFf8PqvT0+fwKnzT8L0Kc37ugJBJWFvXx9c2v1V5bqMZ11/zt/5o7JPdKX8/dH0P36Y4dPPMHjm/vP5E0UJ"
        b"0B/WBcqvMhAuRo1BmjqkwcCp0Ifv3cbop/FM00RdXT2kN/A61b17HkNDXb2fMdg7XaYon31LIPagHgaK7u/XOtJQX0e3d8A0Vm6l"
        b"ZNB0HfUNDUqJ7iFCwRuZpoGbDMLUIGn+VfCZsWrqIeGPSPq5lLAMhpleCj9dyfDe5/zgH5n8sXKAIlYCdN4ZFe+Ix709MFT1FCBN"
        b"v8yKZ7NIbdwI7dFVOD3Xi1QiCZcaYALOdl3jyDs2jp4xA02pJGxy8Ay1navXKU00d/ZsNDU0qv4ACiCScOiarrIFkxqbcOTs2bAd"
        b"PzAYN/hWikRDXQrHzJ2HQqkIXZU6M6W4yIJJJlI44ehjVT5cNfvQNeSAqzPUmMD8mbNRcBwY3L9m4XukruN06cJ8bBVSz6+HTqwZ"
        b"pBKDaz+eioAOkbr59u7oGx2Ecg9LHVl86u6n8J5NJkRCquh4LLG3EmgZuRKgzbOGNotYKwBalIZf25l48SXU/m0l6v56HzL//Af4"
        b"ulU4yrPx/uOPQ95xkKXSXs9DZ7GEKTUZfHTWLNQ9eD/STz8FvafX/xzO4LkCDXVJXP6Od6k0GZm8FP3vzfUpQbi85V1oqE8pIRvq"
        b"OiNLQhXgjMCfIpdkuH9LmzdVK77v0hZMmTQFXb09yiXIFfIq3dnypjerGn+Lyns5VwLMC0Wkn12H5AP34X2ZJE6ZOg27igUUXRd5"
        b"x1XX7ZK5R2JxbQby2dXIPPMk6h5+QF3v1LPPgVPPOCkCdQAYF+Hv7YtW+IXHYaZ2YM5L30Vmw2Z4KUPtjLHGYCUwnf9spJYA69ol"
        b"LwbHXYgjghpNCsCYW7ci9dwaaMTeSHefonPBKvBNXIaVW7bini0vo892MLe2FpceNQ9z6+shbRuMBMs0Yc05EsXj5kNQ/swRSCY5"
        b"nlrzHP684n50dHVhclMTXr/4PJy24HiUrKFFwMMOvATlhzU/D03pdsU5cIi/D0dM0eFQQNPvzPOrG4fy3fQ+qlfY+PJ2/P7eu1VA"
        b"kOIei848G4vPfBUcR4Dm0zMhkXxhPZIvrgcr5CC5BmYY6LFKaH9hA1Z3dcPgHGdPnYq3zjtSxRTUefkHSQEVpYBFTQbW0cehNO8Y"
        b"VQjDvEGsGmMg/Adq5x0ZBITkSOlbMKPp2zB4B/hvp0J/PAGkAx6vuEPCRTKlwy7+Gg0yyA7895CzA6xzl1yiG/jVforHxhdhgbbw"
        b"kF7zNBIbnvdXAUmJen1PDa3EKJAgSuWpn8lycN0BE57+xrEhahuQf8UZcKZMUjObUila7CR41D9ARUNEGeW7Coc8TOkfEr1z9XPP"
        b"qwakKU2TcPIJC1Q5Me2+B2osIuHVdaYCjWvWv4CXt21DY0ODatOlSryhlrKqRqAEuS+UD/f7AojfIZ+nSiEGLZdHzVOPQ9+53T/Y"
        b"4Hh8xcV9rUX1s+H1pZ/3RnggtFA8D+60Gcif9kp46RSYW14lQJ/c3etH/KMW/rT5AmY03AaN9UFQERDzYP6mAdoTaSAtKkgJJHXY"
        b"pWErAdbRIS9PGLjTtuNEBxYsKM9F5rFHYGzfAph+dPZg/me44/rDWAYYf/ZA8LnUcJE7/SzYM2dAKhOZSmWpbsAnEhlKNyC9j2Qn"
        b"Xyzilu9/D39/8jFlxpPiOOGYY/GpD/8b5s5qRjbv7UM4Qu9LJzXkiyV8887v46HHHlV+Ox0v8RRcdcW/Y/aMabDtoVsh4XGrn0kJ"
        b"mhq03j7U/v1B8HwWMKjMdc814Qf5B5QdVVZSHOWgoNdtG6K+AdkzXw0vkymbEgiJPKIVfg+e1FCbXI3pdXeA8xIkpf+UT+WX+Jq/"
        b"qT8slACtl5og2B0zp0ci8+RjMLaR8FPhtZq1ftC/oEU8ONK/X8Ghz1DJfYGaxx+BsasDzPSj6H6Xpl8zP7QjpA2T4Xu//AXue3CF"
        b"yjhQJR3V1D+zbi3+62vXY9VzG1FfpykzXdcoQEeNSpSB0LCrsxNtN38d9z64UgUh6W+pg/GZZ9fipjtug2U5atEP5cYMPm6l+AwN"
        b"WqGE2n88DF7IQbWb7qc5yG9bH7hOhxT+8BqaCfC+XtT+42/gJQuSgqwR+810JJTjj3rnJ+GvTz6OGfX/A85sX/hVIDD4h9ij3t4L"
        b"b2EBKARtfnHHCLMDnHuoCUxYGaeAX/KF52FseYk6VPa7cEf7HWQBMKoTePpxcAr1B7xtQ/8ICVNn2L6rG3974h+qA09xDQaBvNpM"
        b"Bjs6duFzX/0yvv3Dn+OZZ9ejL5tHLl/ECy+9jJ/8+i5c9YUvKLYiv3V34G+poWf9xg3qb5LUlTjCIo30qieVkPrCHzXVtR9T4d2d"
        b"SK9+OvJcGemhrl4gF7XZLzia0n/F9Prvqmf2GQNymCkB3QMy6gIPnQKtjJBqJ9H6Cki+8CygG+VTS34nDXhvN5Ibnkdh/nywkMll"
        b"iKDr1pvNwg1azwYXDFFjUcIwVaHNr/7wO/zhvntRk0opV6NYKiGXz8M0DaRT6X0beAJzvru3S7WGj0SBJrZshbH1ZSWkKrJYDqjA"
        b"agLmyy/BnDUX9vRmMCcaV4Ayj/kDUHiNDEQEyzEpczem1C6DkORShvSdeyFUAtxXAmQfVIw7ECqBvcuGpWQBIekeIOuPxqDHA6og"
        b"G0i8vAmsGN79MuolWsCaDnPzS+AliguwYebhgamTJyNTU6MEfp/mGuVbc2UN0EvZQh592WzQdVhzwLZleobqEeZMn60m1wy7Fl8A"
        b"iZc2YMwgBRKbNkRyq0KzPzrh90l4hWSYkrlrL+GXBz+QCWQJKBNfUnRjT1B5czJwr8dfDZDf7kgYu7YrE31MQBVvxRz0zt2Qiu1l"
        b"6KuYagSaGtJ48wVvULv6/ijGqaIwFHIq1AkLjg7EVERFPNlcFq991dk44Zi5Q05F+l8WWFC5PPTuLj+iX+58dhBToetHMQfVUTNS"
        b"Wu3A7I9W+P1O+uba32BS5tcQkoZ/DPEY2YAScCpVCSRSpAR+hNalDEv3JSwiC0ApgFiAanQsGzwXJHvH6sA8D3pf97BvLF08EtCW"
        b"N70BSy55K/py2YNmEMJI/YE+i/6up68PZy98Ff7j/e9XCmbY4IDW1wO4QVpvzBS3DS3bpyLoI5F/JfzdUfr8A8I/tfYXaMr8AUKm"
        b"+y2CoR8YfJOsUpVAsejCTL0TXfJbrI0Joh4frAQ4EeFEHWMbEUg4OMDsEhgVnYyxX8JU5c7w/46sKseV+LfL3o0r3v1elcqjFlyf"
        b"hZeyCwc/D3qPRhwEjq14Cd/0ugvwnx/7uCpt9okt2PCVqE3n4o05/S6zSiMSDJXqOwht9/AxQOgxte7HaKi5G0LUjNydZBWtBDTV"
        b"SpxIfVR+vPUKRTfe0sL3sADiIP+VClU+L6jgR+I9b7sEX7n2czjhmOOQLxRVOS6xE4UpOjLv1YMyEEHQkNqR+/I5zJg6DZ/+94/h"
        b"6iv+FYZOwcPR9OGPvzc3rCIfSvVFKPy+Nyswre5ONKYfgBDU7jLKVc4qVgnQCtVUSSjXbpb/8fmTiGU4zAzopACCazO+q4bMSCIm"
        b"NZOQ1NQSVqaNEWQiOeKb6R8mU5V3py04Dicc/d94+PF/YMUjD2PdC+tVbb5l2f1UZRQYJNIR4iA87qij8dozzsLiM89GU2MK+ULA"
        b"zDIKZgtBqdOxiqH0p1U5ZDI5LDmLvsjHF34GF9Pqf4C65MPwohD+vZUA95UAKiU7oIJO1EiTTMMr3iJbWy+gp8kVoIanVGwYb9Ti"
        b"NeFl6lRQaUyCWARNh1vfMOqbSJWE4UyBC17zKlWLv7OjBy9ufgk7d+9SOz2dTzqVUs078+YcgRlTm5FKAsUSmcARzCMg9traBpXi"
        b"VD7EWLlvxMKcqff75tn4Cv/0+u+hLvl3eLLWn9EVJVjFKgENdsmDmTwPPc6l7But7TRzgLoAKSwaD6iFxOA0T4e+e+fYfKfnQaRr"
        b"4DZNoTLwUVsdoQCTMJPibZ7UgJnTTvVJNgeXAnt+yT2VEGRz/ntHLfxkRXkSXiYNt2ky9O1bfUVQTiWquLMcuJNmwksnhlQSXBbh"
        b"lwyM25he913UJh8rj/DvRwlUVJ2ASgOqsVjXydbW/8PSpR7FAMzYsAHRhfUAe/YRkOl0UMBSRjeAVp/nqg5BkaJGougEJaTgJl+e"
        b"Rm719BHD0MCDFAQN5hz83ui+HCgdcdTYOXVEPzb36CHv/D1lEH6ulTCz/juoSz4GUU7hr+QUIXXF0aIzEq9AJ7+ACoNoZpSaCxyP"
        b"YqBgB6tNoXjM/CCVVd4JEl7DJFjzjgZT8yDL0cyyVwAweEQu9HvVUtjTp8OZeYQ/HaNchCb0uZYFe/Y8OM1TDlkFSC9RL39flMJP"
        b"11eWMCN5KzKpJ8u78x8kMFhBxUICmi7BxbvoFzWVLDaFQIMWcOmoo2DPmQdQSivqBRzMjiLmm8Kpp0OYRrxNtxEif9JpqmPP7yuO"
        b"+hqS/WvBmzQZhRNPPuT1C2m8ouvn931+LguYbt+AtPZkEPAb4752VmGWALWlOjYV/l8or2xtIuITMzaFQP3weevypy2EM3OOP/FB"
        b"TU9iEfmsLqSuIX/6WXAmNwV+KyYOgliASCWQfdWrITJ1gGNFZOEE94GEv74JuTPOgTBNlcE50Of3c/hFxuQTCD+KmF66EWnxOIRR"
        b"N/bCX5GWgOQQHlWrNUPg1TQVSLkAsYMKsujIvfIsWMedAAhXCa5aQcMujgn+hjSdbUHU1iF31mLYM6ZH1rwSSyXgkjuVQfacRXCn"
        b"zvAtgXByxkivIaUyHRvOjFnInv1aeDXpgBXo4NTdxOYTzazVMNpfwnTra0h7j0Ho9WDE3jm+tN2oHEtACjVHXshzWGeH3K5rmEab"
        b"YnwIQQIEKSWpA+a27T4lWFen/xqRGAzFllR0VpQbc1Wu3zpiHkrHnqDSjeVmsokFgv4A2qGTG59H4oXnwCkdSdeOruFQ+/8pbSGF"
        b"siZKRx8Pa+68IGh74GsYDu2gEt8od34GSwl/jfeI8vl5koNNr0esaOxYjElFJARMkyin7iIF0KFxTCKXOHYKYK8WV9qtzW1bYG55"
        b"CXpXJxiZtTTWmLYWtcIG0VeHfo2uKx47e9pM2HPmwm2o9QN+4jAQ/hA+04maaa/lLZhbNsHcuhlaX58faKVacPLp95oP4D9PbMom"
        b"3LoG5Y5Zs46ASBt+6/RBcv4k/PmCL/wRnURg9luYZt2EGvdvEKxOWYYskwBrpuBfzOjsGGDEUgkQ7ZNO3PjrWNdu2cs11MUmFTiE"
        b"RUz5ek4db10d0Pp6oRXzYGTehlMhDR1eMgVRUwe3sQleQ6Pit1d+6hiRWMYSqmKPKYuKlCldO1Kk1MTDSwXV0KMUI73HSEAQVwFN"
        b"G26aBK+23r/2Q1CeJPyFgt/TH9GBBzu/g+kk/N5DEAh8fiJPrU36CiBOwSwZa0vArzMXsoN1dcgc0YLFXgHspQjUxQ2qXVUBjxj0"
        b"ml/97Gth0gn0WpjjP1yFf3/KNLxGMriGcq/rywf5tur1QytPEv5iEeikQZ2R6NoB4Z9mfR0Z90F/5w8DfqQA6pNgU2ojreM4LCwB"
        b"CZsqAWNPgb4HwhVFNz4M+g6MvfH/pYUXFNn0P18V/H2uoYqB7H1d+69vQPk9jGtInhg1VdLOH63wu5hm3byv8IefH3WK83CoGFQ3"
        b"R5IxGFO//5AIptQc8OUKPa2xxFDYf4fxUUStONoR3QMIx5J6mGbdgoz31z2Fv1LABujFYtg7YPkt61VUMfrSCmX2h2GY0SG0PCSm"
        b"Wd9Axl0x4PMf9P0xBYtZnYA/EIL+3UnTgeM/F7CK2CLgAlHCbwdlGpEw+TCGqdatqHUfqMydP9Z1AkyqdnGGLYOp9KuoYsTCb4XM"
        b"6hHV9zdbd6DO+8vBhT/cuILdNfZgMSEVYRT500jLrqKvroAZ2FXEFdTWS1wG0Qi/gGAck60fod79PQSG2NhTMSksxEMJSHB4avzb"
        b"4zENn1YRd4TDOvPFqJp7QuH/BRrd/w2EfwiCHY50qggTIBZKQKrxVK7XA8jllAKsIPVZRRwQ1vertt6odn5wTLJ/jSb35xAgAs9h"
        b"LEtKV1baKmbjpASkFNANKvN8hH2rbQspgApSnVWMN8IS3+iae3zhb3TuwiT7R4HwDwcMUlGfVeAyZuOhBIKaeYaf02+UBvSqKfMq"
        b"hrp0Spbv90cDX/gb3Hsx2f4eBEsFz4+At/8gHYmxBhtDJUDWPk2ztUovw8Vv6amqBVDFsHP90RT6eEr467yHMcW6DZJRndwIp4qQ"
        b"GetGclATXAnQPHudxlfdxG5ty8mWZRqlASvNe6pinNJ9NLMvmkIfD4JpyHj/xFTrGwAb1JQwEoTtypUMVmYloHZ/Q4NVehZG9nvK"
        b"XmpvEWWevlnFRACtTfL5yfwfvfBTtF9D2nsOU62b1O/+iO5RTO2hP3UqXAGUt2KQEn9hN9zH2U035Wk6EPFu0dNucFOriqCKfUBr"
        b"g6L9uUIUQT8/1ZcUmzHNugEchYCTdhRGaLhqbSIsQeWDlaFiUMKDmdLgOjezb7beS/MAaDoQvUSdALZ6z0S4eFVEHvGnXZ+YfKMQ"
        b"ftqGTLEb00rXQ5PdkKAx3RF4oFTWThZALIZcxswSkHCRTOqwi/fjxBnXkvCjfVn/haJ24NACiDWC6VNV7IVoAnIHZE1XEf8h0AAc"
        b"ApRr5tBkDtNKX4UptkCwCBl8VYRSqEAgM6jEFZUPNtBFOPJWYunBTOiwSuuQt97DPvIRR0rJaB5A+A6d7nPcFQAdH00Js21ZUVWf"
        b"5QTdQY0DpknMTtFbcPR5JPykBEaneP1CE+rpn2p/A0nxLAQrA3c/LQzLBcwxGicXdyVAQT/D0OC6WyCdS9j3v7JTmf6sn0VDQafp"
        b"1nFWAGEEeu36PmzeUoRtexNCwUcB4nWcNT2F+cfWqerOqNa98vuzQDGSkQxkPnA0l76rSDzL1tlHAkEKIEbUgOOmBEj4NY1Dyh5I"
        b"+Xb2rS9toJSfGg2+F5QFgJiCFjTtco8/043nNmRhGmWaplOhsG0Xz6ztRT7v4czTmyL5TEXpVQoGeEQU9Jts/wp13p+G3twzEhCF"
        b"meWCxY0WbKyVAPX6q8nQzIZVfBe79UuPydZWnbUtITbHfaBDxtMCIOEns393h42Nm/NIJrR+av8qfFARRzqpYfPWAo6cU4MZ0xPK"
        b"ZB/p/VSutOfP7hu93+/n+uud+9Fk/wyCDbO+f7hQlEQepOtNnDjAsJUACT8X0LiGkvthduuX/uILf9t+hZ/AJUMxrmlA2v27e53+"
        b"QFdV+PdE2AMnhERXr62u12hBOz9Re41O+P1cf423FlPsOyDZGA2gJor4UiSsJLFPEe4nO0AqW0A3NTjWVezWpT85lPATdLL44nq9"
        b"aHGT2V/FgRFydVI8YDQKMqTyzo16cKcf8TfFLjRbN6vgn1T71RhFbws2UBufifdjZgmkhAczqaNYvIF9+wtfVz7/Acz+waA0YCmu"
        b"CoBqz6c2J5DJaMjlPBXxVnPgx/vAYgAVWWcSjiORSumY0ZxU5vsoZqWqfP9oj8oXfwtTrVugi12QyvQfoyo9ClqQBRAOizlMlIAh"
        b"4OjrGg3YhR+wb7ddR8KP9iVD0riUBVAWANHEI4bR/1SK44xTm/Dok13IFVxV6xGrAx0nqJ5ODqSTOl55aiMyNRqcEVq/YbXfSP9+"
        b"TzovjinW95DynhkXLj/VGlxy1LSg2M0JKAvluHT195YM72fdv9G+kfg3KVs5WIsgj3koH6MsAFKWow/6lK8DbVpzAucvmoptO4oo"
        b"WROk5DMCJBIaZkxNjlr4icufTP/RBv08aGh07kadc3d5cv1DAa2NvAXUUJXhxIYE3FQmoVt568HEFS9+AFf83sHSVqL5HLKE6JwK"
        b"smPsZtOapMWdTHAcd1RN7JTUeIKUNpn9o9m56TN6s761NVLfn4xHTwX9nsdk6weQqq9/nLQ0jTUrOmB0YSjCNUE3CyHgpetMvZR3"
        b"/unY3tuT7PdZ2drKWVvbsIItlAYsxV2mwgyAZQ+Mi6jCp3UZzdAj+jsK/JWI1JOPxuwHdLcPU0rfhGZa8JxkMFh0HO4SfSeVBRds"
        b"sPrUhEwdCRL+jK6VCvb6fMl58+TZ3+sg05+x4Qk/gfowi3Tz4xYD2B/8sX9xV1eVA9r1idtvVCAyDp3DeOrHyO5+Cc68WqSnUi4e"
        b"kO7BJwiXBWoVM8icpYaGTjQIIUUqZWiOLbZCsIsnz/7eFimpxLdtRP4WMQKVoj/MKuIOivsUir5VNWK3SgpIk8PY9BCMTffBLtWi"
        b"b41Ax2MMha3B9LZRtPqPGGTN0KACyghMoGyAEFIkExr3hNfpWNZbUlNvfcEXfr+1d8QKgHYCGhYU7eFWEWdQpiyXH43wS0iNg2d3"
        b"w1z7Q0hugGlSCbybA3pXM3Q9CVidvhJQ8zvHUhHQms6WJkwMQEopDFPjgrFcMedcWjPje08sX75IH43wE7imoRANzVMVlQLaFKne"
        b"fzRlw37hGZBY82Pwwi5AM31/m56mqVPUvdnF0PVPhu5VgEuEIsbAn45JMLBgRTWvbFwhhJSazhnjsKyc9866Od9dKZe36ueeu/KQ"
        b"hT6Hgi4FSiq3TuMCK/s6VTFEUHqcdv8RIzT9N/8d+tYHIc0a1Xo+8Lr/T2j+F7cx2F1A5kiJ9CxfQaj4QLnXmyeVFcAmZ/ycOatM"
        b"4dcNLrnGhJMX783Mvu2PUrbqjB28xHc4/SRFMgcr8/JUMVxQwJf8fnukvr9iZmFghRzMZ38CyfWDvDf4TgMQDtC3jqH7SQanFypI"
        b"OPg9ZQFFtnNkBXgVGQsQ/s4vuc65VXQ/lJ51W3uUwk/ggiEfEKpW3hWqYkTym8+PRu7I92cwN/wOvG8zoCUOnWoLisxI6K0uoOsJ"
        b"htxLflxAEQLLMqcE+4oVt7qlgNQ0Jo2Ext2S+LfMjO/8mMz+KIWfoDMPObKUOFczQ6uYwAgrK4nnb0QbIq1KnYP3bIPx0l8g9fSe"
        b"pv8h/953C2idZZ9lsLsl6o8FNPIgiJWClSkWkLXA6pKAURlsQZLkUWMikda1Qq99Zc3M278j5TKNsUM39wwXFJvNCRrQEnxx1F9Q"
        b"RbwUAAX/RkWhT52HL/4ezKLZYAcx/w+EMD5gAKWdDJ2PM5R2ldkl8ARkTxGVAKnaKZhI1BhaMet8tmbm7bf4qb592XyiAGcGspxX"
        b"BjFoFaMDpXuLZA2PYvfXurf6gT+DquxGsSYplGAAng30PM2QfSGwAMqRLuRBLKBIBIcs5sIPkcwYWrHXWpqefttXSPjB2svWS80d"
        b"B1QLVlUAExwhy++oyD4YYGy+G8zOBiH+SOgC1efmXmDoecbPDpSleIjGYHfnY9shKEn4GUSy1tRKWfvL6Rm3tynhR/uQO/tGAp5O"
        b"I6t6OeKrGKuIAHR7yfwfGauyT87I8z3Qtz0MqVOtf0QW6V4uQdeTTBUSKZdARu3/OH5xUMysAEmud7/wW19JTb3tc/3CX+YSfV4q"
        b"IU/MwJUWJa1ieKCNj9p+R5r6kxzQt/0NrLh7ZL7/Ib/DF3qnz88SWLvLoASoObC74KcFY7LjST9DIlJ1Ca3Qa30t1Xz7Z8dK+Anc"
        b"mgSXAXktHtejinKZ/+4oiuIYB3MF9G0PBmF8lAdBloBqBrqfYihuDaoHo/q+YICI6KQqqHi4AozBS9aZWqnP/nrN9NuvGUvhJ/D8"
        b"8/CkRF75YnG5KvuBqjKN7dHF+xxD0g/hjTQhDWjdL4D3bICkvH85+f3o/IO4QO8ahtzGCA0O9dm03VmQvePsCvj32kvWJzQ7Z38z"
        b"NfXWqxSbzxgKP0E/5hi43Z3IxcQi2i9IKCiFS0qq0qdAHwwalciKKKi59oTiUrBG8QEM0HY8CuYWIc266Pz/AyFc/hzoe45BOBK1"
        b"x0T4tSRhXXmwpA4kjDHfWYKv81INCc3qs7+VbL7tE77wt1EgcEwPRqdRQZ0dMkeLL44WQDgfYFeHjZe3FlEi7vfYHWU0SCQ4Zs9I"
        b"YeqURKSKjkq9Kfo/ItIPMv9tD/quf0Kqhp+xnc1GLkH2RQbpSdQeF5HxoUaKS4jdOfDp9T7//BgtKvU1DF6KzP5e65upqeMn/ITQ"
        b"uOqNowWgdn4DeGFjAY8/1QXXm9gdSzTU5YWNObzipAYcd1QmEktA+f+2bzkN+7P6c/8vgOW2ROyQDx30tblNDFJI1J0waCDCqP0i"
        b"11cC02rHMuAX+vw3pabedvV4Cn+/AuASfVrMWIHUWDCN+Oo8PL26Rz2XJO57TFwQ2xHt1s+s6UXz5CTq6/SRCe7e69weKekrVesA"
        b"WseqsTP/D3QYBpB/maiiJeoWDDw/KvAgHtDJg45BWW6iIkHCT9H+mmkU8Btf4e9XAALoUy2aMWMG1jUaDWbBsj0YBo9rDUdkUDEq"
        b"4np3JHZ2WJjU5CuAUROHjnT6I9PAXFIAq4k5Ynw9xFAJbFX2u68EZERVgj1FtdhYQ6osSkB9ooRIpTVe6rWvr5l2+2fiIPwE5RUK"
        b"iayib4qRBRCCzL7Dr0ZB+ucdASjyPyL/X+0GVD3UDda3CYJT1984z2YfpAT61oWrNyIl0JmH7Is+M+ALvxSpGp3/ttN4ODXtts/Q"
        b"BL84CD/CS8g19I7/oewL2v0mT0rA0P2xVyED7kR++EFPjimTRh8IDNLeI/wcIvsEjJ7nUOPuUnEyEQf++EHuQN+zQTtxFGCA7Mj5"
        b"PQOjn4muEIYqUpoQrdkGvG1r41NEvLMEyyiUFQuJUy6AJtDruf5s0bi4AOG4qoZ6HfOPrcNTa3rV5YzNAZYBgiruJHDy/HpMbjLg"
        b"ROD/k/k/EteOalOTDLh/ZzOsvvPwhoZnUMd6UZRJCDCMazQmUAKFzQzcCFKEEbUTy11ZvyCpdnSThULhT3OJ1o5a+XmvFoae7yJj"
        b"Qz5+H2kYLzYKwAGy1CceO1KQQAmccGwtajM6Nm0twqbJQBMUNOCT0oBzZqXVwI8obgZlEkYW22GKO2BVh4HHi2/GYzgLFyYfwmsS"
        b"TyLFLBRlwh9PNl6KICgdzr3oK4GauRErASHA6tMjap4I2cdI+D/TkcH1nRnoTRIpyajSOVbwLQANvbFUAAHoHsyZlcLsmamJXQhE"
        b"k565L7RRQAUAR/hZlIEp2AK7evKo121s9ybhe7m34yHrNLw9dT9OMdfDkToc6OBjNfl3bwQEpNn1vhJIzYxICTDfHSD/iU2q6Sc7"
        b"HQrUuD7KWDGJT+2uxc1dNeDEuyUlEhy2elN2eizMf4Lv7Li+BaCGisWUFIRaWUc6/bZSQP46BeyiVJzBfR1+CpYBu3otZIseGNdg"
        b"wEENK+J55wh8LfsB3Jl7K3IyhTQrqtjAuLLKc6B3HWB1RNhKzJhqHJI7yRrweRCHIvwUkjCZxL/trsPNXRloXNKfq/xugrPRULGW"
        b"UQHo6PE8UA1yzKcCTWwokzqikwwDilRXMHz/X6oU7M5eC5ZLcRc/JkCCnmQ2NHi4u3QOvtD7ETxuL1CKgawAig2MC9S5MvSuAVya"
        b"dRBVxpITnVgJYlsvpHXwISMk/KR76FpfvrMe3+mugcZF6Ogz0u4G492IpQIogSptnDgrgCqGDxL+EfX/BxteZ9aBs5cGCYW8hhWw"
        b"y5uEW7LvxY/zlyjlkGA2vPHIFATEIl6JoWcVIMjqiTBFCMuFJCVA9QKqUWlPQaFLbFC8CsBlO+rw45409AHhV+Wr0vGgSb5LPbN7"
        b"bWysbHWZei1VCmxHlP2oIiagzM5IQDkqah3enbX8TW8/y5UE3mAODLj4Q3ERbui7HNu8ZmQCl2DMEbQSO70MveuYGlgaGbhfgajS"
        b"hDuyfpCGfCQSekkmP2BJ4J076tHem4KuCbh7diAy4Xm2a+qb1XNr5sdLAWgaLEi/HyAu+ckqIkijjtACoN2TLN6OrAVdI/9+/0uC"
        b"3AJ6JcMKeM6Zhy/1/isetU9Uv/uvsXHJDJS2A/mXIqYWYwPcgmJrL9BVUDGbFAUhBcPbtzfg99kkdE0OCH//3yofqtTryB3q96VL"
        b"46UAZs9WjEDdqh01NodWxWgxkoxJ2I7vegI9eXdIhXG046dYEXmZxrey78HviuciySwVFxgXJaADuQ0Bq1DU/ILcD67YnXmkt3Zh"
        b"Z5+DN+9owL3ZBHS+H+EPvjzBmddc6gzUcXx8baUAGGOuBHqUAhjvI6oiEpAiH6n/TxtWwfZQckS43oekBHS4KkD40/ybcGfubSo3"
        b"QL+PeXAwOGZyBbxS9INJXTDUcYlNDscbdzThoXxiT7N/n+NhMBgrLEoeHbs8FpcUPvU1QXcQaa/qgAmAkWYACBQL6i24ygoYzgfQ"
        b"bk+GP2UF7i6djW9l3wUbBky4YxsXCOoDaCBp3/oIewYQCD88rEYCF8kj8RTLQGMk/Ae8TiqNwhh6r1hINXfxQkC+pHYLZQFUFcDE"
        b"wUg6d8nfp/hWX9GBSwnsEZQQ045PcYC/2yfj5r73IivTKkMw1kqAyoVLOxgKW6IhGHUD4X8IabzeOxLPy4SycA55mUkBSHScyT/i"
        b"K4AYxdnojvh3RUM3dXzG5siqGL0FMMKbqVwASygClpGKLAl7LStgtXM0vtb3AXSJehUXGHNLgAPZF5hiGx5pfYBUwg8l/H+QtbjE"
        b"OwJbYUBDf6rvwCBh10hNYJtqLQgs7riAP/FEYLsI9MS1JbiKkWFEAd0gBlByPNWcNJqAFdUEUFpwgzsbX+97H3aLRlVINKZKgLoY"
        b"bVIC/aQcI2rqqYOHH8sGtIg56IGmApxDMrAkE9AZilJs9kMyS6PqX4wE/XeCMXQSHbPfq1zFRIAYYQyA/sRXAKOvTCQlQDGBje5M"
        b"3NT3PnR4DUiMpSUQZAWs3QzFbcPLCojgWmQgcINoxvvFLBC3KgU3xTDrKhLgNIELeGJ7vCyALM0FUj+hM6hDj9UBVjF8kNCObAKQ"
        b"f/dJ8Il89QA1QCNWApvd6bg5+150e/VBTGAMlxr3U4NeYWhZAQ8MFDYwIPExMR3XyWn9HQ/DurRMMi4lamLYCETgixcHpCUOuqjh"
        b"Jii5j9VBVjG2IAXgUAAhwpVASiDNSsoS+Eb2PciKNEzmjo0SCEuFi0DupUNnBVww1ECgAIZ3iNn4tpyi/P0RcZFSHNWTSDA1gzN2"
        b"6NeFUkOXbavppNQRWMVhDL8pKXrBJLOfegjWu3PxbUoRSgM6vLEpFgpcgeI2BrvnwK6AqyL9LjbCwBvEXPyfbFD1DeTvj0gsKDPq"
        b"ejCZ1oUYgi9d6v+gu+jgHFacG4LCyTkT+RHVdRrxfaT0GQcSAQ1b1LLpQUOG5fGMcyzuyF0aFAyPUQ86uTce1LShvb9QBmY/Cf8D"
        b"MoPzvCPxqExDg3uwHP+hoAZ+S9ulOOBO9czi8SJP2D8YFQJRs1J3t2yQLjZpOupcV40qjo0qkMGiVHUKsTmqMqXuvJEH7/b+rB27"
        b"hj8PQAiJ2hTDn5/qxC8f2YaaRHnYmCmKnpNpvDH1ID5Q8ztFNTYmCSgqdPGAxpMlktN8AhFa7OTfp+DhO3ISPimmowQWpPlGcSP8"
        b"ogrGhSjNZuyETWe1vaTSgIzFxsbun7rW0IBST6cqBqpTsYCYCBpdKepNd1yJ7bsslEqxUqCRTwZqnpSAafp0XKO6B8xXmsPuBwiI"
        b"RKfUmTB1RWRTFoggRfiX4qvRzLtxcWql6iUYE3YhCeQ3AYnJgOQcXFIbM8OVcgZuEZPUW/w03+iFgFwpDcwq1KIjeAZxwuCxi46Q"
        b"6NI0zAlqAVhcxoJ19zh49Mku9PY5E3o2ANXd19UaeOVpjZjcZI5KCbCAYswZmcuKWU0ppE0NxaAfoBzwiUct/KLwBkzTOrDQXIu8"
        b"TJVXCQSxALuHo9TBkJ7aByaOwifkEfiu1wudSeXvRxGcVK4N56iH3PY/kPYSxA+czH/6gWYEMqg4QGyKgdSQDFsq4e/sdqBpDIY+"
        b"cR90fkrZPdGFkiXU+Y/0RpDQkvIc7g7ez8Zco+OIKWk4nlc2JmZ/l/H59r6ffxu2us1jkx5UeUCBwotZ5HEONiaX4h9sOihTF7Y4"
        b"j/orfA/AO64uiX+pTT3xjhPb7FYaBhIT2QqhEiJS0gBo9duWYNHIuOz+O3aX0N3rIGH65uhEf5D535d1sH1HSZ3/iO9EoABGAqoA"
        b"NHXgpNm1EKK8XGwkcEQs0unV48782+BKv8quPJkBOhfyJ/1JQPnZH8KOxGdQYI3oFbsgmB6J7aFSa1LiuKSJS+vTqNe1NcFtjF2R"
        b"XXhA6mpLgZfVBKiYQFWkTWCf/0AgRVC0vFHFAJQyMUY244JxpghBTjmiHs31Jhxix0X54KcHS3jGOQa/K56naMcjVwDBrg87C9F0"
        b"Akpnfx728W9VJJ4FmcduWejft0f1NUEdxStSpnxzJqnZuRIKrniSXlvbvmDcN9a9scfy0Di20CTZOPj/CC5kXUaPTUByzHLwnKlY"
        b"wIir+UILyvAzJyM5BooDTMpoOOuYRtikAMoVCBikBKhQ6K7ia/GUc4L6OZJyYdXjzsEcny3UPuHdKJ7dBtF0NGBTUhLYIbMoKj7x"
        b"kX9feHVo5391jYkLahKQGmOu6+VMkz1Fr7W3tMRuNwvPWB2Y8LAun1dZEW283QA11cYFmqckMGtaGqWSp5RzWI01IR8SKJQ8TG9O"
        b"YkZzctSZAAoCkhUwIlowBsULuPiEyZjZlILliLIqYp+JSMKTHD/LvxF9omaURUKBue/ZgJOH23w6iud8Ac6Cd/q9wa5QNNjE5/cC"
        b"mf+SGIxG5qCHdgMJ0+trU3hN2oQtIbSESc898bWjrtmteDd4fNJ/IUIv0Q8EGlglXHQbBpooFTjeCFMRr3xFg0qRbdlehCCSigkK"
        b"rnEcMSuNkxfU99fzj3Y0WDIJ5Asj+NtgTkFdmuNtp0/D7fdvooR5WavEydGgbsGX3BmKaPSymj+qrMCwxDKkA/ZsMM+CVz8PzjFv"
        b"gztrsS+hNl1U3u8SMADPe1Sj4xCvcVDwO3SoT5FAijO8MZPEsQkdRRoASF11VEzF2N0UVG9dvlTH/hjDxhn9YaKgIKins0OuMk0s"
        b"cpzxTwUqf0pADQc947QGLDi+To0Gi50ajQgJU0M67VfgDbeAZ3+ga5cw/WDgSKwAsvpLtsQrjqzFRSc3464ndyCT0lWxULlAIknm"
        b"/z2ls/EKcx2OMzaiJJOHSA0OmqzqlsCkA6/2CLhzL4Iz+3zIVArModeCpoDw/MBQkMCzHnF1Dj8A6Pv7Eo2ahjfXJTFD15Tw+xN3"
        b"mWb1FaXk2t3069oYUYHvowAoFSglzYKFy4BHSQHk8/1TjsYV4X0lJuZUgiOdHPdDKhvU+g3M/qjMbRL+ZALI5UcYEGQMJUfiLQub"
        b"0VNw8NCzncikNDUpvBwrmkx+EvaCTOL/iufhGuOHimNoP0c2cJGEC+aWILkB0XgM3FnnwZ31GohUGoyuZ/+uP3BR6dgNMOySBawS"
        b"fp/wcJp8lS0kJeaYOi7OJFGnsX7hl1IKM20yO28/tfXYWU/TSbWz9tjxARIGJ4p8N0Dinnwen1YFTDGCMokpBjBxPYBIBX/wZ6bT"
        b"QL448s9Qk60kcNmrZyir4K/PdiKdoHTdqAboHjIguMo+Fo9ap+DViSdQUAVC4Q4uA6Gn7nxApibBmXwO3FmvhTflFEiDDxJ8Pwi4"
        b"73cIpBjHP91NyIosGKPGXzmMYB9wUsoP9mnBXIBB3yKZrnHJ2E/a2RKvdXmr3oa22Jn/eysAJVoNNh7pktiSSGKWZak4Say23MMp"
        b"IxAFQjcgaQLF0gitgOBzNM7w3tfMRFPGwJ+f2gVbSiR1TQlO9CXDDBqT+Iv1GixMrFXce1J4yq+XlKtONMCduhBe80L1r0w3qhCF"
        b"L/jeID//4Pi796Ly/zVFXSqHsOv7a3BxJoFXpQzlVdCj/5uoqEbXNKuvlGVFa1kcG4D2qwDIDVi2TGpsJit0dcq7Uyl8iBRAHNyA"
        b"KkYH2rUzGaDkb5ijGzQigbcubMaRzWn87vEd2LirAEPjqpJRcQIPM30UKnTVe6bcPV+ZkFBBlLDKmoYH2Il4U+3jyGaOAJqOhTdp"
        b"AbxJJ0Kmm6iNHYyMazeo4VOCf3Dj1Tf/OXZKGyvd9QAzVb7hYAitnYwGXFRDwT4DpUDrDd6TyDYxahKa11dsv/m0/9zasqxFa2Nt"
        b"sTT/CXvUirW0+P96Ar8oFPChYNhpFRUOFaVOAKkkUKDxdiNU6aGw5m2qEsxgXvM8/O25Ljy8vhtbu0tKeEkZkKXgU8z7Qh3unKFq"
        b"8H8OhF1NL6L0H607GkrKVCtyytTQXJtCc2MNaie/G71T3wknM1vlNdVOrwr2JZg32L8fmnlI5n8t47jPeRHbvQ4wZh7Q/A8/kYJ9"
        b"s0wdr88kMEXjA8G+PSAl55zbuZLFGW6iZ+a3LAuYVSpAAVA/QDAnYEXnbjxdU4OTC4X4uQFVjACq0Wh0VsBgi6JoQwn7RadMxpnH"
        b"NmHtlixWvZzFizsLilKcujepjyCMEZB8kmLQaDFRazen/gdNNRzVpXU0pA1MrjVVF+Lk2oT6N5PQletCx05lIIzsUapHCD8wzPUP"
        b"+1Iw1fBzl/NMwFVEv++rAELFRTg9ncBr0yZ0NhDs2xtSQiTqklqpp/DLm0/49Fqq/W9j6qhji32qxVesgHbuuczt2CV/aJq4mbIB"
        b"Vb+78kELOZEAajNAb3ZI4+6HMi8TeUuqHfusY+rxyqPqkS8J7Oyz0Nlno6/oqunCtEmbalfnSBoakiZHTUJDfcpQP9P8QUOj9wSc"
        b"CGJgriEpGjVbg/45QEBvOKA8f4oxrPZ2Y6X7LMASanbRgaL8GY3jvJoE5icMOFKqx/6FnzJ+nFt5Ky8gv+JXMBHbThsqTQGoq6Gb"
        b"+FFvD65OJjEjjsHAKoYPEq7QCrDt0QdUQ+owGSgC2lmTBse8KSkcMzWldvrBX6GyOMFDDHqEaV5bOf57ug6+oorOhKZvIKOi3Xkc"
        b"tixAY+k9FEBY6kTHdKSp4/xMEpM1rvz9gzoZEsLMJLVib+HGb5xw7XPLli3TlixRjkqssd/zoUAmuQNdu+V19Q34Snc3PM6r8YCJ"
        b"AFXi6wC7O6JhHtof+v39wNdX39v/H/+H8Pex9I5p908y4CXRi0vztyEvS0EB8kDBMe36Cc5xdtrE6UlDHSDVEB1s96O8v5EyuVty"
        b"nmNFLMz8X664dOlSqqyLZfHPYBzovMiiYbki7ujtxbZkkmYIxjeVUcUwXQEDaGwo31UjpUIcApx8/uBBP6vn1COw5sf4xvm7P8P3"
        b"rYeQE72K9IuE3y/eGdj1392QxpkpygzsleLb74cq019CCA+S/cdNp16TX7tgraLZQwVgv+fG/MAFnzOHdTGG1kSiShU+kUBmd00K"
        b"qK8rTyFPHEGR/xrG8E93F37rPAnOEv19DRThr+UMr69N4F/qUpgSVPURDqWkKGRBpr9ddK7/+vFX379MLtPal8Sz6m9/OLBL42cD"
        b"1KO7AysytXh1NgsvbhWCVYwctAv39AF9fSNPDVYKyIBNMIYP5H+KRzwqLErBk8S5wHBK0sCZaRON5OsHGnEo1omUwkvUpTU7W1qZ"
        b"O7bugu72+0R7yzJqm6wYtXpAzpigMIhTIKOnR37KsvCQrkOLG2NwFSMHbXINdf421peduEqAxo9MYhw/sP+JR9y1AJIq8Hd0wsCr"
        b"UiZmG0T+LVEUw5iLJ6VnpBKanStu1IR89x3sI07IsI0KwkHPl4SfAoINDeyxUgmtmYza/SvGvKliiEqgHmisj24uQZxAgb8M41jt"
        b"deGG0t2q5v+ohIZ31Kfw9rokZhkclvQLkYYq/BT046auCSF7hSv/5cYTrt1GOf9KE37CIXfywBVQcZKu3fhzQyMu7OmBy9iBrYcq"
        b"Kg9qJHgR6On1OxIngjWgAnyUYoTAFaUfo4tvxJmpWswwKKfNVC8DYTjmrBBS6KZGBX95t+Rd+vUTrr6b/P4lbElFboxDOncKdFJg"
        b"sKNDztI5/mYYmF0qVeMBEw08SBF29QCWVdlKgFKcNJm70eT4P++v+BtW4CijVrH922F1IoYHKaWnm7pGxE2yaP/LTQuu+3MlCz9h"
        b"SLeYhJ8ahSZPZlscD5cKgaxhVFODEw0U/zIMoHmynyEIn6sEyFDogylSqbTAnMkcGzKrscn4G440alCSvvAPvWtgANSfZFCDAliX"
        b"W3LfRsK/aHmrXsnCj+FeByINYYy5O7fJt2cyaHeoJds3F6tBwQkGEiLLBrJZ3zVQnHcxu8th7p6qg4n/kIhPiAJNNwXqdY7V9ov4"
        b"ee+vA2qrIcwEP2BFDPOStSndLlrPiYL9jptP+szTLZTuq3DhJwz7li5fLnXVK9Ah35c08QPXBfOoBKJaKjzhEFYJlkpANu//G/bD"
        b"j0d/SL/AB2PPiO2IuA6ox8EMGJBDoo/11sv4Wd//wlG9/n7Bz7C/T0jBNc7M2iRzcqU/u07pQ7fM/8/tE0X4CSO6jaElsHuHvCyV"
        b"xo+EAHecakxgooJ2fhIfsggKBb+XgCy/cimDMBsx+PNJ4A3dd1FM0/+ZFEB4bKq/AAJpxvGsvRm/zP4GlrCgK6ovOfxNHxBGOqF5"
        b"tkuVwJ+/6eirvkivtcgWLa70XiPBiCL5JPxkCUyZxn66c6csZdL4vq6jrlisZgcmIsI4gNptTb9Tj4KE6mEPKINQcIeiENRb93p/"
        b"KOhkzpNw044eCj39TK+pwGQ/n0DQTBR8EAn/U9Z6/Dr7B7jwhi/80jf3NUPXjaShOSX7CdcqXXnLgs89SFN9W5cuZXEm9xgJRqW7"
        b"Q3dg9zb5ymQGv0incBQ1DgUl3zHzGKuIEuHOS4E3GiJCNPKkCOhnEYw4Dzv9/K7BgW7esBdgsFCHJj0JfzCfsl8xDFYu+8vz62DQ"
        b"GfBg4THcXViunh+O2S+pA4gxj2tcN2sSsPPWTg75ld6e9bfecfodzkTb9Qdj1EIadg5u2yaPSCVxe20Gr6fAkRBVl+BwwYGEdW+h"
        b"HaD/2pPUd/B7h1qM5A9TEUgyjoKw8Mf8/Xi89BQSjNp91FzeQ32AVF4+GKO8vp4yYWdL3QD7QdEpfPPWBf+1id7Wsoxq+yeGv78/"
        b"RLJLh0qAfu7pktdxjqXJJBLUO0D8CdUsQRVRgnx9HVxN9dngbMUfcvdgi7sNKZb01+P+hJ+oBv0JI2Q0cCLvMNIGpCfhlpyXpBC/"
        b"YGDfven4azbS24NAHzXFVUgidGSIzEynYiH1gYyJnTvl2akkvphM4FwinqCiIZVZqqYLqxgF/JnBDCkG5KSNBwuP4uHio3Ckq3Z+"
        b"Ugw+QbFK/IUOiMpgco1xrmswkgY814NTsHNM5/dD4pfFUuKuW0/8f7lwx1/W0iIqsax3JIjcTx9sDfR1y39lHJ+rqcERQfTYU5Zf"
        b"NWVYxTBAgs1pcJdPziFXWc9iZeFhbHV3yCQzaege0VVQ2IkzWn2cq7FcmqGBaZx2eHpYQsqXNU37q3DFQ0xn9339mKtfDr+Ddvz5"
        b"S9fItra2w4r3oiyBOqoabGmhO8Lk1q1yck0NPgQPH6mtw5FkEZAyAFMNWMQRWbbjqKIy4TODKzmUOuMywSCpgm+99SJ/2HqUb3A3"
        b"QdM0JPUkGNGR0ww+T8ApOhCeZ0spexjYZgm5AYy9wMDWaVz/R0mwzd869hP9tKhSSrYE7bwdS4hp9LDY8fdGWQVvsDWwaZNsbKjF"
        b"+xjHewEsrKkBikWfm076QxPDpiMKDlUVwmGEwGb3/XPi+dWgUdEtZQWyDHhevIgnraexIbcRdsmGyYySFOgDk5sh2WbG2UtSypcE"
        b"3BelYC84Xs3WKQt2F9rYvrs5de1hBfjSxUu9w8XMPxjKLmikZUOmYfX7MqnlLsAiz8M7JXCRpmMODa2gNBLllWkoJmUQ1Hglfy4g"
        b"KYWQCbqqGCoUg+eFMKZIeMLfaTNnlOunMl5KHfb2CWe3u7vvRXvTpqfs1Vs3O1u2kPler9VuNYT+sp2yX0IPdmVOyRX3J+QhWltb"
        b"OZaCBB5YAdG2tC2YHlJFiDETqL0VQfBcOteDV5UcXKBxnCklTmQMkzMZMIMKTlxfMdAjUAykFCizEB57f19H1ZUYfwwS6nBHD39X"
        b"VICU91fFPYb/IOTzqn7AEhKbNIZH8p71ZK/X/UBr5+0vtbe1FdF+cP4JWldLVyzVsBhYiwVyPtbINrSFK6Qq7IfAmO+og/gF1CCS"
        b"wa91dEjqQTtBYzhZSswDMEcKHAOGIxlDg2lCT6UAGg1HyoEKTwYph9BxFHuxVtPzVQti1PdtD2FSwq0YwQeUscryhAU94YPulV0C"
        b"8gWlpAtSoAMMzwPYyBjWS4H13MDq+nq8zBhTUwBCkMEnpFB+euMT9/Hp2emSxmzPXzNfqt3cf1NVyEeBcTWplTJoB0cLCeqAZbDH"
        b"e1ol7/wYMgmGOa7A0UoZAFMFMFspCGAeZ5jEGBJkQlJjCJ1VqCDoMUhB9CuJgM6els/g7tA9rsdEdzn2M8Zv8K49mNFbVXaGFXu0"
        b"i4elupruX2tqFLJtZZ0VGcMOCvuAYZPwsFnj2CQ5NiQS2NDZie45c1jxIKlkNYZPKRifN7wq4GVEjBa4ZIN3k+BJyiQcNC1DbkTv"
        b"LsyQHEdIhlkAZnCGKUJiOiRmSWAmA5olkDR0aCZ1jpl+SSqVrHqDHqGSGKQswp1OLchBlkS/VTHYBRn03EExyF3xO1UPcGr++Q3t"
        b"6g3+m0E7897PhVZRKNR7NNuEJnr4UJz4th+opQf1A0kJyuHsYAzbJLCdAVsAbBXAVk3DpmIRL0+fjj7GmHWoAPHgORz+da4G5Q5j"
        b"BXBQl2Efn38oyoGwcaNMTk6h3uWYwTQ0OwLNHGjmGiZLiWYpMQUSzWCYAqCRAUmacck5dNrlQn+VU9uUKh7dU1GEj7AEdn+lsIcq"
        b"c917J96f5TF4sMbg5pn9PQbX14e/h/tqWLMf/Kum7TEGB0AWUpnnuwDs5gw7XYkOJtXPu6SH3TyBbfUOdmMySowx+puhpIMp9oPd"
        b"uyEpNTxw/FVhjwNirwCGoxxWrACrrQXLZiEXLx6aghj0OWbn80gghbpEGg2Oh0YwNAqGRgg0AqjnHBkaLCs5ajhQK2jILEONlKhj"
        b"HCmyMtTwGZ8vUYmdlIpIVf0cCvbg2vlQQAcrB1Io+/t5kPuiZuOGDylh0fxMCdCoG6poo+xZDgxZKZHjHDlicaLnBdDDJbolR7cu"
        b"0W0J9DgOumcyFDED9oFcsQNct1C1qF8HuxBVIa8MVLQCGIaC2ENJLF7sP9He7u9KozE9g+8ggdd27ICm6+CGAcNxkNR1JDwPSU1T"
        b"/ya4B0PTqVYFhhAwpAZhMOjBz/4xeKpbzgqkUHKO0JSWQsDWNFiai5LnwLI4SokELOJiaG6G9/zzEMccQy3Zo5tJFwg2e+IJ8IUL"
        b"94noVwW8iokFEuK9HpzMV/JTqeWZCFAef1wa9C89R6/vpVxiB3UerQPnsZ9zCM9DnUv4ULG3KqqoYijYR3Hs+WjtFzD1CIRxsFAO"
        b"5bHHZ+xPaPf3qN6/KqqooooqqqiiiiqwX/x/t2y/MQv2KKwAAAAASUVORK5CYII="
    ),
)


if __name__ == "__main__":
    raise SystemExit(main())
