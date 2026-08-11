# -*- coding: utf-8 -*-
"""
MemoPen - 隨手記 V0.14.2

功能：
- PySide6 / Qt 無標題列筆記視窗
- 支援中文輸入法組字、選字與全形符號
- 白色介面，輸入區固定 14pt
- 最上層、項目編碼、開啟舊檔、存檔與 Q 另存資料夾
- 每 30 秒自動儲存為 UTF-8 TXT
- 新筆記檔名：Memo_YYYYMMDDhhmm.txt
- 新筆記開頭：YYYYMMDD_hhmm

Nuitka 專案設定：
- PySide6 外掛
- Windows 無控制台
- Windows 版本資訊
"""

# Nuitka 會讀取下列專案選項；一般用 Python 執行時只會視為註解。
# nuitka-project: --enable-plugin=pyside6
# nuitka-project-if: {OS} == "Windows":
#    nuitka-project: --windows-console-mode=disable
#    nuitka-project: --product-name=MemoPen
#    nuitka-project: --file-description="MemoPen - 隨手記"
#    nuitka-project: --product-version=0.14.2.0
#    nuitka-project: --file-version=0.14.2.0

from __future__ import annotations

import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Final


APP_NAME: Final = "MemoPen"
APP_TITLE: Final = "隨手記"
APP_VERSION: Final = "V0.14.2"
AUTO_SAVE_MS: Final = 30_000
DEFAULT_WIDTH: Final = 760
DEFAULT_HEIGHT: Final = 520
MIN_WIDTH: Final = 300
MIN_HEIGHT: Final = 230
ITEM_PATTERN: Final = re.compile(r"^\s*(\d+)、")

COLOR_WINDOW_BG: Final = "#ffffff"
COLOR_TOOL_BAR: Final = "#ffffff"
COLOR_EDITOR_BG: Final = "#ffffff"
COLOR_EDITOR_BORDER: Final = "#c6c8cc"
COLOR_TEXT: Final = "#222222"
COLOR_MUTED: Final = "#6f7378"
COLOR_BUTTON_BG: Final = "#ffffff"
COLOR_BUTTON_ACTIVE: Final = "#edf0f3"
COLOR_BUTTON_BORDER: Final = "#bfc2c7"
COLOR_STATUS_BG: Final = "#ffffff"


def show_dependency_error(detail: str) -> None:
    """PySide6 缺少時顯示原生 Windows 訊息，避免把 Tkinter 打包進 EXE。"""
    message = (
        "MemoPen 需要 PySide6。\n\n"
        "請先執行：\n"
        "py -m pip install -U PySide6\n\n"
        f"詳細資料：{detail}"
    )

    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, APP_TITLE, 0x10)
            return
        except Exception:
            pass

    print(message, file=sys.stderr)


try:
    from PySide6.QtCore import (
        QIODevice,
        QPoint,
        QSaveFile,
        QStandardPaths,
        Qt,
        QTimer,
        Signal,
    )
    from PySide6.QtGui import (
        QFont,
        QKeyEvent,
        QKeySequence,
        QMouseEvent,
        QShortcut,
        QTextCursor,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QSizeGrip,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:
    show_dependency_error(str(exc))
    raise SystemExit(1) from exc


def ui_font(size: int = 10, bold: bool = False) -> QFont:
    """依作業系統選用適合繁體中文的字型。"""
    if sys.platform == "darwin":
        family = "PingFang TC"
    elif sys.platform.startswith("win"):
        family = "Microsoft JhengHei UI"
    else:
        family = "Noto Sans CJK TC"

    font = QFont(family, size)
    font.setPointSizeF(float(size))
    font.setBold(bold)
    return font


def desktop_path() -> Path:
    """使用 Qt 的系統桌面路徑，支援 Windows/OneDrive 桌面重新導向。"""
    location = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
    if location:
        path = Path(location)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass

    fallback = Path.home() / "Desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def error_log_path() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    folder = Path(location) if location else Path.home() / ".MemoPen"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "MemoPen_error.log"


class MemoEditor(QTextEdit):
    """支援項目編碼的輸入區；中文組字事件仍交由 Qt 原生處理。"""

    request_next_number = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not event.isAutoRepeat()
            and event.modifiers() == Qt.NoModifier
            and getattr(self.window(), "numbering_enabled", False)
        ):
            self.insertPlainText("\n")
            self.request_next_number.emit()
            return

        super().keyPressEvent(event)


