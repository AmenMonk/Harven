# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
计算器插件 — 处理数学运算请求
"""
import re
from datetime import datetime
from src.core.plugin_system import PluginBase, PluginResult, PluginContext


class CalculatorPlugin(PluginBase):
    name = "calculator"
    version = "1.0.0"
    description = "数学计算器，支持基本运算和表达式求值"
    author = "Harven AI"

    # 匹配模式
    _MATH_PATTERNS = [
        (r"计算\s*[:：]?\s*(.+?)(?:$|。|？|\?|，)", 0.9),
        (r"算一下\s*[:：]?\s*(.+?)(?:$|。|？|\?|，)", 0.9),
        (r"(?:等于|是多少|等于多少)\s*[:：]?\s*$", 0.7),  # 结合上下文
        (r"^\s*[\d+\-*/().\s^%]+$", 0.95),  # 纯数学表达式
        (r"[\d]+\s*[\+\-\*/]\s*[\d]+", 0.8),  # 简单算式
    ]

    _SAFE_GLOBALS = {
        "__builtins__": {
            "abs": abs, "round": round, "min": min, "max": max,
            "sum": sum, "pow": pow, "int": int, "float": float,
            "len": len, "range": range,
        },
        "math": __import__("math"),
    }

    def should_handle(self, message: str) -> float:
        for pattern, score in self._MATH_PATTERNS:
            if re.search(pattern, message):
                return score
        return 0.0

    def execute(self, context: PluginContext) -> PluginResult:
        msg = context.user_message

        # 提取数学表达式
        expr = self._extract_expression(msg)
        if not expr:
            return PluginResult(success=False, message="未识别到有效表达式")

        # 安全求值
        try:
            result = eval(expr, {"__builtins__": {}}, self._SAFE_GLOBALS)
            return PluginResult(
                success=True,
                message=f"计算结果：{expr} = {result}",
                data={"expression": expr, "result": str(result)},
            )
        except ZeroDivisionError:
            return PluginResult(success=False, message="错误：不能除以零")
        except Exception as e:
            return PluginResult(success=False, message=f"计算错误：{str(e)}")

    def _extract_expression(self, msg: str) -> str:
        """从消息中提取数学表达式"""
        # 模式1：纯数学表达式
        if re.match(r"^[\d+\-*/().\s^%]+$", msg):
            return msg.strip()

        # 模式2：计算: / 算一下: 后面的内容
        for prefix in ["计算:", "计算：", "算一下:", "算一下：", "计算", "算一下"]:
            if prefix in msg:
                idx = msg.find(prefix) + len(prefix)
                expr = msg[idx:].strip(" 。！？!?，,：:")
                # 移除中文
                expr = re.sub(r"[^\d+\-*/().\s^%\.]", "", expr)
                return expr.strip()

        return ""


# 导出插件类（PluginManager 自动发现）
plugin_class = CalculatorPlugin
