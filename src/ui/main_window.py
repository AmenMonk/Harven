"""
Harven AI PySide6 主界面
左侧栏（专项培养 + 对话列表 + 功能入口）+ 右侧聊天区域
"""
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QScrollArea, QListWidget, QListWidgetItem, QPushButton,
    QLineEdit, QTextEdit, QLabel, QFrame, QSplitter,
    QInputDialog, QMessageBox, QSizePolicy, QToolButton,
    QFileDialog, QMenu, QComboBox, QSpinBox, QCheckBox, QDialog,
    QTabWidget, QAbstractItemView,
)
from PySide6.QtCore import Qt, QSize, Signal, Slot, QThread, QUrl, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QColor, QIcon, QPalette, QAction, QImage, QCursor
from src.utils.i18n import tr, set_language, get_language, get_available_languages, refresh_ui
from src.core.image_processor import is_image_file, image_to_base64, build_image_message
from src.core.video_processor import is_video_file
from src.ui.chat_area import ChatArea
import logging
logger = logging.getLogger(__name__)


STYLE_SHEET = """
/* ===== 全局 ===== */
QMainWindow {
    background-color: #f7f7f8;
}

/* ===== 左侧栏（豆包风格深色侧栏） ===== */
#sidebar {
    background-color: #1a1a2e;
    border: none;
    min-width: 280px;
    max-width: 320px;
}
#sidebar QLabel {
    color: #a8b4c8;
    font-size: 12px;
}
#sidebar QPushButton {
    background-color: transparent;
    color: #a8b4c8;
    border: none;
    padding: 10px 16px;
    text-align: left;
    font-size: 14px;
    border-radius: 10px;
    margin: 2px 10px;
}
#sidebar QPushButton:hover {
    background-color: #252545;
    color: #d0d8e8;
}
#sidebar QPushButton:pressed {
    background-color: #303060;
}
#sidebar QPushButton:checked {
    background-color: #303060;
    color: #ffffff;
}
#sidebar QLineEdit {
    background-color: #252545;
    color: #d0d8e8;
    border: 1px solid #353560;
    border-radius: 10px;
    padding: 9px 14px;
    font-size: 14px;
    margin: 6px 10px;
    selection-background-color: #4a7bf7;
}
#sidebar QLineEdit::placeholder {
    color: #5a6080;
}
#sidebar QListWidget {
    background-color: transparent;
    border: none;
    outline: none;
}
#sidebar QListWidget::item {
    color: #a8b4c8;
    padding: 10px 16px;
    border-radius: 10px;
    margin: 2px 10px;
    font-size: 14px;
}
#sidebar QListWidget::item:hover {
    background-color: #252545;
    color: #d0d8e8;
}
#sidebar QListWidget::item:selected {
    background-color: #303060;
    color: #ffffff;
}

/* ===== 新建对话按钮 ===== */
#newChatBtn {
    background-color: #4a7bf7;
    color: #ffffff;
    font-weight: bold;
    font-size: 14px;
    border-radius: 12px;
    padding: 11px 0;
    margin: 14px 14px 10px 14px;
}
#newChatBtn:hover {
    background-color: #5a8bf8;
}
#newChatBtn:pressed {
    background-color: #3a6be6;
}

/* ===== 聊天区域 ===== */
#chatArea {
    background-color: #f7f7f8;
    border: none;
}

/* ===== 聊天滚动区 ===== */
#chatArea QScrollArea {
    border: none;
    background-color: transparent;
}
#chatArea QScrollBar:vertical {
    width: 6px;
    background: transparent;
}
#chatArea QScrollBar::handle:vertical {
    background: #d0d0d8;
    border-radius: 3px;
    min-height: 30px;
}
#chatArea QScrollBar::handle:vertical:hover {
    background: #b0b0b8;
}

/* ===== 聊天文本区 ===== */
#chatArea QTextEdit {
    border: none;
    background-color: transparent;
    font-size: 15px;
    padding: 20px 24px;
    line-height: 1.7;
    color: #1a1a2e;
}
#chatArea QTextEdit::disabled {
    background-color: transparent;
}

/* ===== 输入栏 ===== */
#inputBar {
    background-color: #ffffff;
    border-top: 1px solid #e8e8ec;
    padding: 14px 20px;
}

/* 附件按钮 */
#attachBtn {
    background-color: #f0f0f5;
    color: #555;
    border: 1px solid #ddd;
    border-radius: 10px;
    font-size: 14px;
    padding: 0 14px;
    height: 40px;
    min-width: 60px;
}
#attachBtn:hover {
    background-color: #e4e4ee;
    border-color: #bbb;
}
#attachBtn:pressed {
    background-color: #d8d8e4;
}

/* 输入框 */
#inputBar QLineEdit {
    background-color: #f0f0f5;
    border: 1px solid #ddd;
    border-radius: 20px;
    padding: 10px 18px;
    font-size: 15px;
    color: #1a1a2e;
    selection-background-color: #4a7bf7;
}
#inputBar QLineEdit:focus {
    border: 1px solid #4a7bf7;
    background-color: #ffffff;
}
#inputBar QLineEdit::placeholder {
    color: #999;
}

/* 发送按钮 */
#sendBtn {
    background-color: #4a7bf7;
    color: #ffffff;
    border: none;
    border-radius: 20px;
    padding: 10px 24px;
    font-size: 15px;
    font-weight: bold;
    min-width: 72px;
    height: 40px;
}
#sendBtn:hover {
    background-color: #5a8bf8;
}
#sendBtn:pressed {
    background-color: #3a6be6;
}
#sendBtn:disabled {
    background-color: #c8d8f8;
    color: #e8e8f0;
}

/* ===== 顶部标题栏 ===== */
#chatHeader {
    background-color: #ffffff;
    border-bottom: 1px solid #e8e8ec;
    min-height: 50px;
    max-height: 50px;
}
#chatTitle {
    font-size: 15px;
    font-weight: bold;
    color: #1a1a2e;
}
#thinkingLabel {
    color: #4a7bf7;
    font-size: 13px;
}

/* ===== 善意提醒 ===== */
#warningLabel {
    background-color: #fffbe6;
    color: #8a6d00;
    border: 1px solid #ffe58f;
    border-radius: 10px;
    padding: 10px 16px;
    font-size: 13px;
    margin: 8px 20px;
}

/* ===== 附件预览区 ===== */
#previewArea {
    background-color: #ffffff;
    border-top: 1px solid #e8e8ec;
    padding: 8px 16px;
    max-height: 90px;
}

/* ===== 底部状态栏 ===== */
QStatusBar {
    background-color: #f7f7f8;
    border-top: 1px solid #e8e8ec;
    color: #999;
    font-size: 12px;
    padding: 2px 10px;
}

/* ===== 分隔线 ===== */
#separator {
    background-color: #e8e8ec;
    max-width: 1px;
}
"""

DARK_STYLE_SHEET = """
/* ===== 深色主题 ===== */
QMainWindow {
    background-color: #12121f;
}

/* ===== 左侧栏 ===== */
#sidebar {
    background-color: #0d0d1a;
    border: none;
    min-width: 280px;
    max-width: 320px;
}
#sidebar QLabel {
    color: #7880a0;
    font-size: 12px;
}
#sidebar QPushButton {
    background-color: transparent;
    color: #7880a0;
    border: none;
    padding: 10px 16px;
    text-align: left;
    font-size: 14px;
    border-radius: 10px;
    margin: 2px 10px;
}
#sidebar QPushButton:hover {
    background-color: #1a1a35;
    color: #b0b8d0;
}
#sidebar QPushButton:pressed {
    background-color: #252550;
}
#sidebar QPushButton:checked {
    background-color: #252550;
    color: #ffffff;
}
#sidebar QLineEdit {
    background-color: #1a1a35;
    color: #b0b8d0;
    border: 1px solid #2a2a50;
    border-radius: 10px;
    padding: 9px 14px;
    font-size: 14px;
    margin: 6px 10px;
    selection-background-color: #4a7bf7;
}
#sidebar QLineEdit::placeholder {
    color: #454868;
}
#sidebar QListWidget {
    background-color: transparent;
    border: none;
    outline: none;
}
#sidebar QListWidget::item {
    color: #a0a0b8;
    padding: 10px 16px;
    border-radius: 10px;
    margin: 2px 10px;
    font-size: 14px;
}
#sidebar QListWidget::item:hover {
    background-color: #252540;
    color: #d0d0e0;
}
#sidebar QListWidget::item:selected {
    background-color: #2d2d50;
    color: #ffffff;
}

#newChatBtn {
    background-color: #4a7bf7;
    color: #ffffff;
    font-weight: bold;
    font-size: 14px;
    border-radius: 12px;
    padding: 11px 0;
    margin: 14px 14px 10px 14px;
}
#newChatBtn:hover {
    background-color: #5a8bf8;
}

/* ===== 聊天区域 ===== */
#chatArea {
    background-color: #1a1a2e;
    border: none;
}
#chatArea QScrollArea {
    border: none;
    background-color: transparent;
}
#chatArea QScrollBar:vertical {
    width: 6px;
    background: transparent;
}
#chatArea QScrollBar::handle:vertical {
    background: #3a3a55;
    border-radius: 3px;
    min-height: 30px;
}
#chatArea QScrollBar::handle:vertical:hover {
    background: #5a5a75;
}
#chatArea QTextEdit {
    border: none;
    background-color: transparent;
    font-size: 15px;
    padding: 20px 24px;
    line-height: 1.7;
    color: #e0e0f0;
}
#chatArea QTextEdit::disabled {
    background-color: transparent;
}

/* ===== 输入栏 ===== */
#inputBar {
    background-color: #16162a;
    border-top: 1px solid #2d2d50;
    padding: 14px 20px;
}
#attachBtn {
    background-color: #252540;
    color: #a0a0b8;
    border: 1px solid #3a3a55;
    border-radius: 10px;
    font-size: 14px;
    padding: 0 14px;
    height: 40px;
    min-width: 60px;
}
#attachBtn:hover {
    background-color: #2d2d50;
    border-color: #4a4a65;
}
#inputBar QLineEdit {
    background-color: #252540;
    border: 1px solid #3a3a55;
    border-radius: 20px;
    padding: 10px 18px;
    font-size: 15px;
    color: #e0e0f0;
    selection-background-color: #4a7bf7;
}
#inputBar QLineEdit:focus {
    border: 1px solid #4a7bf7;
    background-color: #2d2d50;
}
#inputBar QLineEdit::placeholder {
    color: #555570;
}
#sendBtn {
    background-color: #4a7bf7;
    color: #ffffff;
    border: none;
    border-radius: 20px;
    padding: 10px 24px;
    font-size: 15px;
    font-weight: bold;
    min-width: 72px;
    height: 40px;
}
#sendBtn:hover {
    background-color: #5a8bf8;
}
#sendBtn:disabled {
    background-color: #2d2d50;
    color: #555570;
}

/* ===== 顶部标题栏 ===== */
#chatHeader {
    background-color: #16162a;
    border-bottom: 1px solid #2d2d50;
    min-height: 50px;
    max-height: 50px;
}
#chatTitle {
    font-size: 15px;
    font-weight: bold;
    color: #e0e0f0;
}
#thinkingLabel {
    color: #4a7bf7;
    font-size: 13px;
}

/* ===== 善意提醒 ===== */
#warningLabel {
    background-color: #2d2510;
    color: #e8c84a;
    border: 1px solid #4a3d15;
    border-radius: 10px;
    padding: 10px 16px;
    font-size: 13px;
    margin: 8px 20px;
}

/* ===== 附件预览区 ===== */
#previewArea {
    background-color: #16162a;
    border-top: 1px solid #2d2d50;
    padding: 8px 16px;
    max-height: 90px;
}

/* ===== 底部状态栏 ===== */
QStatusBar {
    background-color: #16162a;
    border-top: 1px solid #2d2d50;
}
QStatusBar QLabel {
    font-size: 12px;
    color: #6c6c90;
    padding: 2px 8px;
}

/* ===== 设置面板 ===== */
#settingsPanel {
    background-color: #1a1a2e;
    border-left: 1px solid #2d2d50;
}

/* ===== 分隔线 ===== */
#separator {
    background-color: #2d2d50;
}
"""


