# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
协作模式管理器 v2.0
三种协作模式：
- off：关闭协作（独立模式）
- cloud_local：云地协作（外脑分析 + 本地执行整合）
- local_dual：本地双模（本地通用模型 + 本地垂直模型联动）
"""
import json
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 协作模式定义
COLLAB_MODES = {
    "off": "关闭协作 — 由回答引擎独立工作",
    "cloud_local": "☁️ 云地协作 — 云端大模型思考 → 本地垂直模型干活",
    "local_dual": "🏠 本地双模 — 本地通用模型调度 → 垂直模型执行",
}

# 隐私过滤级别说明
PRIVACY_LEVELS = {
    "strict": "严格 — 不传用户数据（仅传去敏的问题摘要）",
    "balanced": "平衡 — 传去敏数据（移除姓名/地点/数字等敏感信息）",
    "full": "全量 — 传全部数据（仅限完全信任的外脑）",
}

# 敏感词替换规则（用于 balanced 模式去敏）
_SENSITIVE_PATTERNS = [
    (r'\b\d{11,18}\b', '[ID号]'),       # 身份证号
    (r'\b1[3-9]\d{9}\b', '[手机号]'),    # 手机号
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '[邮箱]'),
    (r'\b\d{3,4}-\d{7,8}\b', '[电话]'),  # 固定电话
    (r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP地址]'),  # IP
    (r'(?:北京|上海|广州|深圳|杭州|成都|武汉|南京|天津|重庆|苏州|西安|长沙|青岛|大连|厦门|宁波|郑州|济南|沈阳|昆明|福州|合肥|哈尔滨|长春|石家庄|南宁|贵阳|太原|兰州|海口|乌鲁木齐|呼和浩特|银川|西宁|拉萨)(?:市|区|县)?', '[城市]'),
    (r'\b(?:\d+\.?\d*)\s*(?:万元?|亿元?|美元|欧元|日元|英镑|港币|澳元)\b', '[金额]'),
]


class CollabManager:
    """协作模式管理器（单例）"""

    def __init__(self, config=None):
        self._config = config or {}
        # P2: collab_mode 优先级明确 — collab_mode 字段优先，collab_enabled 仅做兼容
        raw_mode = self._config.get('collab_mode', None)
        if raw_mode and raw_mode in COLLAB_MODES:
            self._mode = raw_mode
        elif self._config.get('collab_enabled') is True and not raw_mode:
            # 仅当 collab_mode 未设置时才看 collab_enabled（兼容极旧配置）
            self._mode = 'cloud_local'
        else:
            self._mode = 'off'
        self._foreign_model = self._config.get('collab_foreign_model', 'doubao')
        self._privacy_level = self._config.get('collab_privacy_level', 'full')
        self.deep_search = self._config.get('collab_deep_search', False)

    # ── 属性 ──

    @property
    def mode(self) -> str:
        """协作模式：off | cloud_local | local_dual"""
        return self._mode

    @property
    def enabled(self) -> bool:
        """是否开启协作（兼容旧接口）"""
        return self._mode != 'off'

    @property
    def is_cloud_local(self) -> bool:
        """是否为云地协作模式"""
        return self._mode == 'cloud_local'

    @property
    def is_local_dual(self) -> bool:
        """是否为本地双模模式"""
        return self._mode == 'local_dual'

    @property
    def foreign_model(self) -> str:
        return self._foreign_model

    @property
    def privacy_level(self) -> str:
        return self._privacy_level

    # ── 状态管理 ──

    def update(self, mode=None, enabled=None, foreign_model=None, privacy_level=None):
        """更新协作配置。mode 优先于 enabled（兼容旧调用）"""
        if mode is not None and mode in COLLAB_MODES:
            self._mode = mode
        elif enabled is not None:
            # 兼容旧 enabled bool
            self._mode = 'cloud_local' if enabled else 'off'
        if foreign_model is not None:
            self._foreign_model = foreign_model
        if privacy_level is not None and privacy_level in PRIVACY_LEVELS:
            self._privacy_level = privacy_level
        logger.info(
            f"[Collab] 配置更新: mode={self._mode}, "
            f"model={self._foreign_model}, privacy={self._privacy_level}"
        )

    def to_dict(self) -> dict:
        return {
            "mode": self._mode,
            "mode_label": COLLAB_MODES.get(self._mode, "未知"),
            "enabled": self._mode != 'off',  # 兼容
            "is_cloud_local": self._mode == 'cloud_local',
            "is_local_dual": self._mode == 'local_dual',
            "foreign_model": self._foreign_model,
            "privacy_level": self._privacy_level,
            "privacy_options": PRIVACY_LEVELS,
            "available_modes": COLLAB_MODES,
            "deep_search": self.deep_search,  # ★
        }

    # ── 隐私过滤 ──

    def filter_user_data(self, user_message: str) -> str:
        """根据当前隐私级别过滤用户数据"""
        if self._privacy_level == "full":
            return user_message
        if self._privacy_level == "strict":
            return self._filter_strict(user_message)
        return self._filter_balanced(user_message)

    @staticmethod
    def _filter_strict(message: str) -> str:
        """严格模式：仅传问题摘要，移除几乎所有细节"""
        # 提取核心问题关键词
        stripped = re.sub(r'[^\u4e00-\u9fff\w\s?!.。！？]', '', message)
        words = stripped.split()
        if len(words) > 20:
            return ' '.join(words[:20]) + '...'
        return stripped

    @staticmethod
    def _filter_balanced(message: str) -> str:
        """平衡模式：替换掉敏感信息"""
        result = message
        for pattern, replacement in _SENSITIVE_PATTERNS:
            result = re.sub(pattern, replacement, result)
        return result

    # ── Skills JSON 工具描述注入（OpenClaw 风格）──

    def build_tools_prompt(self, skills_manager, user_message: str = "") -> str:
        """
        从技能管理器构建 JSON 工具描述（OpenClaw 风格）
        注入到外脑的 system prompt 中，让外脑可以请求调用本地工具。
        
        Returns: 格式为 Markdown 的工具描述 prompt 片段
        """
        try:
            skills = skills_manager.list_skills()
        except Exception as e:
            logger.warning(f"[Collab] 获取技能列表失败: {e}")
            return ""

        # 过滤已启用的技能
        enabled_skills = [s for s in skills if s.get('enabled', True)]
        if not enabled_skills:
            return ""

        # 检查哪些技能匹配当前消息
        matching_skills = []
        for s in enabled_skills:
            triggers = s.get('triggers', [])
            if triggers:
                msg_lower = user_message.lower()
                if any(t.lower() in msg_lower for t in triggers):
                    matching_skills.append(s)
            else:
                # 没有触发词的技能也展示（通用能力）
                matching_skills.append(s)

        if not matching_skills:
            matching_skills = enabled_skills[:5]  # 最多展示5个通用技能

        # 构建 JSON 工具列表
        tools = []
        for s in matching_skills:
            tools.append({
                "name": s.get('name', s.get('id', '')),
                "id": s.get('id', ''),
                "description": s.get('description', ''),
                "triggers": s.get('triggers', [])[:5],
            })

        tools_json = json.dumps(tools, ensure_ascii=False, indent=2)

        prompt = f"""