class DragBar(QFrame):
    """工具列與狀態列的空白區可拖曳視窗。"""

    def __init__(self, parent: QWidget, main_window: "MemoPenWindow") -> None:
        super().__init__(parent)
        self.main_window = main_window
        self._dragging = False
        self._offset = QPoint()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        child = self.childAt(event.position().toPoint())
        if isinstance(child, (QPushButton, QCheckBox, QSizeGrip)):
            super().mousePressEvent(event)
            return

        window_handle = self.main_window.windowHandle()
        if window_handle is not None and window_handle.startSystemMove():
            event.accept()
            return

        self._dragging = True
        self._offset = (
            event.globalPosition().toPoint()
            - self.main_window.frameGeometry().topLeft()
        )
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.main_window.move(event.globalPosition().toPoint() - self._offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = False
        super().mouseReleaseEvent(event)


class MemoPenWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION}")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)

        self.numbering_enabled = False
        self._closing = False
        self._dirty = True

        now = datetime.now()
        self.memo_stamp = now.strftime("%Y%m%d_%H%M")
        file_stamp = now.strftime("%Y%m%d%H%M")
        self.current_file_path = desktop_path() / f"Memo_{file_stamp}.txt"

        self.build_ui()
        self.install_shortcuts()
        self.insert_initial_stamp()
        self.editor.textChanged.connect(self.mark_dirty)

        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.setInterval(AUTO_SAVE_MS)
        self.auto_save_timer.timeout.connect(self.auto_save)
        self.auto_save_timer.start()

        self.update_status(f"自動儲存：{self.current_file_path}")

    def build_ui(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget {{
                background: {COLOR_WINDOW_BG};
                color: {COLOR_TEXT};
            }}
            QCheckBox {{
                background: transparent;
                spacing: 5px;
            }}
            QPushButton {{
                background: {COLOR_BUTTON_BG};
                border: 1px solid {COLOR_BUTTON_BORDER};
                border-radius: 5px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{
                background: {COLOR_BUTTON_ACTIVE};
            }}
            QPushButton:pressed {{
                background: #dde1e5;
            }}
            QTextEdit {{
                background: {COLOR_EDITOR_BG};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_EDITOR_BORDER};
                selection-background-color: #cfe2ff;
                padding: 10px;
                font-size: 14pt;
            }}
            """
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.toolbar = DragBar(self, self)
        self.toolbar.setFixedHeight(58)
        self.toolbar.setStyleSheet(
            f"background: {COLOR_TOOL_BAR};"
            f"border-bottom: 1px solid {COLOR_EDITOR_BORDER};"
        )
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(16, 9, 16, 9)
        toolbar_layout.setSpacing(16)

        self.topmost_check = QCheckBox("最上層")
        self.topmost_check.setFont(ui_font(14))
        self.topmost_check.toggled.connect(self.toggle_topmost)
        toolbar_layout.addWidget(self.topmost_check)

        self.numbering_check = QCheckBox("編碼")
        self.numbering_check.setFont(ui_font(14))
        self.numbering_check.toggled.connect(self.toggle_numbering)
        toolbar_layout.addWidget(self.numbering_check)

        self.item_button = self.make_button("項目", self.insert_next_number)
        self.open_button = self.make_button("開啟", self.open_file)
        self.save_button = self.make_button("存檔", self.save_now)
        self.folder_button = self.make_button("Q", self.choose_save_folder)

        toolbar_layout.addWidget(self.item_button)
        toolbar_layout.addWidget(self.open_button)
        toolbar_layout.addWidget(self.save_button)
        toolbar_layout.addWidget(self.folder_button)
        toolbar_layout.addStretch(1)

        hint_text = (
            "⌘O 開啟　⌘S 存檔　⌘⇧S 另存資料夾"
            if sys.platform == "darwin"
            else "Ctrl+O 開啟　Ctrl+S 存檔　Ctrl+Shift+S 另存資料夾"
        )
        hint = QLabel(hint_text)
        hint.setFont(ui_font(10))
        hint.setStyleSheet(f"color: {COLOR_MUTED}; background: transparent;")
        toolbar_layout.addWidget(hint)

        main_layout.addWidget(self.toolbar)

        editor_holder = QWidget(self)
        editor_layout = QVBoxLayout(editor_holder)
        editor_layout.setContentsMargins(14, 12, 14, 12)

        self.editor = MemoEditor(editor_holder)
        editor_font = ui_font(14)
        self.editor.setFont(editor_font)
        self.editor.document().setDefaultFont(editor_font)
        self.editor.setAcceptRichText(False)
        self.editor.setLineWrapMode(QTextEdit.WidgetWidth)
        self.editor.setTabChangesFocus(False)
        self.editor.request_next_number.connect(self.insert_next_number)
        editor_layout.addWidget(self.editor)
        main_layout.addWidget(editor_holder, 1)

        self.status_bar = DragBar(self, self)
        self.status_bar.setFixedHeight(31)
        self.status_bar.setStyleSheet(
            f"background: {COLOR_STATUS_BG};"
            f"border-top: 1px solid {COLOR_EDITOR_BORDER};"
        )
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(10, 3, 5, 3)
        status_layout.setSpacing(4)

        self.status_label = QLabel()
        self.status_label.setFont(ui_font(10))
        self.status_label.setStyleSheet(
            f"color: {COLOR_MUTED}; background: transparent;"
        )
        status_layout.addWidget(self.status_label, 1)

        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(16, 16)
        status_layout.addWidget(self.size_grip)

        close_button = QPushButton("X")
        close_button.setFont(ui_font(12, True))
        close_button.setFixedSize(32, 24)
        close_button.setStyleSheet(
            """
            QPushButton {
                color: #7a2e2e;
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                color: white;
                background: #c94a4a;
            }
            """
        )
        close_button.clicked.connect(self.close)
        status_layout.addWidget(close_button)
        main_layout.addWidget(self.status_bar)

    def make_button(self, text: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.setFont(ui_font(14, True))
        button.setFixedHeight(36)
        button.setMinimumWidth(62 if text != "Q" else 42)
        button.clicked.connect(callback)
        return button

    def install_shortcuts(self) -> None:
        QShortcut(QKeySequence.Save, self, activated=self.save_now)
        QShortcut(QKeySequence.Open, self, activated=self.open_file)
        QShortcut(
            QKeySequence("Ctrl+Shift+S"),
            self,
            activated=self.choose_save_folder,
        )
        QShortcut(
            QKeySequence("Meta+Shift+S"),
            self,
            activated=self.choose_save_folder,
        )

    def insert_initial_stamp(self) -> None:
        self.editor.setPlainText(f"{self.memo_stamp}\n\n")
        self.move_cursor_to_end()
        self.editor.setFocus(Qt.OtherFocusReason)
        self._dirty = True

    def move_cursor_to_end(self) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)

    def mark_dirty(self) -> None:
        self._dirty = True

    def toggle_topmost(self, checked: bool) -> None:
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.setGeometry(geometry)
        self.show()
        self.editor.setFocus(Qt.OtherFocusReason)

    def toggle_numbering(self, checked: bool) -> None:
        self.numbering_enabled = checked
        self.update_status(f"項目編碼已{'開啟' if checked else '關閉'}")

    def next_number(self) -> int:
        highest = 0
        for line in self.editor.toPlainText().splitlines():
            match = ITEM_PATTERN.match(line)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def insert_next_number(self) -> None:
        cursor = self.editor.textCursor()
        if cursor.positionInBlock() != 0:
            cursor.insertText("\n")
        cursor.insertText(f"{self.next_number()}、")
        self.editor.setTextCursor(cursor)
        self.editor.setFocus(Qt.OtherFocusReason)

    def open_file(self) -> None:
        if not self.save_to_current_path(show_error=True, force=True):
            return

        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "開啟舊筆記",
            str(self.current_file_path.parent),
            "文字檔案 (*.txt);;所有檔案 (*)",
        )
        if not file_name:
            return

        path = Path(file_name)
        try:
            content = self.read_text_file(path)
        except OSError as exc:
            QMessageBox.critical(self, APP_TITLE, f"無法開啟檔案：\n{exc}")
            return

        self.editor.blockSignals(True)
        try:
            self.editor.setPlainText(content)
        finally:
            self.editor.blockSignals(False)

        self.move_cursor_to_end()
        self.current_file_path = path
        self._dirty = False
        self.update_status(f"已開啟：{path}")
        self.editor.setFocus(Qt.OtherFocusReason)

    @staticmethod
    def read_text_file(path: Path) -> str:
        last_error: UnicodeDecodeError | OSError | None = None
        for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
            except OSError as exc:
                last_error = exc
                break

        if isinstance(last_error, OSError):
            raise OSError(f"無法讀取檔案：{last_error}") from last_error
        raise OSError("無法辨識文字編碼，請先轉成 UTF-8。")

    def choose_save_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "選擇儲存資料夾",
            str(self.current_file_path.parent),
        )
        if not folder:
            return

        old_path = self.current_file_path
        self.current_file_path = Path(folder) / self.current_file_path.name
        if not self.save_to_current_path(show_error=True, force=True):
            self.current_file_path = old_path

    def save_now(self) -> None:
        self.save_to_current_path(show_error=True, force=True)

    def auto_save(self) -> None:
        self.save_to_current_path(show_error=False, force=False)

    def save_to_current_path(self, *, show_error: bool, force: bool) -> bool:
        if not force and not self._dirty:
            return True

        try:
            self.current_file_path.parent.mkdir(parents=True, exist_ok=True)
            data = self.editor.toPlainText().encode("utf-8")

            save_file = QSaveFile(str(self.current_file_path))
            if not save_file.open(QIODevice.WriteOnly):
                raise OSError(save_file.errorString())
            bytes_written = save_file.write(data)
            if bytes_written != len(data):
                save_file.cancelWriting()
                detail = save_file.errorString() or "檔案寫入不完整"
                raise OSError(f"{detail}（已寫入 {bytes_written} / {len(data)} 位元組）")
            if not save_file.commit():
                raise OSError(save_file.errorString())

            self._dirty = False
            now_text = datetime.now().strftime("%H:%M:%S")
            self.update_status(f"{now_text} 已儲存：{self.current_file_path}")
            return True
        except (OSError, RuntimeError) as exc:
            self.update_status(f"儲存失敗：{exc}")
            if show_error:
                QMessageBox.critical(self, APP_TITLE, f"儲存失敗：\n{exc}")
            return False

    def update_status(self, text: str) -> None:
        self.status_label.setText(f"{APP_VERSION}　{text}")
        self.status_label.setToolTip(text)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return

        self._closing = True
        self.auto_save_timer.stop()
        if self.save_to_current_path(show_error=True, force=True):
            event.accept()
            return

        self._closing = False
        self.auto_save_timer.start()
        event.ignore()


def install_exception_hook() -> None:
    """無控制台 EXE 發生未處理錯誤時，寫入 log 並顯示訊息。"""

    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        details = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )
        try:
            log_path = error_log_path()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log_path.open("a", encoding="utf-8", newline="\n") as log_file:
                log_file.write(f"\n[{timestamp}] {APP_NAME} {APP_VERSION}\n")
                log_file.write(details)
        except OSError:
            log_path = None

        app = QApplication.instance()
        if app is not None:
            suffix = f"\n\n錯誤紀錄：{log_path}" if log_path else ""
            QMessageBox.critical(
                None,
                f"{APP_TITLE} {APP_VERSION}",
                f"程式發生未預期錯誤：\n{exc_value}{suffix}",
            )

    sys.excepthook = handle_exception


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(f"{APP_TITLE} {APP_VERSION}")
    app.setFont(ui_font(10))
    install_exception_hook()

    window = MemoPenWindow()
    window.show()
    window.editor.setFocus(Qt.OtherFocusReason)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
