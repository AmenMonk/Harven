"""
ChatArea — 聊天主区域组件
包含：聊天显示区、输入框、发送/停止/附件/语音按钮、标题
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QLineEdit, QPushButton, QLabel, QFrame,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QIcon

from src.utils.i18n import tr


class ChatArea(QWidget):
    """聊天主区域"""

    # 信号
    send_message = Signal(str, list, list, object)  # text, images, files, video
    image_selected = Signal(str)
    file_selected = Signal(str)
    video_selected = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending_images = []
        self._pending_files = []
        self._pending_video = None
        self._generating = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏
        title_frame = QFrame()
        title_frame.setFixedHeight(48)
        title_frame.setStyleSheet(
            "QFrame { background: #ffffff; border-bottom: 1px solid #e0e0e0; }"
        )
        title_layout = QHBoxLayout(title_frame)
        title_layout.setContentsMargins(16, 0, 16, 0)
        self.chat_title = QLabel(tr("sidebar.new_chat"))
        self.chat_title.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        title_layout.addWidget(self.chat_title)
        title_layout.addStretch()
        layout.addWidget(title_frame)

        # 聊天显示区
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet(
            "QTextEdit { background: #f7f7f8; border: none; padding: 16px; }"
        )
        layout.addWidget(self.chat_display, 1)

        # 输入区域
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "QFrame { background: #ffffff; border-top: 1px solid #e0e0e0; padding: 8px; }"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(6)

        self.attach_btn = QPushButton(tr("chat.attach_btn"))
        self.attach_btn.setFixedSize(36, 36)
        self.attach_btn.setStyleSheet(
            "QPushButton { border: none; font-size: 18px; border-radius: 18px; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        input_layout.addWidget(self.attach_btn)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(tr("chat.input_placeholder"))
        self.input_field.setStyleSheet(
            "QLineEdit { border: 1px solid #d0d0d0; border-radius: 8px; "
            "padding: 8px 12px; font-size: 14px; }"
        )
        self.input_field.returnPressed.connect(self._on_input_send)
        input_layout.addWidget(self.input_field, 1)

        self.voice_btn = QPushButton("🎤")
        self.voice_btn.setFixedSize(36, 36)
        self.voice_btn.setToolTip(tr("chat.voice_tooltip"))
        self.voice_btn.setStyleSheet(
            "QPushButton { border: none; font-size: 18px; border-radius: 18px; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        input_layout.addWidget(self.voice_btn)

        self.send_btn = QPushButton(tr("btn.send"))
        self.send_btn.setFixedSize(72, 36)
        self.send_btn.setStyleSheet(
            "QPushButton { background: #4a7bf7; color: #fff; border: none; "
            "border-radius: 8px; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { background: #5a8bf8; }"
            "QPushButton:disabled { background: #c8d8f8; }"
        )
        self.send_btn.clicked.connect(self._on_input_send)
        input_layout.addWidget(self.send_btn)

        self.stop_btn = QPushButton(tr("btn.stop"))
        self.stop_btn.setFixedSize(72, 36)
        self.stop_btn.setVisible(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background: #ff6b6b; color: #fff; border: none; "
            "border-radius: 8px; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { background: #ff5252; }"
        )
        self.stop_btn.clicked.connect(self._on_stop)
        input_layout.addWidget(self.stop_btn)

        layout.addWidget(input_frame)

    def _on_input_send(self):
        text = self.input_field.text().strip()
        if not text and not self._pending_images and not self._pending_files:
            return
        self.send_message.emit(
            text, self._pending_images[:], self._pending_files[:], self._pending_video
        )
        self.input_field.clear()
        self._pending_images.clear()
        self._pending_files.clear()
        self._pending_video = None

    def _on_stop(self):
        self.stop_requested.emit()

    # ---- 公开方法 ----

    def append_user_message(self, text, image_paths=None, file_paths=None):
        """追加用户消息到显示区"""
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)

        html = f'<p style="text-align:right;"><span style="background:#e3f2fd;padding:8px 12px;'
        html += f'border-radius:12px;display:inline-block;max-width:70%;">{self._escape(text)}</span></p>'
        self.chat_display.append(html)

    def append_ai_message(self, text):
        """追加AI消息到显示区"""
        html = f'<p><span style="background:#ffffff;padding:8px 12px;'
        html += f'border-radius:12px;display:inline-block;max-width:70%;border:1px solid #e0e0e0;">'
        html += self._escape(text).replace("\n", "<br>") + "</span></p>"
        self.chat_display.append(html)

    def start_ai_message(self):
        """开始AI回复（准备流式输出）"""
        self._ai_buffer = ""
        self.chat_display.append(
            '<p><span id="ai-stream" style="background:#ffffff;padding:8px 12px;'
            'border-radius:12px;display:inline-block;max-width:70%;border:1px solid #e0e0e0;">'
            '<i>思考中...</i></span></p>'
        )

    def append_ai_chunk(self, content):
        """追加AI流式输出块"""
        self._ai_buffer += content
        # 简单追加，生产环境可用 QTextCursor 精确替换
        # 这里用简化方案：每次重新渲染最后一条消息
        pass  # 由 engine 层直接通过信号更新，这里留空

    def end_ai_message(self):
        """结束AI回复"""
        self._ai_buffer = ""

    def append_error(self, text):
        """显示错误信息"""
        html = f'<p><span style="color:#ff5252;background:#fff0f0;padding:8px 12px;'
        html += f'border-radius:8px;display:inline-block;">⚠️ {self._escape(text)}</span></p>'
        self.chat_display.append(html)

    def show_warning(self, text):
        """显示警告信息"""
        html = f'<p><span style="color:#ff9800;background:#fff8e1;padding:8px 12px;'
        html += f'border-radius:8px;display:inline-block;">⚠️ {self._escape(text)}</span></p>'
        self.chat_display.append(html)

    def show_thinking(self, text):
        """显示思考过程"""
        html = f'<details><summary style="color:#999;cursor:pointer;">思考过程</summary>'
        html += f'<p style="color:#666;font-size:12px;">{self._escape(text).replace(chr(10), "<br>")}</p></details>'
        self.chat_display.append(html)

    def show_source(self, text):
        """显示来源信息"""
        html = f'<p style="color:#4a7bf7;font-size:12px;">📎 来源: {self._escape(text)}</p>'
        self.chat_display.append(html)

    def clear_chat(self):
        """清空聊天显示"""
        self.chat_display.clear()

    def set_title(self, title):
        """设置聊天标题"""
        self.chat_title.setText(title)

    def set_generating(self, generating):
        """设置生成状态（切换发送/停止按钮）"""
        self._generating = generating
        self.send_btn.setVisible(not generating)
        self.stop_btn.setVisible(generating)
        self.input_field.setEnabled(not generating)

    def _escape(self, text):
        """HTML转义"""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def set_input_placeholder(self, text):
        self.input_field.setPlaceholderText(text)

    def set_send_btn_text(self, text):
        self.send_btn.setText(text)

    def set_stop_btn_text(self, text):
        self.stop_btn.setText(text)

    def set_attach_btn_text(self, text):
        self.attach_btn.setText(text)

    def set_voice_btn_tooltip(self, text):
        self.voice_btn.setToolTip(text)
