# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
自定义插件模板 - 复制这个文件来创建自己的插件

使用方法：
1. 复制这个文件，改名为 my_plugin.py（名字随意，必须以 _plugin.py 结尾）
2. 修改 MyPlugin 类里的内容
3. 重启 Harven AI（双击 启动HarvenAI.bat）
4. 插件自动生效
"""

import re
from src.core.plugin_system import PluginBase, PluginResult, PluginContext


class MyPlugin(PluginBase):
    """
    自定义插件 - 修改这个类来实现你的功能
    """

    # ===== 插件基本信息（改这里）=====
    name = "my_plugin"           # 插件ID，英文，不能有空格
    version = "1.0.0"          # 版本号
    description = "我的自定义插件"  # 描述
    author = "Your Name"          # 作者

    # ===== 触发词配置（改这里）=====
    # 当用户消息匹配这些正则时，插件会被调用
    # 每个元组 = (正则表达式, 置信度 0.0-1.0)
    # 置信度越高 = 越优先被调用
    _TRIGGERS = [
        (r"帮我", 0.8),            # 消息包含"帮我" → 置信度 0.8
        (r"执行", 0.9),             # 消息包含"执行" → 置信度 0.9
        (r"运行", 0.7),             # 消息包含"运行" → 置信度 0.7
    ]

    # ===== 核心方法1：决定是否处理这条消息 =====
    def should_handle(self, message: str) -> float:
        """
        返回 0.0-1.0 之间的置信度
        返回 0.0 = 不处理
        返回 > 0.0 = 处理，数值越高越优先
        """
        for pattern, score in self._TRIGGERS:
            if re.search(pattern, message):
                return score
        return 0.0

    # ===== 核心方法2：执行插件逻辑 =====
    def execute(self, context: PluginContext) -> PluginResult:
        """
        插件的主要功能写在这里
        """
        user_msg = context.user_message  # 用户的原始消息
        conv_id = context.conversation_id  # 当前对话ID

        # ---- 在这里写你的逻辑 ----

        # 示例1：简单回复
        if "帮我" in user_msg:
            return PluginResult(
                success=True,
                message="✅ 插件已收到你的请求！",
                data={"action": "help", "input": user_msg}
            )

        # 示例2：调用外部命令
        if "执行" in user_msg:
            import subprocess
            # 提取要执行的命令（示例：执行 "执行: dir"）
            cmd_match = re.search(r"执行\s*[:：]?\s*(.+)", user_msg)
            if cmd_match:
                cmd = cmd_match.group(1).strip()
                try:
                    result = subprocess.run(
                        cmd, shell=True, capture_output=True,
                        text=True, timeout=10
                    )
                    return PluginResult(
                        success=True,
                        message=f"执行结果：\n{result.stdout[:500]}",
                        data={"cmd": cmd, "output": result.stdout}
                    )
                except Exception as e:
                    return PluginResult(
                        success=False,
                        message=f"执行失败：{e}"
                    )

        # 示例3：保存到知识库
        if "记住" in user_msg:
            content = user_msg.replace("记住", "").strip()
            context.save_to_knowledge(content, source=f"plugin:{conv_id}")
            return PluginResult(
                success=True,
                message=f"✅ 已记住：{content[:50]}",
                data={"action": "save_knowledge", "content": content}
            )

        # 没匹配到任何逻辑
        return PluginResult(
            success=False,
            message="插件未识别到可处理的指令"
        )


# ===== 必须保留：导出插件类 =====
plugin_class = MyPlugin
