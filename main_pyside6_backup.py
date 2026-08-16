"""
Harven AI — 个人专属AI大脑
启动入口

Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import sys
import os

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QProgressBar
from PySide6.QtCore import Qt, QThread, Signal as pyqtSignal, QTimer
from PySide6.QtGui import QFont
from src.utils.config import load_config
from src.utils.hardware import detect_hardware, check_requirements, recommend_version
from src.utils.i18n import tr, set_language, get_language
import logging
logger = logging.getLogger(__name__)


class SetupWizard(QDialog):
    """首次启动引导向导"""

    def __init__(self, config, hw_info, parent=None):
        super().__init__(parent)
        self.config = config
        self.hw_info = hw_info
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(tr("setup.title"))
        self.setMinimumSize(550, 400)
        self.setStyleSheet("background-color: #ffffff;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(16)

        # Logo
        title = QLabel(tr("sidebar.logo"))
        title.setFont(QFont("Microsoft YaHei", 28, QFont.Weight.Bold))
        title.setStyleSheet("color: #4a7bf7;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(tr("app.subtitle"))
        subtitle.setFont(QFont("Microsoft YaHei", 12))
        subtitle.setStyleSheet("color: #666;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        # 硬件信息
        hw_text = QLabel()
        gpu = self.hw_info.get("gpu_name", tr("setup.gpu_not_detected"))
        vram = self.hw_info.get("vram_gb", 0)
        ram = self.hw_info.get("ram_gb", 0)
        version = self.hw_info.get("version", "normal")
        ver_name = tr("setup.version_advanced") if version == "advanced" else tr("setup.version_normal")
        hw_text.setText(
            f"🖥️ GPU: {gpu}\n"
            f"🎮 VRAM: {vram}GB  |  💾 RAM: {ram}GB\n"
            f"📦 {ver_name}"
        )
        hw_text.setStyleSheet("background: #f5f7fa; padding: 16px; border-radius: 12px; font-size: 13px; color: #333;")
        layout.addWidget(hw_text)

        # 进度区域
        self.status_label = QLabel(tr("setup.checking_env"))
        self.status_label.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 3)
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            "QProgressBar { border: 1px solid #ddd; border-radius: 6px; text-align: center; height: 24px; }"
            "QProgressBar::chunk { background-color: #4a7bf7; border-radius: 5px; }"
        )
        layout.addWidget(self.progress)

        layout.addStretch()

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.exit_btn = QPushButton(tr("setup.btn_exit"))
        self.exit_btn.setStyleSheet(
            "QPushButton { background: #e0e0e0; color: #333; border: none; "
            "border-radius: 10px; padding: 10px 28px; font-size: 14px; }"
            "QPushButton:hover { background: #d0d0d0; }"
        )
        self.exit_btn.clicked.connect(self._on_exit)
        btn_layout.addWidget(self.exit_btn)

        self.start_btn = QPushButton(tr("setup.btn_start"))
        self.start_btn.setStyleSheet(
            "QPushButton { background: #4a7bf7; color: #fff; border: none; "
            "border-radius: 10px; padding: 10px 28px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #5a8bf8; }"
            "QPushButton:disabled { background: #c8d8f8; color: #e8e8f0; }"
        )
        self.start_btn.clicked.connect(self._on_start)
        self.start_btn.setEnabled(False)
        btn_layout.addWidget(self.start_btn)
        layout.addLayout(btn_layout)

        # 延迟检查
        QTimer.singleShot(500, self._check_environment)

    def _check_environment(self):
        # Step 1: Check Ollama
        self.status_label.setText(tr("setup.checking_ollama"))
        from src.core.model_manager import check_ollama
        ok, msg = check_ollama(self.config.get("ollama.base_url", "http://localhost:11434"))
        self.progress.setValue(1)

        if ok:
            self.status_label.setText(f"{tr('setup.ollama_ok')}\n{msg}")
        else:
            self.status_label.setText(
                f"{tr('setup.ollama_not_running')}\n{msg}\n\n"
                f"{tr('setup.ollama_not_running_detail')}"
            )

        # Step 2: Check dependencies
        self.progress.setValue(2)
        deps_ok = self._check_deps()
        if not deps_ok:
            self.status_label.setText(self.status_label.text() + "\n\n" + tr("setup.deps_missing"))

        # Step 3: Done
        self.progress.setValue(3)
        self.start_btn.setEnabled(True)

    def _check_deps(self):
        """检查关键依赖"""
        missing = []
        try:
            import PySide6
        except ImportError:
            missing.append("PySide6")
        try:
            import yaml
        except ImportError:
            missing.append("pyyaml")
        try:
            import openai
        except ImportError:
            missing.append("openai")
        if missing:
            self.status_label.setText(
                self.status_label.text() + "\n\n" + tr("setup.deps_missing_list", missing=", ".join(missing))
            )
            return False
        return True

    def _on_start(self):
        self.accept()

    def _on_exit(self):
        self.reject()


def main():
    # 高DPI支持
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("Harven AI")
    app.setApplicationDisplayName(tr("app.name"))

    # 加载配置
    config = load_config()

    # ── 启动安全检查与维护（对标 web_app.py） ──
    # 元数据分片自动迁移
    try:
        from src.core.meta_sharder import auto_shard_meta_on_startup
        auto_shard_meta_on_startup()
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")

    # Ollama 进程守护（默认自动激活）
    if os.environ.get('HARVEN_AUTO_OLLAMA', '1').lower() in ('1', 'true', 'yes'):
        try:
            from src.core.ollama_daemon import start_ollama_daemon
            start_ollama_daemon()
        except Exception as _e: logger.debug(f"[main] 忽略: {_e}")

    # 过渡库清理
    try:
        from src.core.db_router import auto_cleanup_transient
        auto_cleanup_transient()
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")

    # 记忆驱逐
    try:
        from src.core.memory_eviction import auto_evict_memories
        auto_evict_memories(max_memories=1000)
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")

    # 对话归档
    try:
        from src.core.conversation_archiver import auto_archive_old_conversations
        auto_archive_old_conversations(days=30)
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")

    # 设备安全校验（硬件指纹 + 克隆检测）
    try:
        from src.core.hw_security import init_security_db, get_device_security_status
        init_security_db()
        status = get_device_security_status()
        if status.get("clone_warnings"):
            logger.warning(f"[安全] ⚠️ 检测到 {len(status['clone_warnings'])} 条克隆警告")
        level = status.get("security_level", "unbound")
        logger.debug(f"[安全] 设备安全等级: {level}")
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")

    # ── 独立性防御体系启动检查（P0/P1/P2） ──
    try:
        from src.core.independence import startup_independence_check
        all_ok, messages = startup_independence_check()
        for msg in messages:
            logger.debug(f"[独立性] {msg}")
        if not all_ok:
            logger.warning("[独立性] ⚠️ 部分独立性检查未通过，请查看上方消息")
    except Exception as e:
        logger.error(f"[独立性] ❌ 独立性检查异常: {e}")

    # 知识库自动导入（后台线程）
    try:
        from src.core.knowledge_base import get_knowledge_base, auto_import_new_files
        kb = get_knowledge_base()
        auto_import_new_files(kb, callback=lambda count, results: (
            logger.info("[知识库] 自动导入完成: %s 个文件", count) if count else None
        ))
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")

    # 加载用户保存的语言偏好
    from src.models.database import get_preference
    saved_lang = get_preference("language")
    if saved_lang:
        set_language(saved_lang)

    # 检测硬件
    hw_info = detect_hardware()

    # 检查最低要求（CPU 模式允许无显卡运行）
    ok, issues, run_mode = check_requirements(hw_info, allow_cpu=True)
    if not ok:
        issue_text = "\n".join(f"  {i}" for i in issues)
        reply = QMessageBox.critical(
            None, tr("setup.hw_not_met"),
            tr("setup.hw_not_met_detail", issues=issue_text),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.No:
            sys.exit(1)
    elif "CPU 模式" in str(issues):
        # CPU-only mode: warn but allow
        issue_text = "\n".join(f"  {i}" for i in issues)
        QMessageBox.warning(
            None, "硬件提示",
            f"检测到以下情况，Harven AI 将以 CPU 模式运行（速度较慢）：\n\n{issue_text}\n\n建议：\n• 使用 qwen2.5:7b 或更小的量化模型\n• 等待时间会稍长，请耐心使用",
            QMessageBox.StandardButton.Ok
        )

    # 推荐版本
    version = recommend_version(hw_info)
    hw_info["version"] = version
    hw_info["run_mode"] = run_mode  # 'gpu' or 'cpu'

    # 更新默认模型
    if version == "advanced":
        model = config.get("advanced_model", "qwen2.5:14b")
    elif version == "cpu":
        model = config.get("normal_model", "qwen2.5:7b")  # CPU 模式用小模型
    else:
        model = config.get("normal_model", "qwen2.5:7b")
    config.set("ollama.default_model", model)

    # 初始化数据库
    from src.models.database import init_database
    init_database()

    # 加载插件
    try:
        from src.core.plugin_system import get_plugin_manager
        get_plugin_manager().load_plugins()
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")  # 插件加载失败不影响主程序

    # 首次启动引导（兼容 PyQt5/PyQt6）
    wizard = SetupWizard(config, hw_info)
    try:
        wizard_dlg = wizard.exec if hasattr(wizard, 'exec') else wizard.exec_
        if wizard_dlg() != QDialog.DialogCode.Accepted:
            sys.exit(0)
    except Exception as _e: logger.debug(f"[main] 忽略: {_e}")  # 向导异常时不阻止启动

    # 启动主窗口
    from src.ui.main_window import MainWindow
    window = MainWindow(config, hw_info)
    window.show()
    app.main_window = window  # 保存引用供 i18n.refresh_ui() 使用

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