## 可用工具（Skills）

你可以调用以下本地工具来协助完成任务。如需调用工具，请在回复末尾使用以下 JSON 格式：

```json
{{
  "tool_calls": [
    {{"tool": "技能名称", "action": "具体操作请求", "reason": "调用理由"}}
  ]
}}
```

### 工具列表

```json
{tools_json}
```

### 调用规则
1. **只能使用上面「工具列表」中列出的工具名，严禁编造或猜测不存在的工具**（如 python_expert、web_search 等不在列表中的名称一律不能用）
2. 工具调用 JSON 块必须放在回复的**最末尾**
3. 如果不需要调用工具，不要输出 tool_calls 块
4. 每次最多调用 3 个工具
5. 调用后系统会返回执行结果，你可以在下一轮对话中使用结果
6. 仅供参考：你的分析建议会传给本地通用模型做最终整合
7. **如果工具列表为空或没有匹配的工具，只提供纯文本分析，不要输出任何 JSON**
"""
        return prompt

    # ── 工具调用解析 ──

    def parse_tool_calls(self, response_text: str) -> list[dict]:
        """
        从外部模型回复中提取 JSON 工具调用。
        返回：list[dict]，每个 dict 包含 tool/action/reason
        """
        tools = []

        # 尝试多种 JSON 提取方式
        # 方式1：```json ... ``` 代码块
        json_blocks = re.findall(r'```json\s*([\s\S]*?)```', response_text)
        for block in json_blocks:
            parsed = self._try_parse_tools(block)
            tools.extend(parsed)

        # 方式2：独立的 JSON 对象
        json_objects = re.findall(r'\{[\s\S]*?"tool_calls"[\s\S]*?\}', response_text)
        for obj_str in json_objects:
            parsed = self._try_parse_tools(obj_str)
            tools.extend(parsed)

        return tools

    @staticmethod
    def _try_parse_tools(text: str) -> list[dict]:
        """尝试从文本解析工具调用"""
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict) and 'tool_calls' in data:
                return data['tool_calls']
        except json.JSONDecodeError as _e:
            logger.debug(f"[collab_manager] 忽略: {_e}")

        # 尝试修复常见 JSON 错误（多余逗号、引号问题）
        try:
            fixed = re.sub(r',\s*}', '}', text.strip())
            fixed = re.sub(r',\s*]', ']', fixed)
            data = json.loads(fixed)
            if isinstance(data, dict) and 'tool_calls' in data:
                return data['tool_calls']
        except json.JSONDecodeError as _e:
            logger.debug(f"[collab_manager] 忽略: {_e}")

        return []

    # ── 本地工具执行 ──

    def execute_tool_calls(self, tool_calls: list[dict], conversation_id: str = "") -> list[dict]:
        """
        在本地执行工具调用。
        Returns: list[dict] 每个 dict 包含 tool/status/result
        """
        results = []
        for call in tool_calls[:3]:  # 最多执行3个
            tool_name = call.get('tool', '')
            action = call.get('action', '')
            result = self._execute_single_tool(tool_name, action, conversation_id)
            results.append({
                "tool": tool_name,
                "action": action,
                "status": result.get('status', 'unknown'),
                "result": result.get('result', ''),
            })
        return results

    def _execute_single_tool(self, tool_name: str, action: str, conversation_id: str) -> dict:
        """执行单个工具操作"""
        try:
            from src.core.skills_manager import get_skills_manager
            sm = get_skills_manager()
            # 先尝试 id 匹配
            all_skills = sm.list_skills()
            matched = None
            for s in all_skills:
                sid = s.get('id', '')
                sname = s.get('name', '')
                if sid.lower() == tool_name.lower() or sname.lower() == tool_name.lower():
                    matched = s
                    break
            if not matched:
                return {"status": "not_found", "result": f"工具 '{tool_name}' 不存在"}

            # 找到技能：通过本地通用模型执行
            return self._invoke_skill_via_ollama(matched, action, conversation_id)
        except Exception as e:
            logger.error(f"[Collab] 工具执行失败 ({tool_name}): {e}")
            return {"status": "error", "result": str(e)}

    def _invoke_skill_via_ollama(self, skill: dict, action: str, conversation_id: str) -> dict:
        """通过本地通用模型执行技能操作"""
        skill_name = skill.get('name', skill.get('id', 'unknown'))
        skill_prompt = skill.get('prompt', '')
        
        # 构建简短的执行 prompt
        exec_prompt = f"请执行以下操作：{action}\n\n参考技能规则：\n{skill_prompt[:500]}"
        
        try:
            from src.core.model_manager import get_model_manager
            mm = get_model_manager()
            response = mm.chat(
                [
                    {"role": "system", "content": f"你是 Harven AI 的 {skill_name} 工具。请简洁执行用户请求。"},
                    {"role": "user", "content": exec_prompt},
                ],
                stream=False,
                temperature=0.3,
            )
            if isinstance(response, str):
                return {"status": "success", "result": response[:1000]}
            return {"status": "success", "result": str(response)[:1000]}
        except Exception as e:
            logger.warning(f"[Collab] Ollama 技能调用失败: {e}")
            return {"status": "error", "result": f"技能 {skill_name} 调用失败: {e}"}


# ── 全局单例 ──

_collab_manager: CollabManager | None = None


def get_collab_manager(config=None) -> CollabManager:
    """获取协作管理器单例"""
    global _collab_manager
    if _collab_manager is None:
        _collab_manager = CollabManager(config)
    elif config is not None:
        # 更新配置（兼容 collab_enabled 和 collab_mode）
        _collab_manager.update(
            mode=config.get('collab_mode'),
            enabled=config.get('collab_enabled'),
            foreign_model=config.get('collab_foreign_model'),
            privacy_level=config.get('collab_privacy_level'),
        )
    return _collab_manager


def reset_collab_manager():
    """重置单例（测试用）"""
    global _collab_manager
    _collab_manager = None