class StreamWorker(QThread):
    """后台线程：文字流式推理"""
    chunk_ready = Signal(str, str)   # (msg_type, content)  type: thinking/text/warning/error/source
    finished = Signal()
    error = Signal(str)

    def __init__(self, engine, user_message, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.user_message = user_message

    def run(self):
        try:
            logger.debug(f"[StreamWorker] 开始发送消息...")
            for msg_type, content in self.engine.send(self.user_message, stream=True):
                logger.debug(f"[StreamWorker] 收到: type={msg_type}, len={len(content)}")
                self.chunk_ready.emit(msg_type, content)
            logger.debug(f"[StreamWorker] 流结束")
            self.finished.emit()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class ImageStreamWorker(QThread):
    """后台线程：图片识别推理"""
    chunk_ready = Signal(str, str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, model_manager, engine, text, image_paths, conversation_id, parent=None):
        super().__init__(parent)
        self.model_manager = model_manager
        self.engine = engine
        self.text = text
        self.image_paths = image_paths
        self.conversation_id = conversation_id

    def run(self):
        try:
            from src.core.image_processor import analyze_image
            from src.core.identity import SYSTEM_PROMPT
            from src.models.database import save_message

            save_message(self.conversation_id, "user", self.text or "[图片]")

            full_text = ""
            for msg_type, content in analyze_image(
                self.model_manager, self.text, self.image_paths,
                system_prompt=SYSTEM_PROMPT, stream=True
            ):
                if msg_type == "text":
                    self.chunk_ready.emit("text", content)
                    full_text += content
                elif msg_type == "done":
                    save_message(self.conversation_id, "assistant", content)
                    self.engine.history.append({"role": "user", "content": self.text or "[图片]"})
                    self.engine.history.append({"role": "assistant", "content": content})

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class VideoStreamWorker(QThread):
    """后台线程：视频识别推理"""
    chunk_ready = Signal(str, str)  # (type, content)
    finished = Signal()
    error = Signal(str)

    def __init__(self, model_manager, engine, text, video_path, conversation_id, parent=None):
        super().__init__(parent)
        self.model_manager = model_manager
        self.engine = engine
        self.text = text
        self.video_path = video_path
        self.conversation_id = conversation_id
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        try:
            from src.core.video_processor import analyze_video, get_video_info
            from src.core.identity import SYSTEM_PROMPT
            from src.models.database import save_message

            # 显示视频信息
            info = get_video_info(self.video_path)
            if info:
                info_text = tr("video.info", width=info['width'], height=info['height'],
                                duration=f"{info['duration_sec']:.1f}", size=info['size_mb'])
            else:
                info_text = tr("video.label")

            save_message(self.conversation_id, "user", self.text or f"[{info_text}]")

            full_text = ""
            for msg_type, content in analyze_video(
                self.model_manager, self.text, self.video_path,
                system_prompt=SYSTEM_PROMPT, stream=True
            ):
                if self._stopped:
                    break
                if msg_type == "text":
                    self.chunk_ready.emit("text", content)
                    full_text += content
                elif msg_type == "thinking":
                    self.chunk_ready.emit("thinking", content)
                elif msg_type == "done":
                    save_message(self.conversation_id, "assistant", content)
                    self.engine.history.append({"role": "user", "content": self.text or f"[{info_text}]"})
                    self.engine.history.append({"role": "assistant", "content": content})
                elif msg_type == "error":
                    self.chunk_ready.emit("error", content)

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class VoiceTranscribeWorker(QThread):
    """后台线程：语音转文字"""
    text_ready = Signal(str)

    def __init__(self, audio_path, parent=None):
        super().__init__(parent)
        self.audio_path = audio_path
        self.result_text = ""

    def run(self):
        try:
            from src.core.voice_input import get_voice_input
            vi = get_voice_input()
            self.result_text = vi.transcribe(self.audio_path, language="zh")
        except Exception as e:
            self.result_text = ""
        self.text_ready.emit(self.result_text)


class SideBar(QWidget):
    """左侧栏：专项培养 + 对话列表 + 功能入口"""
    new_chat_requested = Signal(str)       # specialization_id
    conversation_selected = Signal(str)    # conv_id
    settings_requested = Signal()
    knowledge_requested = Signal()
    task_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(290)
        self._conv_data = []  # 缓存对话数据，方便右键菜单用
        self._multi_select_mode = False  # 多选模式标志
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(4)

        self.logo = QLabel(tr("sidebar.logo"))
        self.logo.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        self.logo.setStyleSheet("color: #7b9fff; padding: 8px 16px;")
        layout.addWidget(self.logo)

        self.new_chat_btn = QPushButton(tr("sidebar.new_chat"))
        self.new_chat_btn.setObjectName("newChatBtn")
        self.new_chat_btn.clicked.connect(self._on_new_chat)
        layout.addWidget(self.new_chat_btn)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("sidebar.search_placeholder"))
        self.search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search)

        # 全局搜索按钮
        self.global_search_btn = QPushButton(tr("sidebar.global_search"))
        self.global_search_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #5860a0; border: none; "
            "padding: 4px 16px; font-size: 12px; text-align: left; }"
            "QPushButton:hover { color: #4a7bf7; }"
        )
        self.global_search_btn.clicked.connect(self._on_global_search)
        layout.addWidget(self.global_search_btn)

        # 专项培养区域
        spec_header = QHBoxLayout()
        self.spec_title = QLabel(tr("sidebar.specializations"))
        self.spec_title.setStyleSheet("padding: 12px 16px 4px; font-size: 12px; color: #5860a0;")
        spec_header.addWidget(self.spec_title)
        spec_header.addStretch()
        self.add_spec_btn = QPushButton("+")
        self.add_spec_btn.setFixedSize(24, 24)
        self.add_spec_btn.setStyleSheet(
            "QPushButton { background-color: #252545; color: #a8b4c8; border: none; "
            "border-radius: 12px; font-size: 16px; font-weight: bold; margin: 12px 10px 0 0; }"
            "QPushButton:hover { background-color: #4a7bf7; color: #fff; }"
        )
        self.add_spec_btn.clicked.connect(self._on_add_spec)
        spec_header.addWidget(self.add_spec_btn)
        layout.addLayout(spec_header)

        self.spec_list = QListWidget()
        self.spec_list.setMaximumHeight(120)
        self.spec_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.spec_list.customContextMenuRequested.connect(self._on_spec_context_menu)
        self.spec_list.itemClicked.connect(self._on_spec_clicked)
        # 默认加一个"日常"
        default_item = QListWidgetItem(tr("sidebar.daily"))
        default_item.setData(Qt.ItemDataRole.UserRole, "__daily__")
        self.spec_list.addItem(default_item)
        layout.addWidget(self.spec_list)

        # 最近对话区域
        conv_header = QHBoxLayout()
        self.conv_title_label = QLabel(tr("sidebar.recent_chats"))
        self.conv_title_label.setStyleSheet("padding: 8px 16px 4px; font-size: 12px; color: #5860a0;")
        conv_header.addWidget(self.conv_title_label)
        conv_header.addStretch()

        # 多选按钮
        self.multi_select_btn = QPushButton(tr("sidebar.multi_select"))
        self.multi_select_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #5860a0; border: none; "
            "padding: 2px 10px; font-size: 11px; border-radius: 10px; }"
            "QPushButton:hover { background-color: #252545; color: #7b9fff; }"
        )
        self.multi_select_btn.clicked.connect(self._toggle_multi_select)
        conv_header.addWidget(self.multi_select_btn)
        layout.addLayout(conv_header)

        # 批量删除工具栏（多选模式下显示）
        self.batch_toolbar = QWidget()
        self.batch_toolbar.setStyleSheet("background-color: #252545; border-radius: 8px; margin: 0 8px;")
        batch_layout = QHBoxLayout(self.batch_toolbar)
        batch_layout.setContentsMargins(8, 4, 8, 4)
        batch_layout.setSpacing(6)

        self.select_all_btn = QPushButton(tr("sidebar.select_all"))
        self.select_all_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #a8b4c8; border: none; "
            "padding: 4px 8px; font-size: 11px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3a3a55; color: #fff; }"
        )
        self.select_all_btn.clicked.connect(self._select_all)
        batch_layout.addWidget(self.select_all_btn)

        self.selected_count_label = QLabel("0")
        self.selected_count_label.setStyleSheet("color: #7b9fff; font-size: 12px; font-weight: bold;")
        self.selected_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_count_label.setFixedWidth(30)
        batch_layout.addWidget(self.selected_count_label)

        batch_layout.addStretch()

        self.delete_selected_btn = QPushButton(tr("sidebar.delete_selected", count=0))
        self.delete_selected_btn.setStyleSheet(
            "QPushButton { background-color: #e74c3c; color: #fff; border: none; "
            "padding: 4px 12px; font-size: 11px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #c0392b; }"
            "QPushButton:disabled { background-color: #3a3a55; color: #666; }"
        )
        self.delete_selected_btn.clicked.connect(self._batch_delete)
        self.delete_selected_btn.setEnabled(False)
        batch_layout.addWidget(self.delete_selected_btn)

        self.batch_toolbar.setVisible(False)
        layout.addWidget(self.batch_toolbar)

        self.conv_list = QListWidget()
        self.conv_list.currentRowChanged.connect(self._on_conv_selected)
        self.conv_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.conv_list.customContextMenuRequested.connect(self._on_conv_context_menu)
        self.conv_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.conv_list)

        # 底部功能按钮
        bottom_layout = QVBoxLayout()
        bottom_layout.setSpacing(2)

        funcs = [
            (tr("sidebar.knowledge"), "knowledge"),
            (tr("sidebar.tasks"), "tasks"),
            (tr("sidebar.settings"), "settings"),
        ]
        self._bottom_btns = []
        for text, func_id in funcs:
            btn = QPushButton(text)
            btn.setProperty("func_id", func_id)
            btn.clicked.connect(lambda checked, fid=func_id: self._on_func(fid))
            self._bottom_btns.append(btn)
            bottom_layout.addWidget(btn)

        layout.addLayout(bottom_layout)
        layout.addStretch()

    def _on_new_chat(self):
        # 获取当前选中的专项培养
        spec_item = self.spec_list.currentItem()
        spec_id = None
        if spec_item:
            data = spec_item.data(Qt.ItemDataRole.UserRole)
            if data and data != "__daily__":
                spec_id = data
        self.new_chat_requested.emit(spec_id or "")

    def _on_conv_selected(self, row):
        if self._multi_select_mode:
            return  # 多选模式下不切换对话
        item = self.conv_list.item(row)
        if item:
            self.conversation_selected.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_func(self, func_id):
        if func_id == "settings":
            self.settings_requested.emit()
        elif func_id == "knowledge":
            self.knowledge_requested.emit()
        elif func_id == "tasks":
            self.task_requested.emit()

    def _on_search_changed(self, text):
        keyword = text.strip()
        if not keyword:
            self.load_conversations(self._conv_data)
        else:
            from src.models.database import search_conversations
            results = search_conversations(keyword)
            self.load_conversations(results)

    def _on_global_search(self):
        """打开全局搜索对话框"""
        from src.models.database import search_messages

        # 弹出搜索输入框
        keyword, ok = QInputDialog.getText(
            self, tr("search.dialog_title"), tr("search.prompt"),
            text=""
        )
        if not ok or not keyword.strip():
            return

        results = search_messages(keyword.strip(), limit=30)
        if not results:
            QMessageBox.information(self, tr("search.result_title").split(":")[0], tr("search.no_results"))
            return

        # 构建搜索结果对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("search.result_title", keyword=keyword.strip()))
        dialog.setMinimumSize(600, 500)
        dialog.setStyleSheet(
            "QDialog { background-color: #ffffff; }"
            "QListWidget { border: none; font-size: 14px; }"
            "QListWidget::item { padding: 12px; border-bottom: 1px solid #f0f0f0; }"
            "QListWidget::item:selected { background-color: #e8f0fe; color: #333; }"
            "QLabel { font-size: 14px; color: #666; padding: 8px; }"
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
        )

        layout = QVBoxLayout(dialog)
        info = QLabel(tr("search.result_count", count=len(results)))
        layout.addWidget(info)

        result_list = QListWidget()
        selected_conv = None

        for r in results:
            role = "👤" if r["role"] == "user" else "🧠"
            time_str = r.get("created_at", "")[:16] if r.get("created_at") else ""
            conv_title = r.get("conv_title", tr("search.unknown_conversation"))
            content = r["content"][:100]
            if len(r["content"]) > 100:
                content += "..."

            item = QListWidgetItem(f"{role} [{conv_title}] {time_str}\n{content}")
            item.setData(Qt.ItemDataRole.UserRole, r["conversation_id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, conv_title)
            result_list.addItem(item)

        layout.addWidget(result_list)

        def on_item_double_clicked(item):
            nonlocal selected_conv
            selected_conv = item.data(Qt.ItemDataRole.UserRole)
            dialog.accept()

        result_list.itemDoubleClicked.connect(on_item_double_clicked)

        close_btn = QPushButton(tr("btn.close"))
        close_btn.clicked.connect(dialog.reject)
        layout.addWidget(close_btn)

        if dialog.exec() == QDialog.DialogCode.Accepted and selected_conv:
            # 跳转到选中的对话
            self._on_select_conversation(selected_conv)

    def _on_conv_context_menu(self, pos):
        item = self.conv_list.itemAt(pos)
        if not item:
            return
        conv_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #1a1a35; color: #a8b4c8; border: 1px solid #2a2a50; "
            "border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 8px 24px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #4a7bf7; color: #fff; }"
        )
        rename_action = menu.addAction(tr("ctx.rename"))
        delete_action = menu.addAction(tr("ctx.delete"))
        menu.addSeparator()
        archive_action = menu.addAction(tr("ctx.archive"))
        menu.addSeparator()
        export_txt_action = menu.addAction(tr("ctx.export_txt"))
        export_md_action = menu.addAction(tr("ctx.export_md"))

        action = menu.exec(self.conv_list.mapToGlobal(pos))
        if action == rename_action:
            self._rename_conversation(conv_id)
        elif action == delete_action:
            self._delete_conversation(conv_id)
        elif action == archive_action:
            self._archive_conversation(conv_id)
        elif action == export_txt_action:
            self._export_conversation(conv_id, "txt")
        elif action == export_md_action:
            self._export_conversation(conv_id, "md")

    def _toggle_multi_select(self):
        """切换多选模式"""
        self._multi_select_mode = not self._multi_select_mode
        if self._multi_select_mode:
            self.conv_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
            self.conv_list.setCurrentRow(-1)  # 清除当前选中（避免立即切换对话）
            # 确保每个 item 可选中
            for i in range(self.conv_list.count()):
                item = self.conv_list.item(i)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
            self.multi_select_btn.setText(tr("sidebar.cancel_select"))
            self.multi_select_btn.setStyleSheet(
                "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
                "padding: 2px 10px; font-size: 11px; border-radius: 10px; }"
                "QPushButton:hover { background-color: #5a8bf8; }"
            )
            self.batch_toolbar.setVisible(True)
            self._update_batch_ui()
        else:
            self._exit_multi_select()

    def _exit_multi_select(self):
        """退出多选模式"""
        self._multi_select_mode = False
        self.conv_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # 去掉 checkable 标志
        for i in range(self.conv_list.count()):
            item = self.conv_list.item(i)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.CheckStateRole, None)
        self.multi_select_btn.setText(tr("sidebar.multi_select"))
        self.multi_select_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #5860a0; border: none; "
            "padding: 2px 10px; font-size: 11px; border-radius: 10px; }"
            "QPushButton:hover { background-color: #252545; color: #7b9fff; }"
        )
        self.batch_toolbar.setVisible(False)

    def _select_all(self):
        """全选/取消全选"""
        all_checked = all(
            self.conv_list.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.conv_list.count())
        )
        state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        for i in range(self.conv_list.count()):
            self.conv_list.item(i).setCheckState(state)
        self._update_batch_ui()

    def _on_item_changed(self, item):
        """item 勾选状态变化时更新批量工具栏"""
        if self._multi_select_mode:
            self._update_batch_ui()

    def _update_batch_ui(self):
        """更新批量删除工具栏状态"""
        count = sum(
            1 for i in range(self.conv_list.count())
            if self.conv_list.item(i).checkState() == Qt.CheckState.Checked
        )
        self.selected_count_label.setText(str(count))
        self.delete_selected_btn.setText(tr("sidebar.delete_selected", count=count))
        self.delete_selected_btn.setEnabled(count > 0)

    def _batch_delete(self):
        """批量删除选中的对话"""
        selected_ids = []
        for i in range(self.conv_list.count()):
            item = self.conv_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_ids.append(item.data(Qt.ItemDataRole.UserRole))

        if not selected_ids:
            QMessageBox.information(self, tr("conv.batch_delete_title"), tr("conv.no_selection"))
            return

        reply = QMessageBox.warning(
            self, tr("conv.batch_delete_title"),
            tr("conv.batch_delete_confirm", count=len(selected_ids)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            from src.models.database import delete_conversations_batch
            delete_conversations_batch(selected_ids)
            self._exit_multi_select()
            self._refresh()

    def _rename_conversation(self, conv_id):
        from src.models.database import rename_conversation
        item = self.conv_list.findItems("", Qt.MatchFlag.MatchContains)
        old_title = tr("conv.new_chat")
        for it in self.conv_list.findItems("*", Qt.MatchFlag.MatchWildcard):
            if it.data(Qt.ItemDataRole.UserRole) == conv_id:
                old_title = it.text().split(" ", 1)[-1] if " " in it.text() else it.text()
                break
        new_title, ok = QInputDialog.getText(self, tr("conv.rename_title"), tr("conv.rename_prompt"), text=old_title)
        if ok and new_title.strip():
            rename_conversation(conv_id, new_title.strip())
            self._refresh()

    def _delete_conversation(self, conv_id):
        from src.models.database import delete_conversation
        reply = QMessageBox.question(
            self, tr("conv.delete_title"), tr("conv.delete_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            delete_conversation(conv_id)
            self._refresh()

    def _archive_conversation(self, conv_id):
        from src.models.database import archive_conversation
        archive_conversation(conv_id, True)
        self._refresh()

    def _export_conversation(self, conv_id, fmt="md"):
        """导出对话为TXT或Markdown文件"""
        from src.models.database import get_messages, get_conversations
        from PySide6.QtWidgets import QFileDialog

        msgs = get_messages(conv_id, limit=500)
        if not msgs:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("conv.export_title"), tr("conv.export_empty"))
            return

        # 获取对话标题
        convs = get_conversations()
        title = tr("conv.new_chat")
        for c in convs:
            if c["id"] == conv_id:
                title = c.get("title", tr("conv.new_chat"))
                break

        # 清理标题中的非法文件名字符
        safe_title = "".join(c for c in title if c not in r'\/:*?"<>|')

        if fmt == "txt":
            filter_str = tr("conv.filter_txt")
            default_ext = ".txt"
        else:
            filter_str = tr("conv.filter_md")
            default_ext = ".md"

        default_path = os.path.join(os.path.expanduser("~"), "Downloads", f"HarvenIA_{safe_title}{default_ext}")
        file_path, _ = QFileDialog.getSaveFileName(
            self, tr("conv.export_title"), default_path, filter_str
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                if fmt == "md":
                    f.write(f"# {title}\n\n")
                    f.write(f"{tr('conv.export_md_time', time=__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\n\n---\n\n")
                    for m in msgs:
                        role = tr("conv.export_md_role_user") if m["role"] == "user" else tr("conv.export_md_role_ai")
                        time_str = m.get("created_at", "")
                        f.write(f"### {role}")
                        if time_str:
                            f.write(f" _{time_str}_")
                        f.write("\n\n")
                        f.write(f"{m['content']}\n\n---\n\n")
                else:
                    f.write(f"{title}\n{'=' * 40}\n")
                    f.write(f"{tr('conv.export_txt_time', time=__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\n\n")
                    for m in msgs:
                        role = tr("conv.export_txt_role_user") if m["role"] == "user" else tr("conv.export_txt_role_ai")
                        time_str = m.get("created_at", "")
                        f.write(f"[{role}")
                        if time_str:
                            f.write(f" {time_str}")
                        f.write(f"]\n{m['content']}\n\n{'-' * 40}\n\n")

            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("conv.export_success"), tr("conv.export_success_msg", path=file_path))
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, tr("conv.export_fail"), tr("conv.export_fail_msg", error=e))

    def _refresh(self):
        """触发父窗口刷新对话列表"""
        self.new_chat_requested.emit("__refresh__")

    # ── 专项培养 ──

    def _on_add_spec(self):
        name, ok = QInputDialog.getText(self, tr("spec.new_title"), tr("spec.new_prompt"))
        if ok and name.strip():
            icon, ok2 = QInputDialog.getText(self, tr("spec.icon_title"), tr("spec.icon_prompt"), text="🤖")
            if not ok2:
                icon = "🤖"
            from src.models.database import create_specialization
            spec_id = create_specialization(name.strip(), icon.strip())
            item = QListWidgetItem(f"{icon.strip()} {name.strip()}")
            item.setData(Qt.ItemDataRole.UserRole, spec_id)
            self.spec_list.addItem(item)

    def _on_spec_clicked(self, item):
        """点击专项培养 → 新建该培养的对话"""
        spec_id = item.data(Qt.ItemDataRole.UserRole)
        if spec_id and spec_id != "__daily__":
            self.new_chat_requested.emit(spec_id)

    def _on_spec_context_menu(self, pos):
        item = self.spec_list.itemAt(pos)
        if not item:
            return
        spec_id = item.data(Qt.ItemDataRole.UserRole)
        if spec_id == "__daily__":
            return  # 日常不可删除
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #1a1a35; color: #a8b4c8; border: 1px solid #2a2a50; "
            "border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 8px 24px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #4a7bf7; color: #fff; }"
        )
        edit_prompt_action = menu.addAction(tr("ctx.edit_prompt"))
        rename_action = menu.addAction(tr("ctx.rename"))
        menu.addSeparator()
        delete_action = menu.addAction(tr("ctx.delete"))
        action = menu.exec(self.spec_list.mapToGlobal(pos))
        if action == rename_action:
            from src.models.database import update_specialization
            old_name = item.text().split(" ", 1)[-1] if " " in item.text() else item.text()
            new_name, ok = QInputDialog.getText(self, tr("spec.rename_title"), tr("spec.rename_prompt"), text=old_name)
            if ok and new_name.strip():
                update_specialization(spec_id, name=new_name.strip())
                old_icon = item.text().split(" ")[0] if " " in item.text() else "🤖"
                item.setText(f"{old_icon} {new_name.strip()}")
        elif action == delete_action:
            from src.models.database import delete_specialization
            reply = QMessageBox.question(
                self, tr("spec.delete_title"), tr("spec.delete_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                delete_specialization(spec_id)
                self.spec_list.takeItem(self.spec_list.row(item))
        elif action == edit_prompt_action:
            self._edit_spec_prompt(spec_id, item)

    def _edit_spec_prompt(self, spec_id, item):
        """编辑专项培养的系统提示词"""
        from src.models.database import get_specializations, update_specialization
        specs = get_specializations()
        current_prompt = ""
        for s in specs:
            if s["id"] == spec_id:
                current_prompt = s.get("system_prompt", "")
                break

        spec_name = item.text().split(" ", 1)[-1] if " " in item.text() else tr("sidebar.specializations")

        # 创建编辑对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("spec.edit_prompt_title", name=spec_name))
        dialog.setMinimumSize(600, 500)
        dialog.setStyleSheet(
            "QDialog { background-color: #ffffff; }"
            "QLabel { font-size: 14px; color: #1a1a2e; }"
            "QTextEdit { border: 1px solid #ddd; border-radius: 8px; padding: 10px; "
            "font-size: 13px; background: #fafafa; }"
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 20px; font-size: 14px; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)

        hint = QLabel(tr("spec.edit_prompt_hint", name=spec_name))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        editor = QTextEdit()
        editor.setPlainText(current_prompt)
        editor.setPlaceholderText(tr("spec.edit_prompt_placeholder"))
        layout.addWidget(editor, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton(tr("btn.cancel"))
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #e0e0e0; color: #333; border: none; "
            "border-radius: 8px; padding: 8px 20px; font-size: 14px; }"
        )
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        save_btn = QPushButton(tr("btn.save"))
        save_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_prompt = editor.toPlainText().strip()
            update_specialization(spec_id, system_prompt=new_prompt if new_prompt else "")

    def load_conversations(self, conversations):
        self._conv_data = conversations
        self.conv_list.clear()
        for conv in conversations:
            icon = conv.get("spec_icon", "💬")
            title = conv.get("title", tr("conv.new_chat"))
            text = f"{icon} {title}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, conv["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, conv.get("spec_name", ""))
            self.conv_list.addItem(item)

    def load_specializations(self, specializations):
        """加载专项培养列表"""
        self.spec_list.clear()
        # 始终保留"日常"
        default_item = QListWidgetItem(tr("sidebar.daily"))
        default_item.setData(Qt.ItemDataRole.UserRole, "__daily__")
        self.spec_list.addItem(default_item)
        for spec in specializations:
            icon = spec.get("icon", "🤖") or "🤖"
            name = spec.get("name", "")
            item = QListWidgetItem(f"{icon} {name}")
            item.setData(Qt.ItemDataRole.UserRole, spec["id"])
            self.spec_list.addItem(item)


class SettingsDrawer(QWidget):
    """右侧设置抽屉面板"""
    setting_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        self._visible = False
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background-color: #ffffff; border-left: 1px solid #e8e8ec;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 标题 + 关闭按钮
        header = QHBoxLayout()
        title = QLabel(tr("settings.title"))
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #1a1a2e;")
        self.title_label = title
        header.addWidget(title)
        header.addStretch()
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(32, 32)
        self.close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 18px; "
            "color: #666; border-radius: 16px; }"
            "QPushButton:hover { background-color: #f0f0f0; color: #333; }"
        )
        self.close_btn.clicked.connect(self.hide_panel)
        header.addWidget(self.close_btn)
        layout.addLayout(header)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e8e8ec;")
        line.setFixedHeight(1)
        layout.addWidget(line)

        # === 模型设置 ===
        model_label = QLabel(tr("settings.section_model"))
        model_label.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        model_label.setStyleSheet("color: #1a1a2e; padding-top: 8px;")
        layout.addWidget(model_label)

        # 模型选择
        model_name_label = QLabel(tr("settings.model_label"))
        model_name_label.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(model_name_label)
        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet(
            "QComboBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 8px 12px; font-size: 13px; color: #333; }"
            "QComboBox:hover { border-color: #4a7bf7; }"
            "QComboBox::drop-down { border: none; width: 30px; }"
            "QComboBox QAbstractItemView { background: #fff; border: 1px solid #ddd; "
            "selection-background-color: #4a7bf7; }"
        )
        self.model_combo.addItem(tr("settings.model_lightweight"), "qwen2.5:7b")
        self.model_combo.addItem(tr("settings.model_recommended"), "qwen2.5:14b")
        self.model_combo.addItem("DeepSeek-R1-14B", "deepseek-r1:14b")
        layout.addWidget(self.model_combo)

        # 温度
        temp_label = QLabel(tr("settings.temp_label"))
        temp_label.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(temp_label)
        self.temp_spin = QSpinBox()
        self.temp_spin.setRange(1, 20)  # 0.1 - 2.0, display as *10
        self.temp_spin.setValue(7)
        self.temp_spin.setSuffix(tr("settings.temp_suffix"))
        self.temp_spin.setStyleSheet(
            "QSpinBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 8px 12px; font-size: 13px; color: #333; }"
        )
        layout.addWidget(self.temp_spin)

        # 最大上下文轮数
        ctx_label = QLabel(tr("settings.ctx_label"))
        ctx_label.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(ctx_label)
        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(5, 50)
        self.ctx_spin.setValue(20)
        self.ctx_spin.setSuffix(tr("settings.ctx_suffix"))
        self.ctx_spin.setStyleSheet(
            "QSpinBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 8px 12px; font-size: 13px; color: #333; }"
        )
        layout.addWidget(self.ctx_spin)

        # 分隔线
        layout.addWidget(self._make_separator())

        # === 功德行设置 ===
        moral_label = QLabel(tr("settings.section_morality"))
        moral_label.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        moral_label.setStyleSheet("color: #1a1a2e; padding-top: 8px;")
        layout.addWidget(moral_label)

        self.moral_warn = QCheckBox(tr("settings.moral_warn"))
        self.moral_warn.setChecked(True)
        self.moral_warn.setStyleSheet("color: #333; font-size: 13px; spacing: 8px;")
        layout.addWidget(self.moral_warn)

        self.moral_guide = QCheckBox(tr("settings.moral_guide"))
        self.moral_guide.setChecked(True)
        self.moral_guide.setStyleSheet("color: #333; font-size: 13px; spacing: 8px;")
        layout.addWidget(self.moral_guide)

        self.moral_lock = QCheckBox(tr("settings.moral_lock"))
        self.moral_lock.setChecked(True)
        self.moral_lock.setStyleSheet("color: #333; font-size: 13px; spacing: 8px;")
        layout.addWidget(self.moral_lock)

        # 分隔线
        layout.addWidget(self._make_separator())

        # === 显示设置 ===
        display_label = QLabel(tr("settings.section_display"))
        display_label.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        display_label.setStyleSheet("color: #1a1a2e; padding-top: 8px;")
        layout.addWidget(display_label)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(12, 22)
        self.font_size_spin.setValue(15)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setStyleSheet(
            "QSpinBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 8px 12px; font-size: 13px; color: #333; }"
        )
        font_label = QLabel(tr("settings.font_size"))
        font_label.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(font_label)
        layout.addWidget(self.font_size_spin)

        self.stream_check = QCheckBox(tr("settings.stream_output"))
        self.stream_check.setChecked(True)
        self.stream_check.setStyleSheet("color: #333; font-size: 13px; spacing: 8px;")
        layout.addWidget(self.stream_check)

        self.kb_search_check = QCheckBox(tr("settings.kb_search"))
        self.kb_search_check.setChecked(True)
        self.kb_search_check.setStyleSheet("color: #333; font-size: 13px; spacing: 8px;")
        layout.addWidget(self.kb_search_check)

        self.web_search_check = QCheckBox(tr("settings.web_search"))
        self.web_search_check.setChecked(True)
        self.web_search_check.setStyleSheet("color: #333; font-size: 13px; spacing: 8px;")
        layout.addWidget(self.web_search_check)

        layout.addWidget(self._make_separator())

        # 主题切换
        theme_label = QLabel(tr("settings.theme_label"))
        theme_label.setStyleSheet("color: #333; font-size: 13px; font-weight: bold;")
        layout.addWidget(theme_label)

        theme_layout = QHBoxLayout()
        self.light_theme_btn = QPushButton(tr("settings.theme_light"))
        self.dark_theme_btn = QPushButton(tr("settings.theme_dark"))
        for btn in (self.light_theme_btn, self.dark_theme_btn):
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton { background-color: #f0f0f5; color: #555; border: 1px solid #ddd; "
                "border-radius: 8px; padding: 8px 0; font-size: 13px; }"
                "QPushButton:checked { background-color: #4a7bf7; color: #fff; border-color: #4a7bf7; }"
                "QPushButton:hover { background-color: #e4e4ee; }"
                "QPushButton:checked:hover { background-color: #5a8bf8; }"
            )
        self.light_theme_btn.setChecked(True)
        theme_layout.addWidget(self.light_theme_btn)
        theme_layout.addWidget(self.dark_theme_btn)
        layout.addLayout(theme_layout)

        layout.addWidget(self._make_separator())

        # ── 语言设置 ──
        lang_label = QLabel(tr("settings.section_language"))
        lang_label.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        lang_label.setStyleSheet("color: #1a1a2e; padding-top: 8px;")
        layout.addWidget(lang_label)

        lang_name_label = QLabel(tr("settings.language_label"))
        lang_name_label.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(lang_name_label)

        self.lang_combo = QComboBox()
        self.lang_combo.setStyleSheet(
            "QComboBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 8px 12px; font-size: 13px; color: #333; }"
            "QComboBox:hover { border-color: #4a7bf7; }"
            "QComboBox::drop-down { border: none; width: 30px; }"
            "QComboBox QAbstractItemView { background: #fff; border: 1px solid #ddd; "
            "selection-background-color: #4a7bf7; }"
        )
        avail_langs = get_available_languages()
        for code, name in avail_langs.items():
            self.lang_combo.addItem(f"{name} ({code})", code)
        # 选中当前语言
        current = get_language()
        for i in range(self.lang_combo.count()):
            if self.lang_combo.currentData(i) == current:
                self.lang_combo.setCurrentIndex(i)
                break
        layout.addWidget(self.lang_combo)

        layout.addStretch()

        # 多模型配置按钮
        self.multi_model_btn = QPushButton(tr("settings.btn_multi_model"))
        self.multi_model_btn.setStyleSheet(
            "QPushButton { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); "
            "color: #fff; border: none; border-radius: 10px; padding: 10px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: linear-gradient(135deg, #7690f0 0%, #8658b2 100%); }"
        )
        self.multi_model_btn.clicked.connect(self._on_multi_model_config)
        layout.addWidget(self.multi_model_btn)

        # 保存按钮
        self.save_btn = QPushButton(tr("settings.btn_save"))
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 10px; padding: 10px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
        )
        self.save_btn.clicked.connect(self._on_save)
        layout.addWidget(self.save_btn)

        # 用户画像按钮
        self.profile_btn = QPushButton(tr("settings.btn_profile"))
        self.profile_btn.setStyleSheet(
            "QPushButton { background-color: #e8f0fe; color: #4a7bf7; border: none; "
            "border-radius: 10px; padding: 10px; font-size: 14px; }"
            "QPushButton:hover { background-color: #d8e4fd; }"
        )
        self.profile_btn.clicked.connect(self._on_view_profile)
        layout.addWidget(self.profile_btn)

        # 记忆管理按钮
        self.memory_btn = QPushButton(tr("settings.btn_memory"))
        self.memory_btn.setStyleSheet(
            "QPushButton { background-color: #f0e8fe; color: #7c3aed; border: none; "
            "border-radius: 10px; padding: 10px; font-size: 14px; }"
            "QPushButton:hover { background-color: #e4d8fd; }"
        )
        self.memory_btn.clicked.connect(self._on_manage_memory)
        layout.addWidget(self.memory_btn)

    def _make_separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e8e8ec;")
        line.setFixedHeight(1)
        return line

    def hide_panel(self):
        self._visible = False
        self.hide()

    def toggle_panel(self):
        self._visible = not self._visible
        self.setVisible(self._visible)
        return self._visible

    def _on_save(self):
        """保存设置到数据库"""
        from src.models.database import set_preference
        model_id = self.model_combo.currentData()
        temp = self.temp_spin.value() / 10.0
        ctx = self.ctx_spin.value()
        font_size = self.font_size_spin.value()
        stream = self.stream_check.isChecked()
        lang_code = self.lang_combo.currentData()

        set_preference("model", model_id)
        set_preference("temperature", temp)
        set_preference("context_rounds", ctx)
        set_preference("font_size", font_size)
        set_preference("stream_output", stream)
        set_preference("web_search_enabled", self.web_search_check.isChecked())
        set_preference("kb_search_enabled", self.kb_search_check.isChecked())
        set_preference("moral_warn", self.moral_warn.isChecked())
        set_preference("moral_guide", self.moral_guide.isChecked())
        set_preference("moral_lock", self.moral_lock.isChecked())
        if lang_code:
            set_preference("language", lang_code)
            set_language(lang_code)

        self.setting_changed.emit()
        self.hide_panel()

    def _on_view_profile(self):
        """查看用户画像"""
        from src.core.user_profile import get_user_profile
        profile = get_user_profile().get_profile()

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("profile.title"))
        dialog.setMinimumSize(500, 400)
        dialog.setStyleSheet(
            "QDialog { background-color: #ffffff; }"
            "QLabel { font-size: 14px; color: #1a1a2e; }"
            "QTextEdit { border: 1px solid #ddd; border-radius: 8px; padding: 10px; "
            "font-size: 13px; background: #fafafa; }"
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)

        title = QLabel(tr("profile.heading"))
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        hint = QLabel(tr("profile.hint"))
        hint.setStyleSheet("color: #888; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        display = QTextEdit()
        display.setReadOnly(True)
        display.setFont(QFont("Microsoft YaHei", 13))

        lines = []
        has_content = False
        for dim, label in get_user_profile().DIMENSIONS.items():
            values = profile.get(dim, [])
            if values:
                has_content = True
                lines.append(f"📌 {label}：")
                for v in values:
                    lines.append(f"   • {v}")
                lines.append("")

        if not has_content:
            lines.append(tr("profile.no_data"))
            lines.append("")
            lines.append(tr("profile.auto_learn_hint"))
            lines.append("  • " + tr("profile.example_interest"))
            lines.append("  • " + tr("profile.example_profession"))
            lines.append("  • " + tr("profile.example_identity"))

        display.setPlainText("\n".join(lines))
        layout.addWidget(display, 1)

        # 清除画像按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        clear_btn = QPushButton(tr("profile.btn_clear"))
        clear_btn.setStyleSheet(
            "QPushButton { background-color: #fff1f0; color: #cf1322; border: 1px solid #ffa39e; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #ffccc7; }"
        )
        clear_btn.clicked.connect(dialog.reject)
        close_btn = QPushButton(tr("btn.close"))
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec()

    def _on_manage_memory(self):
        """记忆管理面板"""
        from src.core.memory import get_memory_manager
        mm = get_memory_manager()

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("memory.title"))
        dialog.setMinimumSize(650, 500)
        dialog.setStyleSheet(
            "QDialog { background-color: #ffffff; }"
            "QLabel { font-size: 14px; color: #1a1a2e; }"
            "QListWidget { border: 1px solid #e8e8ec; border-radius: 8px; font-size: 13px; }"
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #f5f5f5; }"
            "QPushButton { color: #fff; border: none; border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel(tr("memory.heading"))
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        # 标签页
        tab_widget = QTabWidget()

        # Tab 1: 主人信息
        master_tab = QWidget()
        master_layout = QVBoxLayout(master_tab)
        master_list = QListWidget()

        master_info = mm.get_master_info()
        if master_info:
            for k, v in master_info.items():
                master_list.addItem(f"📌 {k}: {v}")
        else:
            master_list.addItem(tr("memory.no_master"))
        master_layout.addWidget(master_list)
        tab_widget.addTab(master_tab, tr("memory.tab_master"))

        # Tab 2: 长期记忆
        long_term_tab = QWidget()
        long_term_layout = QVBoxLayout(long_term_tab)
        long_term_list = QListWidget()

        long_term = mm.get_long_term_memories()
        if long_term:
            for k, v in long_term.items():
                val = v.get("value", v) if isinstance(v, dict) else v
                long_term_list.addItem(f"📌 {k}: {val}")
        else:
            long_term_list.addItem(tr("memory.no_long_term"))
        long_term_layout.addWidget(long_term_list)
        tab_widget.addTab(long_term_tab, tr("memory.tab_long_term"))

        # Tab 3: 快照列表
        snapshot_tab = QWidget()
        snapshot_layout = QVBoxLayout(snapshot_tab)
        snapshot_list = QListWidget()

        from pathlib import Path
        snapshots_dir = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "snapshots"))
        if snapshots_dir.exists():
            snapshots = sorted(snapshots_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if snapshots:
                for sp in snapshots[:50]:
                    snapshot_list.addItem(f"💾 {sp.name} ({sp.stat().st_size / 1024:.1f}KB)")
            else:
                snapshot_list.addItem(tr("memory.no_snapshot"))
        else:
            snapshot_list.addItem(tr("memory.no_snapshot_dir"))
        snapshot_layout.addWidget(snapshot_list)
        tab_widget.addTab(snapshot_tab, tr("memory.tab_snapshot"))

        layout.addWidget(tab_widget)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        # 删除选中记忆
        def delete_selected():
            current_tab = tab_widget.currentIndex()
            current_list = [master_list, long_term_list, snapshot_list][current_tab]
            for item in current_list.selectedItems():
                if current_tab == 0:  # 主人信息
                    text = item.text()
                    key = text.split(": ")[0].replace("📌 ", "") if ": " in text else ""
                    import sqlite3
                    conn = sqlite3.connect(mm._db_path)
                    conn.execute("DELETE FROM cognitive_locks WHERE key = ?", (f"master_{key}",))
                    conn.commit()
                    conn.close()
                    current_list.takeItem(current_list.row(item))
                elif current_tab == 1:  # 长期记忆
                    text = item.text()
                    key = text.split(": ")[0].replace("📌 ", "") if ": " in text else ""
                    import sqlite3
                    conn = sqlite3.connect(mm._db_path)
                    conn.execute("DELETE FROM cognitive_locks WHERE key = ?", (f"long_term_{key}",))
                    conn.commit()
                    conn.close()
                    current_list.takeItem(current_list.row(item))

        del_btn = QPushButton(tr("memory.btn_delete"))
        del_btn.setStyleSheet("background-color: #cf1322;")
        del_btn.clicked.connect(delete_selected)
        btn_row.addWidget(del_btn)

        close_btn = QPushButton(tr("btn.close"))
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec()

    def _on_multi_model_config(self):
        """打开多模型配置对话框"""
        dlg = MultiModelDialog(self)
        dlg.model_switched.connect(self.setting_changed.emit)
        dlg.exec()


class MultiModelDialog(QDialog):
    """多模型配置对话框"""
    model_switched = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("multi_model.title"))
        self.setMinimumSize(520, 580)
        self.resize(560, 640)
        self.setStyleSheet(
            "QDialog { background-color: #f5f6fa; }"
            "QLabel { font-size: 13px; color: #2d3436; }"
            "QLineEdit, QComboBox { background: #fff; border: 1px solid #dcdde1; "
            "border-radius: 8px; padding: 8px 12px; font-size: 13px; color: #2d3436; }"
            "QLineEdit:focus, QComboBox:focus { border-color: #667eea; }"
            "QPushButton { border: none; border-radius: 8px; padding: 8px 16px; "
            "font-size: 13px; font-weight: bold; color: #fff; }"
            "QPushButton:disabled { background-color: #b2bec3; color: #fff; }"
            "QScrollArea { border: none; background: transparent; }"
        )

        self._mm = None
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            self._mm = get_multi_model_manager()
        except Exception as e:
            logger.error(f"[MultiModel] 加载失败: {e}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 标题
        title = QLabel(tr("multi_model.title"))
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #1a1a2e;")
        layout.addWidget(title)

        hint = QLabel(tr("multi_model.hint"))
        hint.setStyleSheet("color: #636e72; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 当前模型指示
        self._current_label = QLabel()
        self._current_label.setStyleSheet(
            "background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); "
            "color: #fff; padding: 10px 14px; border-radius: 10px; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(self._current_label)

        # 模型卡片滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setSpacing(10)
        self._cards_layout.setContentsMargins(0, 0, 4, 0)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_container)
        layout.addWidget(scroll, 1)

        # 构建模型卡片
        self._cards = {}
        self._build_cards()

        # 关闭按钮
        close_btn = QPushButton(tr("btn.close"))
        close_btn.setStyleSheet(
            "QPushButton { background-color: #dfe6e9; color: #2d3436; }"
            "QPushButton:hover { background-color: #b2bec3; }"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _build_cards(self):
        """为每个模型提供商构建配置卡片"""
        if not self._mm:
            return

        from src.core.multi_model_manager import PROVIDERS
        # 移除 stretch
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_models = self._mm.get_all_models()
        current_id = self._mm.current_model

        # 更新当前模型标签
        current_name = "Ollama (本地)"
        for m in all_models:
            if m['is_current']:
                current_name = m['name']
                break
        self._current_label.setText(f"📌 {tr('multi_model.current')}: {current_name}")

        for model_info in all_models:
            pid = model_info['id']
            card = self._create_card(model_info)
            self._cards[pid] = card
            self._cards_layout.addWidget(card)

        self._cards_layout.addStretch()

    def _create_card(self, info):
        """创建单个模型配置卡片"""
        pid = info['id']
        name = info['name']
        enabled = info['enabled']
        is_current = info['is_current']
        needs_key = info['needs_key']
        is_free = info['free']
        models_list = info['models']

        # 卡片容器
        card = QWidget()
        card.setStyleSheet(
            "QWidget { background-color: #ffffff; border: 1px solid #dcdde1; "
            "border-radius: 12px; }"
        )
        if is_current:
            card.setStyleSheet(
                "QWidget { background-color: #ffffff; border: 2px solid #667eea; "
                "border-radius: 12px; }"
            )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # 第一行：名称 + 标签
        row1 = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #2d3436;")
        row1.addWidget(name_label)

        if is_current:
            cur_tag = QLabel(f"📌 {tr('multi_model.current')}")
            cur_tag.setStyleSheet(
                "background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); "
                "color: #fff; padding: 3px 10px; border-radius: 10px; font-size: 11px; font-weight: bold;"
            )
            row1.addWidget(cur_tag)

        tag_text = tr("multi_model.free") if is_free else tr("multi_model.paid")
        tag_color = "#00b894" if is_free else "#fdcb6e"
        tag = QLabel(tag_text)
        tag.setStyleSheet(
            f"background: {tag_color}22; color: {tag_color}; padding: 3px 10px; "
            "border-radius: 10px; font-size: 11px; font-weight: bold;"
        )
        row1.addWidget(tag)
        row1.addStretch()
        card_layout.addLayout(row1)

        # API Key（需要密钥时显示）
        if needs_key:
            key_row = QHBoxLayout()
            key_label = QLabel(tr("multi_model.api_key"))
            key_label.setFixedWidth(70)
            key_label.setStyleSheet("color: #636e72; font-size: 12px;")
            key_row.addWidget(key_label)

            key_input = QLineEdit()
            key_input.setPlaceholderText(tr("multi_model.api_key_placeholder"))
            key_input.setEchoMode(QLineEdit.EchoMode.Password)
            # 如果已启用，显示已配置的key（部分遮蔽）
            if enabled and self._mm:
                saved_key = self._mm.config.get('models', {}).get(pid, {}).get('api_key', '')
                if saved_key:
                    key_input.setText(saved_key[:6] + "****" + saved_key[-4:] if len(saved_key) > 10 else "****")
            key_row.addWidget(key_input)
            card_layout.addLayout(key_row)

        # 模型名称选择
        if models_list:
            model_row = QHBoxLayout()
            model_label = QLabel(tr("multi_model.model_name"))
            model_label.setFixedWidth(70)
            model_label.setStyleSheet("color: #636e72; font-size: 12px;")
            model_row.addWidget(model_label)

            model_combo = QComboBox()
            for m_name in models_list:
                model_combo.addItem(m_name)
            # 选中已保存的模型名
            if enabled and self._mm:
                saved_name = self._mm.config.get('models', {}).get(pid, {}).get('model_name', '')
                if saved_name:
                    idx = model_combo.findText(saved_name)
                    if idx >= 0:
                        model_combo.setCurrentIndex(idx)
            model_row.addWidget(model_combo)
            card_layout.addLayout(model_row)

        # API 地址（可选，高级配置）
        url_row = QHBoxLayout()
        url_label = QLabel(tr("multi_model.base_url"))
        url_label.setFixedWidth(70)
        url_label.setStyleSheet("color: #636e72; font-size: 12px;")
        url_row.addWidget(url_label)

        url_input = QLineEdit()
        from src.core.multi_model_manager import PROVIDERS
        default_url = PROVIDERS.get(pid, {}).get('base_url', '')
        url_input.setPlaceholderText(default_url)
        if enabled and self._mm:
            saved_url = self._mm.config.get('models', {}).get(pid, {}).get('base_url', '')
            if saved_url and saved_url != default_url:
                url_input.setText(saved_url)
        url_row.addWidget(url_input)
        card_layout.addLayout(url_row)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if enabled:
            test_btn = QPushButton(tr("multi_model.btn_test"))
            test_btn.setStyleSheet(
                "QPushButton { background-color: #0984e3; }"
                "QPushButton:hover { background-color: #74b9ff; }"
            )
            test_btn.clicked.connect(lambda checked, p=pid, t=test_btn: self._test_connection(p, t))
            btn_row.addWidget(test_btn)

            if not is_current:
                switch_btn = QPushButton(tr("multi_model.btn_switch"))
                switch_btn.setStyleSheet(
                    "QPushButton { background-color: #00b894; }"
                    "QPushButton:hover { background-color: #55efc4; color: #2d3436; }"
                )
                switch_btn.clicked.connect(lambda checked, p=pid, n=name: self._switch_model(p, n))
                btn_row.addWidget(switch_btn)

            disable_btn = QPushButton(tr("multi_model.btn_disable"))
            disable_btn.setStyleSheet(
                "QPushButton { background-color: #d63031; }"
                "QPushButton:hover { background-color: #ff7675; }"
            )
            disable_btn.clicked.connect(lambda checked, p=pid: self._disable_model(p))
            btn_row.addWidget(disable_btn)
        else:
            enable_btn = QPushButton(tr("multi_model.btn_enable"))
            enable_btn.setStyleSheet(
                "QPushButton { background-color: #00b894; }"
                "QPushButton:hover { background-color: #55efc4; color: #2d3436; }"
            )
            enable_btn.clicked.connect(
                lambda checked, p=pid, k=key_input if needs_key else None,
                m=model_combo if models_list else None, u=url_input: self._enable_model(p, k, m, u)
            )
            btn_row.addWidget(enable_btn)

        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        return card

    def _enable_model(self, provider_id, key_input, model_combo, url_input):
        """启用模型提供商"""
        if not self._mm:
            return

        api_key = ""
        if key_input:
            api_key = key_input.text().strip()
            # 如果显示的是遮蔽的key，取保存的
            if "****" in api_key:
                api_key = self._mm.config.get('models', {}).get(provider_id, {}).get('api_key', '')

        model_name = ""
        if model_combo:
            model_name = model_combo.currentText()

        base_url = ""
        if url_input and url_input.text().strip():
            base_url = url_input.text().strip()

        result = self._mm.enable_model(provider_id, api_key=api_key, base_url=base_url, model_name=model_name)
        if result['success']:
            # 自动切换到新启用的模型
            self._mm.set_current_model(provider_id)
            self._rebuild()
            self.model_switched.emit()
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Error", result.get('error', tr("multi_model.save_fail", error="unknown")))

    def _disable_model(self, provider_id):
        """禁用模型提供商"""
        if not self._mm:
            return
        if provider_id == self._mm.current_model:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Info", tr("multi_model.switch_fail", error="Cannot disable current model"))
            return
        self._mm.disable_model(provider_id)
        self._rebuild()

    def _test_connection(self, provider_id, test_btn):
        """测试模型连接"""
        if not self._mm:
            return
        test_btn.setEnabled(False)
        old_text = test_btn.text()
        test_btn.setText(tr("multi_model.test_running"))

        def do_test():
            try:
                result = self._mm.test_connection(provider_id)
                if result['success']:
                    new_text = tr("multi_model.test_ok")
                    new_style = "QPushButton { background-color: #00b894; color: #fff; }"
                else:
                    err = result.get('error', 'unknown')
                    new_text = tr("multi_model.test_fail", error=err[:30])
                    new_style = "QPushButton { background-color: #d63031; color: #fff; }"
            except Exception as e:
                new_text = tr("multi_model.test_fail", error=str(e)[:30])
                new_style = "QPushButton { background-color: #d63031; color: #fff; }"

            # 在主线程更新UI
            from PySide6.QtCore import QMetaObject, Qt, Q_ARG
            QMetaObject.invokeMethod(self, "_update_test_btn",
                                     Qt.ConnectionType.QueuedConnection,
                                     Q_ARG(object, test_btn), Q_ARG(str, new_text),
                                     Q_ARG(str, new_style), Q_ARG(str, old_text))

        import threading
        threading.Thread(target=do_test, daemon=True).start()

    @Slot(object, str, str, str)
    def _update_test_btn(self, btn, text, style, old_text):
        """更新测试按钮状态（主线程）"""
        btn.setText(text)
        btn.setStyleSheet(style)
        btn.setEnabled(True)
        # 3秒后恢复原文字
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: (
            btn.setText(old_text),
            btn.setStyleSheet(
                "QPushButton { background-color: #0984e3; color: #fff; }"
                "QPushButton:hover { background-color: #74b9ff; }"
            )
        ))

    def _switch_model(self, provider_id, name):
        """切换当前使用的模型"""
        if not self._mm:
            return
        result = self._mm.set_current_model(provider_id)
        if result['success']:
            self._rebuild()
            self.model_switched.emit()
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Error", tr("multi_model.switch_fail", error=result.get('error', '')))

    def _rebuild(self):
        """重建卡片列表"""
        self._build_cards()
    """聊天区域：消息显示 + 输入栏"""
    send_message = Signal(str)          # 文本消息
    image_selected = Signal(list)       # 图片路径列表
    file_selected = Signal(list)        # 文件路径列表
    video_selected = Signal(str, str)   # (text, video_path)
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending_images = []
        self._pending_files = []
        self._pending_video = None  # 视频文件路径
        self._is_generating = False
        self._ai_msg_started = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部标题栏
        header = QFrame()
        header.setObjectName("chatHeader")
        header.setFixedHeight(50)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        self.chat_title = QLabel(tr("chat.title_new"))
        self.chat_title.setObjectName("chatTitle")
        self.chat_title.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        header_layout.addWidget(self.chat_title)
        header_layout.addStretch()

        self.thinking_label = QLabel("")
        self.thinking_label.setObjectName("thinkingLabel")
        header_layout.addWidget(self.thinking_label)

        layout.addWidget(header)

        # 聊天消息区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("chatArea")

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setFont(QFont("Microsoft YaHei", 14))
        scroll.setWidget(self.chat_display)

        layout.addWidget(scroll, 1)

        # 善意提醒区域
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("warningLabel")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        # 待发送附件预览区域
        self.attach_preview = QWidget()
        self.attach_preview.setStyleSheet("background-color: #fafafa; border-top: 1px solid #e0e0e0;")
        self.attach_layout = QHBoxLayout(self.attach_preview)
        self.attach_layout.setContentsMargins(16, 4, 16, 4)
        self.attach_layout.setSpacing(8)
        self.attach_preview.hide()
        layout.addWidget(self.attach_preview)

        # 输入栏
        input_bar = QFrame()
        input_bar.setObjectName("inputBar")
        input_layout = QHBoxLayout(input_bar)
        input_layout.setContentsMargins(16, 8, 16, 16)

        # 附件按钮
        self.attach_btn = QPushButton(tr("chat.attach_btn"))
        self.attach_btn.setObjectName("attachBtn")
        self.attach_btn.clicked.connect(self._on_attach_click)
        input_layout.addWidget(self.attach_btn)

        # 语音按钮
        self.voice_btn = QPushButton("🎤")
        self.voice_btn.setObjectName("voiceBtn")
        self.voice_btn.setToolTip(tr("chat.voice_tooltip"))
        self.voice_btn.setStyleSheet(
            "QPushButton { background-color: #f0f0f5; color: #555; border: 1px solid #ddd; "
            "border-radius: 10px; font-size: 16px; padding: 0 12px; height: 40px; min-width: 44px; }"
            "QPushButton:hover { background-color: #e4e4ee; border-color: #bbb; }"
            "QPushButton:pressed { background-color: #ff6b6b; color: #fff; }"
        )
        self.voice_btn.pressed.connect(self._on_voice_press)
        self.voice_btn.released.connect(self._on_voice_release)
        input_layout.addWidget(self.voice_btn)

        # 输入框
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(tr("chat.input_placeholder"))
        self.input_field.returnPressed.connect(self._on_send)
        input_layout.addWidget(self.input_field, 1)

        # 发送按钮（生成时变为停止按钮）
        self.send_btn = QPushButton(tr("btn.send"))
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.clicked.connect(self._on_send)
        self.stop_btn = QPushButton(tr("btn.stop"))
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setStyleSheet(
            "QPushButton { background-color: #ff6b6b; color: #ffffff; border: none; "
            "border-radius: 20px; padding: 10px 18px; font-size: 15px; font-weight: bold; "
            "min-width: 72px; height: 40px; }"
            "QPushButton:hover { background-color: #ff5252; }"
        )
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.hide()
        input_layout.addWidget(self.send_btn)
        input_layout.addWidget(self.stop_btn)

        layout.addWidget(input_bar)

        # 启用拖拽
        self.setAcceptDrops(True)
        # 粘贴事件过滤器安装在输入框上，不影响chat_display
        self.input_field.installEventFilter(self)

    def _on_send(self):
        text = self.input_field.text().strip()
        has_images = len(self._pending_images) > 0
        has_files = len(self._pending_files) > 0
        has_video = self._pending_video is not None
        has_content = text or has_images or has_files or has_video

        if has_content:
            self.input_field.clear()
            if has_video:
                # 视频也走统一消息队列
                self.send_message.emit(text if text else tr("chat.video_placeholder"))
                self._clear_pending()
            elif has_images:
                self.image_selected.emit(self._pending_images[:])
                if not text:
                    text = tr("chat.image_placeholder")
                self.send_message.emit(text)
                self._clear_pending()
            elif has_files:
                self.file_selected.emit(self._pending_files[:])
                if not text:
                    text = tr("chat.file_placeholder")
                self.send_message.emit(text)
                self._clear_pending()
            else:
                self.send_message.emit(text)

    def _clear_pending(self):
        self._pending_images.clear()
        self._pending_files.clear()
        self._pending_video = None
        while self.attach_layout.count():
            item = self.attach_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.attach_preview.hide()

    def add_pending_files(self, file_paths):
        for fp in file_paths:
            if is_video_file(fp):
                self._pending_video = fp
            elif is_image_file(fp):
                self._pending_images.append(fp)
            else:
                self._pending_files.append(fp)
        self._update_attach_preview()

    def _update_attach_preview(self):
        while self.attach_layout.count():
            item = self.attach_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_files = self._pending_images + self._pending_files
        if self._pending_video:
            all_files.append(self._pending_video)
        if not all_files:
            self.attach_preview.hide()
            return

        self.attach_preview.show()
        from pathlib import Path
        for fp in all_files:
            name = Path(fp).name
            if is_video_file(fp):
                icon = "🎬"
            elif is_image_file(fp):
                icon = "🖼️"
            else:
                icon = "📄"
            label = QLabel(f"{icon} {name}")
            label.setStyleSheet("font-size: 12px; color: #666; background: #eee; padding: 4px 8px; border-radius: 4px;")
            self.attach_layout.addWidget(label)
        self.attach_layout.addStretch()

    def _on_attach_click(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, tr("file.select"), "",
            tr("file.filter_all") + ";;"
            + tr("file.filter_images") + ";;"
            + tr("file.filter_videos") + ";;"
            + tr("file.filter_all_files")
        )
        if files:
            self.add_pending_files(files)

    # ── 拖拽支持 ──

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            files = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    files.append(url.toLocalFile())
            if files:
                self.add_pending_files(files)
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── 粘贴支持 ──

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            from PySide6.QtGui import QKeyEvent
            ke = event
            # Ctrl+V 粘贴
            if ke.key() == 0x56 and (ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._handle_clipboard_paste()
                return True
        return super().eventFilter(obj, event)

    def _handle_clipboard_paste(self):
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        # 如果剪贴板有图片 → 添加到附件
        if mime.hasImage():
            img = clipboard.image()
            if img and not img.isNull():
                import tempfile
                from datetime import datetime
                tmp_dir = tempfile.gettempdir()
                filename = f"lingzhi_paste_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                save_path = os.path.join(tmp_dir, filename)
                img.save(save_path, "PNG")
                self.add_pending_files([save_path])
                return

        # 如果剪贴板有文件URL → 添加到附件
        if mime.hasUrls():
            files = []
            for url in mime.urls():
                if url.isLocalFile():
                    files.append(url.toLocalFile())
            if files:
                self.add_pending_files(files)
                return

        # 纯文本 → 直接粘贴到输入框（使用系统默认粘贴行为）
        if mime.hasText():
            text = mime.text()
            if text:
                cursor = self.input_field.cursorPosition()
                self.input_field.insert(text)

    # ── 语音输入 ──

    def _on_voice_press(self):
        """按下语音按钮开始录音"""
        self._voice_recording = True
        self.voice_btn.setText("⏹️")
        self.voice_btn.setStyleSheet(
            "QPushButton { background-color: #ff6b6b; color: #fff; border: none; "
            "border-radius: 10px; font-size: 16px; padding: 0 12px; height: 40px; min-width: 44px; }"
        )
        try:
            from src.core.voice_input import get_voice_input
            vi = get_voice_input()
            if not vi.is_available:
                self.thinking_label.setText(tr("chat.voice_need_install"))
                return
            vi.start_recording()
            self.thinking_label.setText(tr("chat.voice_recording"))
        except Exception as e:
            self.thinking_label.setText(tr("chat.voice_record_fail", error=e))

    def _on_voice_release(self):
        """松开语音按钮结束录音并转文字"""
        if not hasattr(self, '_voice_recording'):
            return
        self._voice_recording = False
        self.voice_btn.setText("🎤")
        self.voice_btn.setStyleSheet(
            "QPushButton { background-color: #f0f0f5; color: #555; border: 1px solid #ddd; "
            "border-radius: 10px; font-size: 16px; padding: 0 12px; height: 40px; min-width: 44px; }"
            "QPushButton:hover { background-color: #e4e4ee; border-color: #bbb; }"
            "QPushButton:pressed { background-color: #ff6b6b; color: #fff; }"
        )
        self.thinking_label.setText(tr("chat.voice_recognizing"))

        try:
            from src.core.voice_input import get_voice_input
            vi = get_voice_input()
            audio_path = vi.stop_recording()
            if audio_path:
                # 在后台线程中转文字
                self._voice_worker = VoiceTranscribeWorker(audio_path)
                self._voice_worker.text_ready = self._on_voice_text
                self._voice_worker.start()
            else:
                self.thinking_label.setText("")
        except Exception as e:
            self.thinking_label.setText("")

    def _on_voice_text(self, text):
        """语音转文字完成"""
        self.thinking_label.setText("")
        if text:
            self.input_field.setText(text)
            self.input_field.setFocus()

    # ── 消息显示方法 ──

    def set_generating(self, generating):
        self._is_generating = generating
        # 不再禁用输入框，允许随时输入新消息
        if generating:
            self.stop_btn.show()
            self.send_btn.setText(tr("btn.send_queue"))  # 提示：发送将进入排队
        else:
            self.send_btn.setText(tr("btn.send"))
            self.stop_btn.hide()
            self.thinking_label.setText("")

    def _on_stop(self):
        """停止生成"""
        self._is_generating = False
        self.set_generating(False)
        self.stop_requested.emit()

    def show_warning(self, text):
        self.warning_label.setText(tr("chat.warning_prefix", text=text))
        self.warning_label.show()

    def show_thinking(self, text):
        """显示AI当前正在做什么（三段式查询提示）"""
        self.thinking_label.setText(text)

    def show_source(self, text):
        """显示信息来源提示"""
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertHtml(
            f'<div style="color:#888;font-size:12px;padding:4px 24px;">{self._escape_html(text)}</div>'
        )
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def hide_warning(self):
        self.warning_label.hide()

    def append_user_message(self, text, image_paths=None, file_paths=None):
        self.hide_warning()
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)

        inner_html = ""

        if image_paths:
            for img_path in image_paths:
                from pathlib import Path
                abs_path = str(Path(img_path).absolute()).replace("\\", "/")
                inner_html += f'<img src="file:///{abs_path}" style="max-width:220px;max-height:160px;border-radius:12px;margin:4px 0;"><br>'

        if file_paths:
            from pathlib import Path
            for fp in file_paths:
                name = Path(fp).name
                inner_html += f'<span style="background:rgba(255,255,255,0.3);padding:5px 12px;border-radius:8px;font-size:13px;">📄 {self._escape_html(name)}</span><br>'

        if text and text not in ("[图片]", "[文件]"):
            inner_html += self._escape_html(text)

        div_style = "background-color:#e8f0fe;color:#1a1a2e;padding:10px 16px;border-radius:18px 18px 4px 18px;display:inline-block;font-size:15px;white-space:pre-wrap;max-width:80%;box-shadow:0 1px 2px rgba(0,0,0,0.04);"
        formatted = (
            f'<div style="text-align:right;margin:8px 20px 4px 20px;">'
            f'<div style="{div_style}">'
            f'{inner_html}'
            f'</div></div><br>'
        )
        cursor.insertHtml(formatted)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def append_ai_chunk(self, chunk):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        escaped = self._escape_html(chunk)
        cursor.insertHtml(escaped)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def append_ai_message(self, text):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        escaped = self._markdown_to_html(text)
        formatted = (
            f'<div style="text-align:left;margin:4px 20px;">'
            f'<div style="background-color:#ffffff;color:#1a1a2e;padding:10px 16px;border-radius:18px 18px 18px 4px;display:inline-block;font-size:15px;white-space:pre-wrap;max-width:90%;box-shadow:0 1px 3px rgba(0,0,0,0.06);">'
            f'{escaped}'
            f'</div></div><br>'
        )
        cursor.insertHtml(formatted)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def append_error(self, text):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertHtml(
            f'<div style="text-align:left;margin:4px 20px;">'
            f'<div style="background-color:#fff1f0;color:#cf1322;padding:10px 16px;border-radius:18px 18px 18px 4px;display:inline-block;font-size:14px;">'
            f'⚠️ {self._escape_html(text)}'
            f'</div></div><br>'
        )
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def start_ai_message(self):
        self.hide_warning()
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertHtml(
            '<div style="text-align:left;margin:4px 20px;">'
            '<div id="ai-stream" style="background-color:#ffffff;color:#1a1a2e;padding:10px 16px;border-radius:18px 18px 18px 4px;display:inline-block;font-size:15px;white-space:pre-wrap;max-width:90%;box-shadow:0 1px 3px rgba(0,0,0,0.06);">'
        )
        self._ai_msg_started = True
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def end_ai_message(self):
        if hasattr(self, '_ai_msg_started') and self._ai_msg_started:
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertHtml('</div></div><br>')
            self.chat_display.setTextCursor(cursor)
            self.chat_display.ensureCursorVisible()
            self._ai_msg_started = False

    def _escape_html(self, text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

    def _markdown_to_html(self, text):
        """将 Markdown 文本转为 HTML（用于AI回复渲染）"""
        import re
        # 先处理代码块 ```...```
        code_blocks = []
        def extract_code(match):
            code_blocks.append(match.group(1))
            return f"__CODE_BLOCK_{len(code_blocks)-1}__"
        text = re.sub(r"```(\w*)\n(.*?)```", extract_code, text, flags=re.DOTALL)

        # 处理行内代码 `...`
        inline_codes = []
        def extract_inline(match):
            inline_codes.append(match.group(1))
            return f"__INLINE_CODE_{len(inline_codes)-1}__"
        text = re.sub(r"`([^`]+)`", extract_inline, text)

        # 转义 HTML（但保留我们后面要插入的标签）
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # 加粗 **...**
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        # 斜体 *...*
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        # 标题
        text = re.sub(r'^### (.+)$', r'<b style="font-size:14px;">\1</b>', text, flags=re.MULTILINE)
        text = re.sub(r'^## (.+)$', r'<b style="font-size:15px;">\1</b>', text, flags=re.MULTILINE)
        text = re.sub(r'^# (.+)$', r'<b style="font-size:16px;">\1</b>', text, flags=re.MULTILINE)
        # 无序列表
        text = re.sub(r'^[-*] (.+)$', r'<span style="padding-left:12px;">• \1</span>', text, flags=re.MULTILINE)
        # 有序列表
        text = re.sub(r'^(\d+)\. (.+)$', r'<span style="padding-left:12px;">\1. \2</span>', text, flags=re.MULTILINE)
        # 分隔线
        text = re.sub(r'^---$', '<hr style="border:none;border-top:1px solid #e0e0e0;margin:8px 0;">', text, flags=re.MULTILINE)

        # 还原代码块
        for i, code in enumerate(code_blocks):
            lang = ""
            # 代码块已经被处理了，直接渲染
            escaped_code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").rstrip()
            html_code = (
                f'<div style="background:#1e1e2e;color:#cdd6f4;border-radius:8px;padding:12px 16px;'
                f'margin:6px 0;font-family:Consolas,monospace;font-size:13px;overflow-x:auto;'
                f'white-space:pre-wrap;word-break:break-all;">'
                f'{escaped_code}'
                f'</div>'
            )
            text = text.replace(f"__CODE_BLOCK_{i}__", html_code)

        # 还原行内代码
        for i, code in enumerate(inline_codes):
            escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_code = (
                f'<span style="background:#f0f0f5;color:#d63384;padding:2px 6px;'
                f'border-radius:4px;font-family:Consolas,monospace;font-size:13px;">'
                f'{escaped}</span>'
            )
            text = text.replace(f"__INLINE_CODE_{i}__", html_code)

        # 换行
        text = text.replace("\n", "<br>")
        return text

    def clear_chat(self):
        self.chat_display.clear()
        self.hide_warning()

    def set_title(self, text):
        self.chat_title.setText(text)

    def _chat_display_scroll_to_bottom(self):
        sb = self.chat_display.verticalScrollBar()
        sb.setValue(sb.maximum())


class KnowledgeDialog(QDialog):
    """知识库管理对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("knowledge.title"))
        self.setMinimumSize(700, 500)
        self.setStyleSheet(
            "QDialog { background-color: #ffffff; }"
            "QLabel { font-size: 14px; color: #1a1a2e; }"
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
            "QPushButton:disabled { background-color: #c8d8f8; color: #e8e8f0; }"
            "QListWidget { border: 1px solid #e0e0e0; border-radius: 8px; font-size: 13px; }"
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #f0f0f0; }"
            "QListWidget::item:selected { background-color: #e8f0fe; color: #1a1a2e; }"
            "QTextEdit { border: 1px solid #ddd; border-radius: 8px; padding: 8px; "
            "font-size: 13px; background: #fafafa; }"
            "QProgressBar { border: 1px solid #ddd; border-radius: 4px; text-align: center; "
            "background: #f0f0f0; font-size: 12px; }"
            "QProgressBar::chunk { background-color: #4a7bf7; border-radius: 3px; }"
        )
        self._kb = None
        self._setup_ui()
        self._load_stats()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 标题
        header = QHBoxLayout()
        title = QLabel(tr("knowledge.heading"))
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 18px; "
            "color: #666; border-radius: 16px; }"
            "QPushButton:hover { background-color: #f0f0f0; color: #333; }"
        )
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # 统计信息
        self.stats_label = QLabel(tr("knowledge.loading"))
        self.stats_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.stats_label)

        # 导入区域
        import_frame = QFrame()
        import_frame.setStyleSheet("QFrame { border: 2px dashed #d0d0d8; border-radius: 10px; background: #fafafa; }")
        import_layout = QVBoxLayout(import_frame)
        import_layout.setContentsMargins(16, 12, 16, 12)
        imp_hint = QLabel(tr("knowledge.drag_hint"))
        imp_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        imp_hint.setStyleSheet("color: #888; font-size: 13px; border: none;")
        import_layout.addWidget(imp_hint)
        imp_btn_row = QHBoxLayout()
        self.import_btn = QPushButton(tr("knowledge.btn_import"))
        self.import_btn.setStyleSheet(
            "QPushButton { background-color: #f0f0f5; color: #333; border: 1px solid #ddd; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #e4e4ee; border-color: #bbb; }"
        )
        self.import_btn.clicked.connect(self._import_file)
        imp_btn_row.addWidget(self.import_btn)
        self.clear_btn = QPushButton(tr("knowledge.btn_clear"))
        self.clear_btn.setStyleSheet(
            "QPushButton { background-color: #fff1f0; color: #cf1322; border: 1px solid #ffa39e; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #ffccc7; }"
        )
        self.clear_btn.clicked.connect(self._clear_kb)
        imp_btn_row.addWidget(self.clear_btn)
        import_layout.addLayout(imp_btn_row)
        layout.addWidget(import_frame)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # 知识条目列表
        list_label = QLabel(tr("knowledge.stored_label"))
        list_label.setStyleSheet("font-weight: bold; color: #333; font-size: 13px;")
        layout.addWidget(list_label)

        self.doc_list = QListWidget()
        self.doc_list.setAlternatingRowColors(True)
        layout.addWidget(self.doc_list, 1)

        # 手动添加
        add_layout = QHBoxLayout()
        add_layout.setSpacing(8)
        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText(tr("knowledge.add_placeholder"))
        self.add_input.setStyleSheet(
            "QLineEdit { border: 1px solid #ddd; border-radius: 8px; padding: 8px 12px; "
            "font-size: 13px; background: #fafafa; }"
            "QLineEdit:focus { border-color: #4a7bf7; background: #fff; }"
        )
        add_layout.addWidget(self.add_input, 1)
        self.add_btn = QPushButton(tr("btn.add"))
        self.add_btn.clicked.connect(self._add_manual)
        add_layout.addWidget(self.add_btn)
        layout.addLayout(add_layout)

        # 手动添加支持回车
        self.add_input.returnPressed.connect(self._add_manual)

    def _get_kb(self):
        if self._kb is None:
            try:
                from src.core.knowledge_base import get_knowledge_base
                self._kb = get_knowledge_base()
            except Exception as e:
                QMessageBox.warning(self, tr("knowledge.error_title"), tr("knowledge.load_fail", error=e))
                return None
        return self._kb

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if files:
                kb = self._get_kb()
                if kb:
                    total = 0
                    for fp in files:
                        try:
                            total += kb.import_file(fp)
                        except Exception as e:
                            logger.debug(f"[main_window] 忽略: {e}")
                    if total > 0:
                        QMessageBox.information(self, tr("knowledge.import_done"), tr("knowledge.drag_import", count=total))
                        self._load_stats()
            event.acceptProposedAction()

    def _load_stats(self):
        kb = self._get_kb()
        if kb:
            stats = kb.get_stats()
            sources = ", ".join(stats["sources"][:5]) if stats["sources"] else tr("knowledge.stats_sources").split(": ")[1]
            self.stats_label.setText(
                tr("knowledge.stats", total=stats['total_documents']) + " | " +
                tr("knowledge.stats_sources", sources=sources) + " | " +
                tr("knowledge.stats_dim", dimension=stats['dimension'])
            )
            self._refresh_list()

    def _refresh_list(self):
        kb = self._get_kb()
        if not kb:
            return
        self.doc_list.clear()
        for doc in kb.documents[-100:]:  # 最近100条
            text = doc["text"][:80] + ("..." if len(doc["text"]) > 80 else "")
            source = doc.get("source", "")
            item = QListWidgetItem(f"[{source}] {text}")
            item.setData(Qt.ItemDataRole.UserRole, doc.get("id", ""))
            item.setToolTip(doc["text"])
            self.doc_list.addItem(item)
        self.doc_list.scrollToBottom()

    def _import_file(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, tr("knowledge.select_files"), "",
            tr("knowledge.file_filter") + ";;" + tr("file.filter_all_files")
        )
        if not files:
            return
        kb = self._get_kb()
        if not kb:
            return

        total = 0
        self.progress.setVisible(True)
        self.progress.setMaximum(len(files))
        self.progress.setValue(0)
        self.import_btn.setEnabled(False)

        for i, fp in enumerate(files):
            try:
                count = kb.import_file(fp)
                total += count
            except Exception as e:
                QMessageBox.warning(self, tr("knowledge.import_fail"), tr("knowledge.import_fail_msg", path=fp, error=e))
            self.progress.setValue(i + 1)

        self.progress.setVisible(False)
        self.import_btn.setEnabled(True)
        QMessageBox.information(self, tr("knowledge.import_done"), tr("knowledge.import_success_msg", count=total))
        self._load_stats()

    def _clear_kb(self):
        reply = QMessageBox.question(
            self, tr("knowledge.clear_title"), tr("knowledge.clear_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            kb = self._get_kb()
            if kb:
                kb.clear()
                self._kb = None
                self._load_stats()

    def _add_manual(self):
        text = self.add_input.text().strip()
        if not text:
            return
        kb = self._get_kb()
        if not kb:
            return
        kb.add_document(text, source=tr("knowledge.manual_source"))
        self.add_input.clear()
        self._load_stats()


class TaskDialog(QDialog):
    """任务管理对话框"""

    def __init__(self, conversation_id=None, parent=None):
        super().__init__(parent)
        self._conv_id = conversation_id
        self._tm = None
        self.setWindowTitle(tr("task.title"))
        self.setMinimumSize(650, 500)
        self.setStyleSheet(
            "QDialog { background-color: #ffffff; }"
            "QLabel { font-size: 14px; color: #1a1a2e; }"
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
            "QPushButton:disabled { background-color: #c8d8f8; color: #e8e8f0; }"
            "QListWidget { border: 1px solid #e0e0e0; border-radius: 8px; font-size: 13px; }"
            "QListWidget::item { padding: 10px; border-bottom: 1px solid #f0f0f0; }"
            "QListWidget::item:selected { background-color: #e8f0fe; color: #1a1a2e; }"
            "QComboBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 6px 12px; font-size: 13px; color: #333; }"
            "QComboBox:hover { border-color: #4a7bf7; }"
            "QComboBox::drop-down { border: none; width: 30px; }"
            "QComboBox QAbstractItemView { background: #fff; border: 1px solid #ddd; "
            "selection-background-color: #4a7bf7; }"
            "QLineEdit { border: 1px solid #ddd; border-radius: 8px; padding: 8px 12px; "
            "font-size: 13px; background: #fafafa; }"
            "QLineEdit:focus { border-color: #4a7bf7; background: #fff; }"
            "QTextEdit { border: 1px solid #ddd; border-radius: 8px; padding: 8px; "
            "font-size: 13px; background: #fafafa; }"
            "QSpinBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 6px 12px; font-size: 13px; color: #333; }"
        )
        self._setup_ui()
        self._load_tasks()

        # 自动刷新定时器（后台任务进度更新用）
        self._refresh_timer = None

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 标题栏
        header = QHBoxLayout()
        title = QLabel(tr("task.heading"))
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 18px; "
            "color: #666; border-radius: 16px; }"
            "QPushButton:hover { background-color: #f0f0f0; color: #333; }"
        )
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Tab 切换：原任务管理 + 后台任务
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #e0e0e0; border-radius: 8px; }"
            "QTabBar::tab { background: #f0f0f5; border: none; padding: 8px 20px; "
            "font-size: 13px; font-weight: bold; border-radius: 8px 8px 0 0; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #4a7bf7; color: #fff; }"
            "QTabBar::tab:hover:!selected { background: #e0e0f0; }"
        )

        # Tab 1: 原任务管理
        task_page = QWidget()
        self._setup_task_tab(task_page)
        self._tabs.addTab(task_page, tr("task.tab_plans"))

        # Tab 2: 后台任务
        bg_page = QWidget()
        self._setup_bg_task_tab(bg_page)
        self._tabs.addTab(bg_page, tr("task.tab_background"))

        layout.addWidget(self._tabs, 1)

    def _setup_task_tab(self, page):
        """原任务管理标签页"""
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # 统计 + 筛选
        top_row = QHBoxLayout()
        self.stats_label = QLabel(tr("task.loading"))
        self.stats_label.setStyleSheet("color: #666; font-size: 12px;")
        top_row.addWidget(self.stats_label)
        top_row.addStretch()

        # 状态筛选
        filter_label = QLabel(tr("task.filter_label"))
        filter_label.setStyleSheet("color: #555; font-size: 13px;")
        top_row.addWidget(filter_label)
        self.filter_combo = QComboBox()
        self.filter_combo.addItem(tr("task.filter_all"), "all")
        self.filter_combo.addItem(tr("task.filter_pending"), "pending")
        self.filter_combo.addItem(tr("task.filter_progress"), "in_progress")
        self.filter_combo.addItem(tr("task.filter_completed"), "completed")
        self.filter_combo.addItem(tr("task.filter_cancelled"), "cancelled")
        self.filter_combo.currentIndexChanged.connect(self._load_tasks)
        top_row.addWidget(self.filter_combo)
        layout.addLayout(top_row)

        # 任务列表
        self.task_list = QListWidget()
        self.task_list.setAlternatingRowColors(True)
        layout.addWidget(self.task_list, 1)

        # 底部操作区
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        # 新建任务按钮
        self.add_btn = QPushButton(tr("task.btn_new"))
        self.add_btn.setStyleSheet(
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
        )
        self.add_btn.clicked.connect(self._add_task)
        bottom.addWidget(self.add_btn)

        bottom.addStretch()

        # 操作按钮
        self.complete_btn = QPushButton(tr("task.btn_complete"))
        self.complete_btn.setStyleSheet(
            "QPushButton { background-color: #52c41a; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 14px; font-size: 13px; }"
            "QPushButton:hover { background-color: #73d13d; }"
        )
        self.complete_btn.clicked.connect(self._complete_task)
        bottom.addWidget(self.complete_btn)

        self.progress_btn = QPushButton(tr("task.btn_progress"))
        self.progress_btn.setStyleSheet(
            "QPushButton { background-color: #faad14; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 14px; font-size: 13px; }"
            "QPushButton:hover { background-color: #ffc53d; }"
        )
        self.progress_btn.clicked.connect(self._update_progress)
        bottom.addWidget(self.progress_btn)

        self.delete_btn = QPushButton(tr("task.btn_delete"))
        self.delete_btn.setStyleSheet(
            "QPushButton { background-color: #ff4d4f; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 14px; font-size: 13px; }"
            "QPushButton:hover { background-color: #ff7875; }"
        )
        self.delete_btn.clicked.connect(self._delete_task)
        bottom.addWidget(self.delete_btn)

        layout.addLayout(bottom)

    def _setup_bg_task_tab(self, page):
        """后台任务标签页"""
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        # 顶部统计 + 操作
        bg_top = QHBoxLayout()
        self._bg_stats_label = QLabel(tr("bg_task.loading"))
        self._bg_stats_label.setStyleSheet("color: #666; font-size: 12px;")
        bg_top.addWidget(self._bg_stats_label)
        bg_top.addStretch()

        # 取消任务按钮
        self._bg_cancel_btn = QPushButton(tr("bg_task.btn_cancel"))
        self._bg_cancel_btn.setStyleSheet(
            "QPushButton { background-color: #ff4d4f; color: #fff; border: none; "
            "border-radius: 8px; padding: 6px 14px; font-size: 12px; }"
            "QPushButton:hover { background-color: #ff7875; }"
        )
        self._bg_cancel_btn.clicked.connect(self._cancel_bg_task)
        bg_top.addWidget(self._bg_cancel_btn)

        # 清理已完成
        self._bg_clear_btn = QPushButton(tr("bg_task.btn_clear"))
        self._bg_clear_btn.setStyleSheet(
            "QPushButton { background-color: #e0e0e0; color: #555; border: none; "
            "border-radius: 8px; padding: 6px 14px; font-size: 12px; }"
            "QPushButton:hover { background-color: #d0d0d0; }"
        )
        self._bg_clear_btn.clicked.connect(self._clear_bg_tasks)
        bg_top.addWidget(self._bg_clear_btn)

        layout.addLayout(bg_top)

        # 后台任务列表
        self._bg_task_list = QListWidget()
        self._bg_task_list.setAlternatingRowColors(True)
        layout.addWidget(self._bg_task_list, 1)

        # 初始化后台任务管理器
        self._bg_tm = None
        self._bg_task_widgets = {}  # task_id -> {"progress_bar": QWidget, ...}
        self._init_bg_task_manager()

    def _init_bg_task_manager(self):
        """初始化后台任务管理器"""
        try:
            from src.core.background_task_manager import get_background_task_manager
            self._bg_tm = get_background_task_manager()
            self._bg_tm.register_change_callback(self._on_bg_task_changed)
            self._load_bg_tasks()
        except Exception as e:
            logger.error(f"[TaskDialog] 后台任务管理器初始化失败: {e}")

    def _on_bg_task_changed(self):
        """后台任务变化回调"""
        # 通过 QTimer 在主线程安全刷新
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._load_bg_tasks)

    def _load_bg_tasks(self):
        """加载后台任务列表"""
        if not self._bg_tm:
            return

        tasks = self._bg_tm.get_all_tasks(limit=50)
        running = sum(1 for t in tasks if t["status"] == "running")

        # 更新统计
        total = len(tasks)
        completed = sum(1 for t in tasks if t["status"] == "completed")
        failed = sum(1 for t in tasks if t["status"] == "failed")
        self._bg_stats_label.setText(
            tr("bg_task.stats", total=total, running=running, completed=completed, failed=failed)
        )

        # 如果有运行中的任务，启动自动刷新
        if running > 0 and self._refresh_timer is None:
            from PySide6.QtCore import QTimer
            self._refresh_timer = QTimer(self)
            self._refresh_timer.timeout.connect(self._load_bg_tasks)
            self._refresh_timer.start(2000)  # 2秒刷新
        elif running == 0 and self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

        # 记录当前选中项
        selected_id = None
        sel_item = self._bg_task_list.currentItem()
        if sel_item:
            selected_id = sel_item.data(Qt.ItemDataRole.UserRole)

        # 刷新列表
        self._bg_task_list.clear()
        self._bg_task_widgets.clear()

        status_icons = {
            "pending": "⏳", "running": "🔄", "completed": "✅",
            "failed": "❌", "cancelled": "🚫"
        }
        type_icons = {
            "search": "🔍", "download": "📥", "install": "📦",
            "process": "⚙️", "custom": "📋"
        }

        for t in tasks:
            status_icon = status_icons.get(t["status"], "❓")
            type_icon = type_icons.get(t["task_type"], "📋")

            # 标题行
            progress = t.get("progress", 0)
            title = t.get("title", "未命名任务")
            current_step = t.get("current_step", "")

            text = f"{type_icon}{status_icon} {title}"
            if t["status"] == "running":
                text += f"  [{progress}%]"
            elif t["status"] == "failed":
                text += f"  - {t.get('error_message', '')[:30]}"

            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            item.setSizeHint(QSize(0, 52))  # 增加行高以显示步骤

            # 颜色
            if t["status"] == "completed":
                item.setForeground(QColor("#999999"))
            elif t["status"] == "failed":
                item.setForeground(QColor("#ff4d4f"))
            elif t["status"] == "running":
                item.setForeground(QColor("#4a7bf7"))

            # 恢复选中
            if selected_id and t["id"] == selected_id:
                self._bg_task_list.setCurrentItem(item)

            self._bg_task_list.addItem(item)

            # 存储额外数据
            self._bg_task_widgets[t["id"]] = {
                "steps": t.get("steps", []),
                "steps_done": t.get("steps_done", 0),
                "steps_total": t.get("steps_total", 0),
                "current_step": current_step,
            }

            # 如果有步骤，添加子项显示步骤详情
            steps = t.get("steps", [])
            if steps and t["status"] in ("running", "completed", "failed"):
                step_icons = {
                    "pending": "○", "running": "●", "success": "✓",
                    "failed": "✗", "skipped": "⊹"
                }
                for i, s in enumerate(steps[:5]):  # 最多显示5个步骤
                    si = step_icons.get(s.get("status", "pending"), "○")
                    detail = s.get("detail", "")
                    step_text = f"    {si} {s.get('name', '')}"
                    if detail and len(detail) > 40:
                        detail = detail[:40] + "..."
                    if detail:
                        step_text += f" — {detail}"

                    sub_item = QListWidgetItem(step_text)
                    sub_item.setData(Qt.ItemDataRole.UserRole, t["id"])
                    sub_item.setFlags(sub_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    if s.get("status") == "success":
                        sub_item.setForeground(QColor("#999999"))
                    elif s.get("status") == "running":
                        sub_item.setForeground(QColor("#4a7bf7"))
                    elif s.get("status") == "failed":
                        sub_item.setForeground(QColor("#ff4d4f"))
                    self._bg_task_list.addItem(sub_item)
                if len(steps) > 5:
                    more = QListWidgetItem(f"    ... +{len(steps) - 5} " + tr("bg_task.more_steps"))
                    more.setFlags(more.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    more.setForeground(QColor("#999999"))
                    self._bg_task_list.addItem(more)

    def _get_selected_bg_task_id(self):
        item = self._bg_task_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _cancel_bg_task(self):
        task_id = self._get_selected_bg_task_id()
        if not task_id:
            QMessageBox.information(self, tr("bg_task.title"), tr("task.no_selection_msg"))
            return
        if not self._bg_tm:
            return
        reply = QMessageBox.question(
            self, tr("bg_task.cancel_title"), tr("bg_task.cancel_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._bg_tm.cancel_task(task_id)
            self._load_bg_tasks()

    def _clear_bg_tasks(self):
        """清理已完成的任务"""
        if not self._bg_tm:
            return
        self._bg_tm.clear_completed()
        self._load_bg_tasks()

    def showEvent(self, event):
        """对话框显示时刷新后台任务"""
        super().showEvent(event)
        self._load_bg_tasks()

    def hideEvent(self, event):
        """对话框隐藏时停止定时器"""
        super().hideEvent(event)
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None
        if self._bg_tm:
            self._bg_tm.unregister_change_callback(self._on_bg_task_changed)

    def _get_tm(self):
        if self._tm is None:
            try:
                from src.core.task_manager import get_task_manager
                self._tm = get_task_manager()
            except Exception as e:
                QMessageBox.warning(self, tr("task.error_title"), tr("task.load_fail", error=e))
                return None
        return self._tm

    def _load_tasks(self):
        tm = self._get_tm()
        if not tm:
            return

        filter_val = self.filter_combo.currentData()
        status = filter_val if filter_val != "all" else None

        tasks = tm.get_tasks(conversation_id=self._conv_id, status=status, limit=100)

        # 统计
        all_tasks = tm.get_tasks(conversation_id=self._conv_id, limit=999)
        pending = sum(1 for t in all_tasks if t["status"] == "pending")
        active = sum(1 for t in all_tasks if t["status"] == "in_progress")
        done = sum(1 for t in all_tasks if t["status"] == "completed")
        self.stats_label.setText(tr("task.stats", total=len(all_tasks), pending=pending, active=active, done=done))

        # 清空并填充列表
        self.task_list.clear()
        for t in tasks:
            # 状态图标
            status_map = {
                "pending": "⏳",
                "in_progress": "🔄",
                "completed": "✅",
                "cancelled": "❌",
            }
            icon = status_map.get(t["status"], "❓")

            # 优先级标识
            priority_map = {1: "", 2: "", 3: "🔴", 4: "🚨"}
            pri_icon = priority_map.get(t.get("priority", 2), "")

            # 进度条文本
            progress = t.get("progress", 0)
            progress_text = f" [{progress}%]" if t["status"] == "in_progress" else ""

            # 标题
            title = t.get("title", tr("task.untitled"))
            desc = t.get("description", "")
            if desc and len(desc) > 60:
                desc = desc[:60] + "..."

            # 标签
            tags = t.get("tags", [])
            tag_text = ""
            if tags:
                tag_text = " [" + ", ".join(str(tag) for tag in tags[:3]) + "]"

            # 截止日期
            due = t.get("due_date", "")
            due_text = tr("status.due_prefix", date=due) if due else ""

            display_text = f"{pri_icon}{icon} {title}{progress_text}{tag_text}{due_text}"
            if desc:
                display_text += f"\n    {desc}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, t["id"])

            # 已完成的灰显
            if t["status"] == "completed":
                item.setForeground(QColor("#999999"))
            elif t["status"] == "cancelled":
                item.setForeground(QColor("#bbbbbb"))

            self.task_list.addItem(item)

    def _add_task(self):
        tm = self._get_tm()
        if not tm:
            return

        # 新建任务对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("task.new_title"))
        dialog.setMinimumSize(450, 300)
        dialog.setStyleSheet(
            "QDialog { background-color: #ffffff; }"
            "QLabel { font-size: 13px; color: #333; }"
            "QLineEdit { border: 1px solid #ddd; border-radius: 8px; padding: 8px 12px; "
            "font-size: 13px; background: #fafafa; }"
            "QLineEdit:focus { border-color: #4a7bf7; background: #fff; }"
            "QTextEdit { border: 1px solid #ddd; border-radius: 8px; padding: 8px; "
            "font-size: 13px; background: #fafafa; }"
            "QComboBox { background: #f0f0f5; border: 1px solid #ddd; border-radius: 8px; "
            "padding: 6px 12px; font-size: 13px; color: #333; }"
            "QPushButton { background-color: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #5a8bf8; }"
            "QPushButton#cancel { background-color: #e0e0e0; color: #333; }"
            "QPushButton#cancel:hover { background-color: #d0d0d0; }"
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel(tr("task.task_title")))
        title_input = QLineEdit()
        title_input.setPlaceholderText(tr("task.title_placeholder"))
        layout.addWidget(title_input)

        layout.addWidget(QLabel(tr("task.task_desc")))
        desc_input = QTextEdit()
        desc_input.setMaximumHeight(80)
        desc_input.setPlaceholderText(tr("task.desc_placeholder"))
        layout.addWidget(desc_input)

        # 优先级
        pri_row = QHBoxLayout()
        pri_row.addWidget(QLabel(tr("task.priority")))
        pri_combo = QComboBox()
        pri_combo.addItem(tr("task.priority_normal"), 2)
        pri_combo.addItem(tr("task.priority_low"), 1)
        pri_combo.addItem(tr("task.priority_high"), 3)
        pri_combo.addItem(tr("task.priority_urgent"), 4)
        pri_row.addWidget(pri_combo, 1)
        layout.addLayout(pri_row)

        # 截止日期
        due_row = QHBoxLayout()
        due_row.addWidget(QLabel(tr("task.due_date")))
        due_input = QLineEdit()
        due_input.setPlaceholderText(tr("task.due_placeholder"))
        due_row.addWidget(due_input, 1)
        layout.addLayout(due_row)

        # 标签
        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel(tr("task.tags")))
        tag_input = QLineEdit()
        tag_input.setPlaceholderText(tr("task.tags_placeholder"))
        tag_row.addWidget(tag_input, 1)
        layout.addLayout(tag_row)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(tr("btn.cancel"))
        cancel_btn.setObjectName("cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton(tr("btn.create"))
        ok_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            title_text = title_input.text().strip()
            if not title_text:
                QMessageBox.warning(self, tr("task.no_title"), tr("task.no_title_msg"))
                return

            desc_text = desc_input.toPlainText().strip()
            priority = pri_combo.currentData()
            due_text = due_input.text().strip()
            tag_text = tag_input.text().strip()
            tags = [t.strip() for t in tag_text.split(",") if t.strip()] if tag_text else []

            conv_id = self._conv_id or "__default__"
            tm.create_task(
                conversation_id=conv_id,
                title=title_text,
                description=desc_text,
                priority=priority,
                tags=tags,
                due_date=due_text,
            )
            self._load_tasks()

    def _get_selected_task_id(self):
        item = self.task_list.currentItem()
        if not item:
            QMessageBox.information(self, tr("task.no_title"), tr("task.no_selection_msg"))
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _complete_task(self):
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        tm = self._get_tm()
        if tm:
            tm.complete_task(task_id)
            self._load_tasks()

    def _delete_task(self):
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        reply = QMessageBox.question(
            self, tr("task.delete_title"), tr("task.delete_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            tm = self._get_tm()
            if tm:
                tm.delete_task(task_id)
                self._load_tasks()

    def _update_progress(self):
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        tm = self._get_tm()
        if not tm:
            return

        # 获取当前进度
        tasks = tm.get_tasks()
        current_progress = 0
        for t in tasks:
            if t["id"] == task_id:
                current_progress = t.get("progress", 0)
                break

        value, ok = QInputDialog.getInt(
            self, tr("task.progress_title"), tr("task.progress_prompt"),
            value=current_progress, min=0, max=100
        )
        if ok:
            tm.update_task(task_id, progress=value)
            if value >= 100:
                tm.complete_task(task_id)
            self._load_tasks()


class MainWindow(QMainWindow):
    """Harven AI主窗口"""
    def __init__(self, config, hw_info, parent=None):
        super().__init__(parent)
        self.config = config
        self.hw_info = hw_info
        self.setWindowTitle(tr("app.name"))
        self.setMinimumSize(1000, 650)
        self.resize(1200, 800)
        self.setStyleSheet(STYLE_SHEET)

        self._current_conv_id = None
        self._current_spec_id = None
        self._engine = None
        self._worker = None
        self._video_worker = None
        self._msg_queue = []          # 消息队列
        self._is_processing = False  # 是否正在处理消息

        self._setup_ui()
        self._setup_model()
        self._load_conversations()
        self._load_specializations()
        self._load_user_preferences()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 左侧栏
        self.sidebar = SideBar()
        self.sidebar.new_chat_requested.connect(self._on_new_chat)
        self.sidebar.conversation_selected.connect(self._on_select_conversation)
        self.sidebar.settings_requested.connect(self._on_toggle_settings)
        self.sidebar.knowledge_requested.connect(self._on_knowledge)
        self.sidebar.task_requested.connect(self._on_tasks)
        main_layout.addWidget(self.sidebar)

        # 分隔线
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedWidth(1)
        main_layout.addWidget(sep)

        # 右侧设置抽屉（初始隐藏）
        self.settings_drawer = SettingsDrawer()
        self.settings_drawer.setting_changed.connect(self._on_settings_changed)
        self.settings_drawer.hide()
        main_layout.addWidget(self.settings_drawer)

        # 聊天区域
        self.chat_area = ChatArea()
        self.chat_area.send_message.connect(self._on_send_message)
        self.chat_area.image_selected.connect(self._on_image_selected)
        self.chat_area.file_selected.connect(self._on_file_selected)
        self.chat_area.video_selected.connect(self._on_video_selected)
        self.chat_area.stop_requested.connect(self._on_stop_generation)
        main_layout.addWidget(self.chat_area, 1)

        # 状态栏
        self.statusBar().setStyleSheet(
            "QStatusBar { background-color: #f5f5f5; border-top: 1px solid #e0e0e0; }\n"
            "QStatusBar QLabel { font-size: 12px; color: #6c7086; padding: 2px 8px; }"
        )

        model = self.config.get("ollama.default_model", "qwen2.5:14b")
        vram = self.hw_info.get("vram_gb", "?")
        version = tr("status.version_advanced") if self.hw_info.get("version") == "advanced" else tr("status.version_normal")
        self.statusBar().showMessage(
            f"🧠 {tr('status.model', model=model)}  |  🎮 {tr('status.vram', vram=vram)}  |  {version}  |  {tr('status.local_run')}"
        )

    def _setup_model(self):
        base_url = self.config.get("ollama.base_url", "http://localhost:11434")
        model = self.config.get("ollama.default_model", "qwen2.5:14b")
        self.model_manager = None
        self._base_url = base_url
        self._model_name = model

    def _try_init_model(self):
        if self.model_manager:
            return True
        from src.core.model_manager import ModelManager, check_ollama
        ok, msg = check_ollama(self._base_url)
        if not ok:
            return False
        self.model_manager = ModelManager(self._base_url, self._model_name)
        # 后台预热模型，消除首次对话冷启动延迟
        self.model_manager.warmup()
        self.statusBar().showMessage(
            f"🧠 {tr('status.model', model=self._model_name)}  |  🎮 {tr('status.vram', vram=self.hw_info.get('vram_gb', '?'))}  |  "
            f"{'📦 ' + tr('status.version_advanced') if self.hw_info.get('version') == 'advanced' else '📦 ' + tr('status.version_normal')}  |  {tr('status.connected')}"
        )
        return True

    def _load_conversations(self):
        from src.models.database import get_conversations
        convs = get_conversations()
        self.sidebar.load_conversations(convs)

    def _load_specializations(self):
        from src.models.database import get_specializations
        specs = get_specializations()
        self.sidebar.load_specializations(specs)

    def _load_user_preferences(self):
        """加载用户保存的偏好设置"""
        from src.models.database import get_preference
        model_pref = get_preference("model")
        if model_pref:
            for i in range(self.settings_drawer.model_combo.count()):
                if self.settings_drawer.model_combo.itemData(i) == model_pref:
                    self.settings_drawer.model_combo.setCurrentIndex(i)
                    break

        temp = get_preference("temperature")
        if temp:
            self.settings_drawer.temp_spin.setValue(int(float(temp) * 10))

        ctx = get_preference("context_rounds")
        if ctx:
            self.settings_drawer.ctx_spin.setValue(int(ctx))

        font_size = get_preference("font_size")
        if font_size:
            self.settings_drawer.font_size_spin.setValue(int(font_size))

        stream = get_preference("stream_output")
        if stream is not None:
            self.settings_drawer.stream_check.setChecked(stream == "True")

        web = get_preference("web_search_enabled")
        if web is not None:
            self.settings_drawer.web_search_check.setChecked(web == "True")

        kb = get_preference("kb_search_enabled")
        if kb is not None:
            self.settings_drawer.kb_search_check.setChecked(kb == "True")

        dark = get_preference("dark_theme")
        if dark == "True":
            self.settings_drawer.dark_theme_btn.setChecked(True)
            self.settings_drawer.light_theme_btn.setChecked(False)
            self._apply_theme(True)

    def _on_toggle_settings(self):
        self.settings_drawer.toggle_panel()

    def _on_settings_changed(self):
        """设置保存后应用"""
        model_id = self.settings_drawer.model_combo.currentData()
        if model_id and self.model_manager:
            self.model_manager.set_model(model_id)
            self._model_name = model_id

        # 检查多模型管理器是否有切换
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            if mm.current_model != "ollama":
                # 外部模型已启用，更新状态栏
                from src.models.database import get_specializations
                specs = get_specializations()
                model_label = mm.current_model
                for s in specs:
                    pass  # specs 数据库不存多模型信息
                self.statusBar().showMessage(
                    f"🤖 {tr('multi_model.switched', name=model_label)}"
                )
        except Exception as _e: logger.debug(f"[main_window] 忽略: {_e}")

        font_size = self.settings_drawer.font_size_spin.value()
        self.chat_area.chat_display.setFont(QFont("Microsoft YaHei", font_size))

        # 主题切换
        from src.models.database import set_preference, get_preference
        is_dark = self.settings_drawer.dark_theme_btn.isChecked()
        set_preference("dark_theme", str(is_dark))
        self._apply_theme(is_dark)

        self.statusBar().showMessage(tr("settings.saved"))

    def _apply_theme(self, is_dark):
        """应用深色/浅色主题"""
        if is_dark:
            self.setStyleSheet(DARK_STYLE_SHEET)
            self.settings_drawer.setStyleSheet("background-color: #1a1a2e; border-left: 1px solid #2d2d50;")
            self.settings_drawer.title_label.setStyleSheet("color: #e0e0f0;")
            self.settings_drawer.close_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; font-size: 18px; "
                "color: #6c6c90; border-radius: 16px; }"
                "QPushButton:hover { background-color: #252540; color: #e0e0f0; }"
            )
        else:
            self.setStyleSheet(STYLE_SHEET)
            self.settings_drawer.setStyleSheet("background-color: #ffffff; border-left: 1px solid #e8e8ec;")
            self.settings_drawer.title_label.setStyleSheet("color: #1a1a2e;")
            self.settings_drawer.close_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; font-size: 18px; "
                "color: #666; border-radius: 16px; }"
                "QPushButton:hover { background-color: #f0f0f0; color: #333; }"
            )

    def _on_knowledge(self):
        """打开知识库管理对话框"""
        dialog = KnowledgeDialog(self)
        dialog.exec()

    def _on_tasks(self):
        """打开任务管理对话框"""
        dialog = TaskDialog(conversation_id=self._current_conv_id, parent=self)
        dialog.exec()

    def _on_new_chat(self, specialization_id):
        # 处理刷新信号
        if specialization_id == "__refresh__":
            self._load_conversations()
            return

        from src.models.database import create_conversation
        conv_id = create_conversation(title=tr("conv.new_chat"), specialization_id=specialization_id or None)
        self._current_conv_id = conv_id
        self._current_spec_id = specialization_id or None
        self.chat_area.clear_chat()
        self.chat_area.set_title(tr("sidebar.new_chat"))
        self._engine = None
        self._load_conversations()

    def _on_select_conversation(self, conv_id):
        self._current_conv_id = conv_id
        self.chat_area.clear_chat()
        self.chat_area.set_title(tr("chat.title_chatting"))

        from src.models.database import get_messages, get_conversations, get_specializations
        msgs = get_messages(conv_id, limit=50)
        for m in msgs:
            if m["role"] == "user":
                self.chat_area.append_user_message(m["content"])
            elif m["role"] == "assistant":
                self.chat_area.append_ai_message(m["content"])

        # 获取对话关联的专项培养ID和提示词
        convs = get_conversations()
        spec_id = None
        spec_prompt = None
        for c in convs:
            if c["id"] == conv_id:
                # 从spec_name反查spec_id（需要查数据库）
                from src.models.database import get_connection, DEFAULT_DB_PATH
                import os
                db_path = os.path.normpath(DEFAULT_DB_PATH)
                conn = get_connection(db_path)
                row = conn.execute(
                    "SELECT specialization_id FROM conversations WHERE id = ?", (conv_id,)
                ).fetchone()
                conn.close()
                if row and row["specialization_id"]:
                    spec_id = row["specialization_id"]
                break
        self._current_spec_id = spec_id

        if spec_id:
            specs = get_specializations()
            for s in specs:
                if s["id"] == spec_id and s.get("system_prompt"):
                    spec_prompt = s["system_prompt"]
                    break

        if self._try_init_model():
            from src.core.engine import ConversationEngine
            self._engine = ConversationEngine(self.model_manager, conv_id, specialization_prompt=spec_prompt)

        self.chat_area.set_title(tr("chat.title_chatting"))
        self._chat_display_scroll_to_bottom()

    def _chat_display_scroll_to_bottom(self):
        sb = self.chat_area.chat_display.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── 信号槽 ──

    def _on_send_message(self, text):
        """收到用户消息：立即入队或直接处理"""
        if not self._current_conv_id:
            self._on_new_chat("")

        images = self.chat_area._pending_images[:]
        files = self.chat_area._pending_files[:]
        video = self.chat_area._pending_video
        self.chat_area._pending_images.clear()
        self.chat_area._pending_files.clear()
        self.chat_area._pending_video = None

        self.chat_area.append_user_message(text, image_paths=images if images else None, file_paths=files if files else None)

        item = {"text": text, "images": images, "files": files, "video": video}

        if self._is_processing:
            self._msg_queue.append(item)
            self._update_queue_ui()
            return

        self._process_message(item)

    def _process_message(self, item):
        """处理单条消息（设置 _is_processing=True）"""
        text = item["text"]
        images = item["images"]
        files = item["files"]
        video = item["video"]

        # 自动重命名对话（如果是默认标题"新对话"）
        if self._current_conv_id:
            from src.models.database import get_conversations
            convs = get_conversations()
            default_titles = (tr("conv.new_chat"), "新对话", "New Chat")
            for c in convs:
                if c["id"] == self._current_conv_id and c["title"] in default_titles:
                    clean_text = text.replace("\n", " ").strip()
                    if clean_text.startswith("[图片]") or clean_text.startswith("[文件]"):
                        clean_text = clean_text.replace("[图片]", "").replace("[文件]", "").strip()
                    new_title = clean_text[:15] + ("..." if len(clean_text) > 15 else "")
                    if new_title.strip():
                        from src.models.database import rename_conversation
                        rename_conversation(self._current_conv_id, new_title)
                        self.chat_area.set_title(f"💬 {new_title}")
                    break

        if not self._try_init_model():
            self.chat_area.append_error(tr("error.ollama_not_running"))
            return

        from src.core.engine import ConversationEngine
        spec_prompt = None
        if self._current_spec_id:
            from src.models.database import get_specializations
            specs = get_specializations()
            for s in specs:
                if s["id"] == self._current_spec_id and s.get("system_prompt"):
                    spec_prompt = s["system_prompt"]
                    break
        self._engine = ConversationEngine(
            self.model_manager, self._current_conv_id,
            specialization_prompt=spec_prompt
        )

        self._is_processing = True
        self.chat_area.set_generating(True)

        if video:
            self.chat_area.start_ai_message()
            self._video_worker = VideoStreamWorker(
                self.model_manager, self._engine, text, video, self._current_conv_id
            )
            self._video_worker.chunk_ready.connect(self._on_chunk)
            self._video_worker.finished.connect(self._on_stream_done)
            self._video_worker.error.connect(self._on_stream_error)
            self._video_worker.start()
        elif images:
            self.chat_area.start_ai_message()
            self._worker = ImageStreamWorker(self.model_manager, self._engine, text, images, self._current_conv_id)
            self._worker.chunk_ready.connect(self._on_chunk)
            self._worker.finished.connect(self._on_stream_done)
            self._worker.error.connect(self._on_stream_error)
            self._worker.start()
        elif files:
            self._handle_files(text, files)
        else:
            self.chat_area.start_ai_message()
            self._worker = StreamWorker(self._engine, text)
            self._worker.chunk_ready.connect(self._on_chunk)
            self._worker.finished.connect(self._on_stream_done)
            self._worker.error.connect(self._on_stream_error)
            self._worker.start()

    def _on_image_selected(self, image_paths):
        pass  # handled in _on_send_message

    def _on_video_selected(self, text, video_path):
        """处理视频选择：启动视频分析"""
        if not self._current_conv_id:
            self._on_new_chat(None)

        if not self._try_init_model():
            self.chat_area.append_error(tr("error.ollama_not_running_video"))
            return

        from src.core.video_processor import get_video_info
        info = get_video_info(video_path)
        if info is None:
            self.chat_area.append_error(tr("error.video_unsupported"))
            return

        display_text = text if text else tr("error.video_analyze_prompt")
        self.chat_area.append_user_message(display_text)
        self.chat_area.start_ai_message()
        self.chat_area.set_generating(True)

        self._video_worker = VideoStreamWorker(
            self.model_manager, self._engine, text, video_path, self._current_conv_id
        )
        self._video_worker.chunk_ready.connect(self._on_chunk)
        self._video_worker.finished.connect(self._on_stream_done)
        self._video_worker.error.connect(self._on_stream_error)
        self._video_worker.start()

    def _on_file_selected(self, file_paths):
        pass  # handled in _on_send_message

    def _handle_files(self, text, file_paths):
        from pathlib import Path
        parts = []
        if text and text not in ("[文件]",):
            parts.append(text)

        for fp in file_paths:
            p = Path(fp)
            try:
                suffix = p.suffix.lower()
                if suffix in (".txt", ".md", ".py", ".js", ".java", ".c", ".cpp", ".h", ".html", ".css", ".json", ".yaml", ".yml", ".xml", ".csv", ".log", ".ini", ".cfg", ".sh", ".bat"):
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(5000)
                    parts.append(f"--- 文件: {p.name} ---\n{content}")
                elif suffix == ".pdf":
                    # PDF 解析
                    try:
                        import PyPDF2
                        reader = PyPDF2.PdfReader(str(fp))
                        pdf_text = ""
                        max_pages = min(len(reader.pages), 20)
                        for i in range(max_pages):
                            pdf_text += reader.pages[i].extract_text() + "\n"
                        pdf_text = pdf_text.strip()[:8000]
                        parts.append(f"--- PDF文件: {p.name} ({len(reader.pages)}页) ---\n{pdf_text}")
                    except Exception as pdf_e:
                        parts.append(tr("file_parse.pdf_fail", name=p.name, error=pdf_e))
                elif suffix == ".docx":
                    # Word 文档解析
                    try:
                        import docx
                        doc = docx.Document(str(fp))
                        doc_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:8000]
                        parts.append(f"--- Word文档: {p.name} ---\n{doc_text}")
                    except Exception as docx_e:
                        parts.append(tr("file_parse.word_fail", name=p.name, error=docx_e))
                elif suffix == ".xlsx":
                    # Excel 简易解析
                    try:
                        import csv
                        from io import StringIO
                        import openpyxl
                        wb = openpyxl.load_workbook(str(fp), read_only=True)
                        sheet = wb.active
                        rows_data = []
                        for row in sheet.iter_rows(max_row=50, values_only=True):
                            row_text = " | ".join(str(c) if c else "" for c in row)
                            rows_data.append(row_text)
                        parts.append(f"--- Excel文件: {p.name} ---\n" + "\n".join(rows_data))
                    except Exception:
                        parts.append(f"[Excel解析失败: {p.name}]")
                elif suffix == ".pptx":
                    # PowerPoint 解析
                    try:
                        from pptx import Presentation
                        prs = Presentation(str(fp))
                        slides_text = []
                        for i, slide in enumerate(prs.slides, 1):
                            slide_content = []
                            for shape in slide.shapes:
                                if shape.has_text_frame:
                                    for para in shape.text_frame.paragraphs:
                                        text = para.text.strip()
                                        if text:
                                            slide_content.append(text)
                            if slide_content:
                                slides_text.append(f"[第{i}页] " + " | ".join(slide_content[:5]))
                        ppt_text = "\n".join(slides_text[:20])[:6000]
                        parts.append(f"--- PPT文件: {p.name} ({len(prs.slides)}页) ---\n{ppt_text}")
                    except Exception as pptx_e:
                        parts.append(tr("file_parse.ppt_fail", name=p.name, error=pptx_e))
                elif suffix in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"):
                    # 音频文件 - 提示用户
                    parts.append(tr("file_parse.audio_hint", name=p.name))
                elif suffix in (".zip", ".rar", ".7z", ".tar", ".gz"):
                    # 压缩文件
                    parts.append(tr("file_parse.archive_hint", name=p.name))
                elif suffix in (".exe", ".msi", ".dll", ".bat", ".cmd", ".ps1", ".sh"):
                    # 可执行文件 - 安全拒绝
                    parts.append(tr("file_parse.executable_hint", name=p.name))
                else:
                    parts.append(tr("file_parse.unsupported_hint", name=p.name))
            except Exception as e:
                parts.append(tr("file_parse.read_fail", name=p.name, error=e))

        combined_text = "\n\n".join(parts)
        self.chat_area.start_ai_message()
        self._worker = StreamWorker(self._engine, combined_text)
        self._worker.chunk_ready.connect(self._on_chunk)
        self._worker.finished.connect(self._on_stream_done)
        self._worker.error.connect(self._on_stream_error)
        self._worker.start()

    def _on_chunk(self, msg_type, content):
        if msg_type == "text":
            self.chat_area.append_ai_chunk(content)
        elif msg_type == "warning":
            self.chat_area.show_warning(content)
        elif msg_type == "error":
            self.chat_area.append_error(content)
        elif msg_type == "thinking":
            self.chat_area.show_thinking(content)
        elif msg_type == "source":
            self.chat_area.show_source(content)

    def _on_stream_done(self):
        self.chat_area.end_ai_message()
        self._is_processing = False
        self._load_conversations()
        self._process_next_in_queue()

    def _on_stream_error(self, err_msg):
        self.chat_area.append_error(tr("error.send_failed", error=err_msg))
        self.chat_area.end_ai_message()
        self._is_processing = False
        self._process_next_in_queue()

    def _on_stop_generation(self):
        """用户停止生成"""
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
        if hasattr(self, '_video_worker') and self._video_worker and self._video_worker.isRunning():
            self._video_worker.stop()
            self._video_worker.terminate()
            self._video_worker.wait(2000)
        self.chat_area.end_ai_message()
        self._is_processing = False
        # 停止时清空队列
        self._msg_queue.clear()
        self.chat_area.set_generating(False)

    def _process_next_in_queue(self):
        """处理队列中的下一条消息"""
        if self._msg_queue:
            next_item = self._msg_queue.pop(0)
            self._update_queue_ui()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(150, lambda: self._process_message(next_item))
        else:
            self.chat_area.set_generating(False)
            self._update_queue_ui()

    def _update_queue_ui(self):
        """更新队列状态显示"""
        count = len(self._msg_queue)
        # 后台任务数
        bg_running = 0
        try:
            from src.core.background_task_manager import get_background_task_manager
            bg_running = get_background_task_manager().get_running_count()
        except Exception as _e: logger.debug(f"[main_window] 忽略: {_e}")

        parts = []
        if count > 0:
            parts.append(f"📝 {tr('queue.pending', count=count)}")
        if bg_running > 0:
            parts.append(f"⚙️ {tr('bg_task.running_count', count=bg_running)}")

        if parts:
            self.statusBar().showMessage("  |  ".join(parts))
        else:
            model = self.config.get("ollama.default_model", "qwen2.5:14b")
            vram = self.hw_info.get("vram_gb", "?")
            version = tr("status.version_advanced") if self.hw_info.get("version") == "advanced" else tr("status.version_normal")
            self.statusBar().showMessage(
                f"🧠 {tr('status.model', model=model)}  |  🎮 {tr('status.vram', vram=vram)}  |  {version}  |  {tr('status.local_run')}"
            )

    def _chat_display_scroll_to_bottom(self):
        sb = self.chat_area.chat_display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def refresh_translations(self):
        """语言切换后刷新所有界面文案"""
        self.setWindowTitle(tr("app.name"))
        # 侧边栏
        self.sidebar.logo.setText(tr("sidebar.logo"))
        self.sidebar.new_chat_btn.setText(tr("sidebar.new_chat"))
        self.sidebar.search.setPlaceholderText(tr("sidebar.search_placeholder"))
        self.sidebar.global_search_btn.setText(tr("sidebar.global_search"))
        self.sidebar.spec_title.setText(tr("sidebar.specializations"))
        self.sidebar.conv_title_label.setText(tr("sidebar.recent_chats"))
        self.sidebar.multi_select_btn.setText(tr("sidebar.multi_select"))
        self.sidebar.select_all_btn.setText(tr("sidebar.select_all"))
        self.sidebar.delete_selected_btn.setText(tr("sidebar.delete_selected", count=0))
        # 侧边栏底部按钮
        btn_keys = {"knowledge": "sidebar.knowledge", "tasks": "sidebar.tasks", "settings": "sidebar.settings"}
        for btn in self.sidebar._bottom_btns:
            fid = btn.property("func_id")
            if fid in btn_keys:
                btn.setText(tr(btn_keys[fid]))
        # 聊天区域
        self.chat_area.chat_title.setText(tr("chat.title_new"))
        self.chat_area.input_field.setPlaceholderText(tr("chat.input_placeholder"))
        self.chat_area.send_btn.setText(tr("btn.send"))
        self.chat_area.stop_btn.setText(tr("btn.stop"))
        self.chat_area.attach_btn.setText(tr("chat.attach_btn"))
        self.chat_area.voice_btn.setToolTip(tr("chat.voice_tooltip"))
        # 设置面板
        self.settings_drawer.title_label.setText(tr("settings.title"))
        self.settings_drawer.save_btn.setText(tr("settings.btn_save"))
        self.settings_drawer.profile_btn.setText(tr("settings.btn_profile"))
        self.settings_drawer.memory_btn.setText(tr("settings.btn_memory"))
        # 状态栏
        self.statusBar().showMessage(tr("settings.saved"))
