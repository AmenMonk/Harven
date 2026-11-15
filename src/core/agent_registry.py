# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — Agent 注册中心（Agent Registry）
架构定位：Harness 平台层的"总经理办公室"
职责：只做 注册 + 发现 + 移交，不自己干活

通信协议（JSON Schema）：
┌─────────────────────────────────────────────┐
│  register(name, role, model, skills, prompt) │
│  discover(intent) → agent_id                 │
│  handoff(agent_id, context) → result         │
│  list_agents() → [{id, name, role, status}]  │
└─────────────────────────────────────────────┘

每个 Agent 拥有：
- 独立 system_prompt（不进全局 morality.py）
- 专属 Skills 库（互不干扰）
- 可选模型偏好（local/external/collab）
"""

import json
import time
import logging
import re
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent.parent / "data" / "sqlite" / "agent_registry.json"

# ─── 三层管理层级（★ 2026-09-13 经多轮讨论定案）───
# 🔒 核心层：不可删不可停（哈文操作电脑/系统维护/写代码的根基）
CORE_AGENTS = {"computer_use", "system_ops", "coder"}
# 🗑️ 僵尸层：代码里已删除但旧数据残留（2026-07-03 音乐改走垂直面板），加载时直接清出
# ★ 2026-09-13 已定版#8：8 个非核心助手退役（以后工具能力走面板模块，不占 Agent 团队）
ZOMBIE_AGENTS = {"music_generator", "ace_step", "music_suggest",
                 "code_reviewer", "data_analyst", "translator", "researcher",
                 "critic", "planner", "ops_agent", "file_explorer"}

# ─── 意图 → Agent 路由表（正则匹配）───
# ⚠️ 禁止使用 \b（Python re 中 \b 在中文字符间不匹配，中文是 \w）
# 英文触发器末尾用 (?![\da-zA-Z_]) — 允许中文跟随，但防止英文子串误匹配
# （如 "bug" 不应匹配 "debugging"，但 "pip包" 应匹配）
INTENT_ROUTES = [
    # ═══ 音乐生成：★ 2026-07-03 已删除，改走垂直面板参数提取 ═══
    # 旧音乐 Agent（ace_step/music_suggest/music_generator）已禁用并删除
    # 音乐生成现在走 engine.py Step 2.4 → vertical_api.py 垂直预填
    # 🛠️ 系统管家：装/卸软件、磁盘清理、网络修复等（2026-06-26 新增）
    (r"(装.*软件|安装.*软件|帮我装|卸载.*软件|删除.*软件|C盘.*满|清理垃圾|磁盘清理|空间.*不够|网连不上|网络断了|网络修复|网速慢|没有网|连不上网|我电脑.*配置|什么配置|显卡.*是|内存.*多大|整理桌面|整理下载|整理文件|桌面.*乱|截图|截屏|占CPU|占内存|什么程序.*卡|换壁纸|换背景|桌面背景|帮我读.*图|读截图|OCR|识别.*文字|系统管家)", "system_ops"),
    (r"(写代码|帮我写|生成代码|实现.*功能|写个|写一个|编码|coding(?![\da-zA-Z_])|python(?![\da-zA-Z_])|函数|class(?![\da-zA-Z_]))", "coder"),
    # 分析类
    # 批判类（优先级 > planner，防止"漏洞"被"方案"抢走）
    # 规划类
    # 运维/系统类
    # 文件浏览器（★ 2026-07-13: 缩小匹配，避免正常问题被误判）
    # 电脑操作（靠后，宽泛匹配做兜底）
    (r"(打开.*电脑|控制电脑|操作电脑|截图|截屏|打开.*盘|打开.*目录|打开.*文件夹|列出.*盘|查看.*盘|整理桌面|整理文件|输入文字|窗口|最大化|最小化)", "computer_use"),
    # 教学类（★ 2026-07-13 缩小匹配；★ 2026-09-13 重做后补孩子学习触发词，避免“讲”字单字误判）
    (r"(入门|教程|指南|教我| teach|讲题|这道题|这道数学|怎么解|解方程|辅导作业|给我讲讲)", "teacher"),
]

# ─── 预置 Agent ───
DEFAULT_AGENTS = {
    
    "coder": {
        "name": "程序员",
        "role": "coder",
        "model_pref": "collab",  # 用协作模式：外脑设计+本地执行
        "skills": ["python_expert"],
        "system_prompt": """你是哈文AI的专项子Agent——程序员。
你只做一件事：写代码。

规则：
1. 输出：先给思路（2-3句），再给完整代码
2. 代码必须有注释、错误处理、边界条件
3. 非代码请求回复："[SKIP] 非编程任务，请交给其他Agent"

你的能力边界：写代码、加注释、生成测试。不审查代码（那是code_reviewer的事），不运行代码。""",
        "triggers": ["写代码", "帮我写", "实现", "写个", "函数", "class"],
        "input_schema": {"type": "requirement", "language": "str"},
    
        "output_schema": {"type": "code"},
    },
    "teacher": {
        "name": "学习辅导",
        "role": "teacher",
        "model_pref": "local",
        "skills": [],
        # ★ 2026-09-13 重做：从泛用“教师”升级为“孩子学习辅导”专项（已定案，家庭孩子场景真需求，历史调用全队第二）
        "system_prompt": """你是哈文AI的专项子Agent——学习辅导老师，服务对象是中小学生。
你只做一件事：帮孩子学会知识、解开题目。

规则：
1. **不直接报答案**：先引导孩子说思路，分小步提示，让孩子自己走完最后一步
2. 用大白话和生活例子解释（比如分数像切蛋糕，别用专业术语堆砌）
3. 数学题一步步列式，每步说明“为什么这么做”
4. 语文（古诗/成语/阅读）、英语（单词/语法）、科学都可以教
5. 语气亲切鼓励：“差一步就出来了”“这个想法很棒”，从不批评嘲笑
6. 孩子明显只是想玩/聊无关话题时，温和拉回学习，不训斥
7. 超出中小学范围就坦白说“这个等老师讲到再学更好”，不编造
8. 涉及危险、不良内容一律拒绝并提醒找家长

你的能力边界：中小学课业辅导与讲解。不写代码、不操作电脑、不代替孩子完成作业（只引导）。""",
        "triggers": ["讲题", "这道题", "怎么解", "辅导作业", "教我", "讲解", "古诗", "成语", "单词", "造句", "解方程"],
    },
    
    "computer_use": {
        "name": "电脑操作",
        "role": "system",
        "model_pref": "script",
        "script_name": "computer_use",
        "skills": [],
        "system_prompt": "脚本型Agent（computer_use.py），操作本地电脑。",
        "triggers": ["操作电脑", "控制电脑", "操纵电脑", "打开*盘", "打开磁盘", "截图", "截屏", "打开应用", "启动浏览器", "整理桌面", "整理文件", "查看内存", "输入文字", "窗口", "最大化", "最小化", "打开文件夹", "打开目录", "查看*盘", "列出*盘", "打开D盘", "打开C盘", "打开E盘", "打开F盘"],
    },
    # ★ 2026-07-03: 以下3个音乐 Agent 已删除（music_generator / ace_step / music_suggest）
    # 音乐生成改走 engine.py Step 2.4 → vertical_api.py 垂直预填模式
    "system_ops": {
        "name": "🛠️ 系统管家",
        "role": "ops",
        "model_pref": "script",
        "script_name": "system_ops",
        "skills": ["system_ops"],
        "system_prompt": "脚本型Agent（system_ops.py），系统管家：装/卸软件、磁盘清理、网络修复、查配置、文件整理、截屏、查进程、改壁纸、OCR。",
        "triggers": ["装软件", "卸软件", "清理磁盘", "网络修复", "查配置", "整理桌面", "截图", "查进程", "换壁纸"],
    },
}


class AgentRegistry:
    """Agent 注册中心 — 哈文的"总经理办公室"单例"""
    
    def __init__(self):
        self._agents: Dict[str, dict] = {}
        self._load()
    
    def _load(self):
        if REGISTRY_PATH.exists():
            try:
                data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                self._agents = data.get("agents", {})
            except Exception:
                self._agents = {}
        # ★ 2026-09-13: 清出僵尸 Agent（代码已删除但旧数据残留，见 ZOMBIE_AGENTS 注释）
        for zombie in ZOMBIE_AGENTS:
            if zombie in self._agents:
                logger.info(f"[Registry] 清出僵尸 Agent: {zombie}（音乐已走垂直面板，此为旧数据残留）")
                del self._agents[zombie]
        # 确保预置 Agent 始终存在
        for agent_id, agent_def in DEFAULT_AGENTS.items():
            if agent_id not in self._agents:
                self._agents[agent_id] = dict(agent_def)
                self._agents[agent_id]["status"] = "idle"
                self._agents[agent_id]["registered_at"] = time.time()
                self._agents[agent_id]["call_count"] = 0
        # ★ 2026-09-13: 内置 Agent 定义同步——json 里的旧定义（名字/提示词/触发词）刷新为
        #   DEFAULT_AGENTS 最新版，只保留运行时状态（status/call_count/user_toggled/_pref_backup/model_pref 停用态）
        for aid, a in self._agents.items():
            _def = DEFAULT_AGENTS.get(aid)
            if not _def:
                continue
            for k in ("name", "role", "system_prompt", "triggers", "skills", "model_pref_default"):
                if k in _def:
                    a[k] = _def[k] if not isinstance(_def[k], list) else list(_def[k])
            if "model_pref" in _def and a.get("model_pref") != "disabled":
                a["model_pref"] = _def["model_pref"]
            for k in ("script_name", "default_disabled"):
                if k in _def:
                    a[k] = _def[k]
        # ★ 2026-09-13: default_disabled 的一次性落盘（用户手动开过就不再动）
        for aid, a in self._agents.items():
            _def = DEFAULT_AGENTS.get(aid) or {}
            if _def.get("default_disabled") and a.get("model_pref") != "disabled" and not a.get("user_toggled"):
                a["_pref_backup"] = a.get("model_pref", "local")
                a["model_pref"] = "disabled"
                logger.info(f"[Registry] {aid} 默认停用（与电脑操作触发词重复，可随时开启）")
        self._save()
        logger.info(f"[Registry] 已加载 {len(self._agents)} 个 Agent")
    
    def _save(self):
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(
            json.dumps({"agents": self._agents, "updated_at": time.time()}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    # ════════════ API ════════════
    
    def register(self, agent_id: str, name: str, role: str, system_prompt: str,
                 model_pref: str = "local", skills: List[str] = None,
                 triggers: List[str] = None) -> bool:
        """注册一个新 Agent"""
        if agent_id in self._agents and not self._agents[agent_id].get("custom", False):
            return False  # 不允许覆盖内置 Agent
        
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "role": role,
            "system_prompt": system_prompt,
            "model_pref": model_pref,
            "skills": skills or [],
            "triggers": triggers or [],
            "status": "idle",
            "registered_at": time.time(),
            "call_count": 0,
            "custom": True,
        }
        self._save()
        logger.info(f"[Registry] 注册新 Agent: {agent_id} ({name})")
        return True
    
    # ★ 2026-08-25 P4: 讨论类排除——用户在"问问题/诊断"而非"下命令"时，不触发执行类 Agent
    # 例："帮我检测一下为什么本地模型用不了"是讨论，不该触发模型安装/操作类面板
    _DISCUSSION_MARKERS = (
        '为什么', '怎么了', '怎么回事', '什么情况', '检测一下', '检查一下为什么',
        '诊断', '排查一下', '不好用', '用不了', '报错', '出错', '失败了', '什么问题',
        '是什么', '是外脑还是', '还是本地', '还是', '帮我分析', '什么原因',
    )

    def discover(self, user_message: str) -> Optional[str]:
        """意图发现：根据用户消息，找到最匹配的 Agent"""
        msg_lower = user_message.lower()
        # P4: 讨论类消息（疑问/诊断句式）不触发执行类 Agent，交给默认对话流程回答
        if any(m in user_message for m in self._DISCUSSION_MARKERS) and len(user_message) > 8:
            logger.info(f"[Registry] P4 讨论类消息跳过 Agent 路由: '{user_message[:40]}'")
            return None
        for pattern, agent_id in INTENT_ROUTES:
            if re.search(pattern, msg_lower, re.IGNORECASE):
                if agent_id in self._agents:
                    # ★ 2026-07-03: 禁用的 agent 不路由，交给垂直面板参数提取流程
                    agent = self._agents[agent_id]
                    if agent.get("model_pref") == "disabled":
                        logger.info(f"[Registry] Agent '{agent_id}' 已禁用，跳过（走垂直面板参数提取）")
                        continue  # 继续匹配下一个 INTENT_ROUTES 模式
                    logger.info(f"[Registry] 意图匹配: '{user_message[:40]}...' → {agent_id}")
                    return agent_id
        
        # 无匹配 → 不路由，交给默认对话流程
        logger.debug(f"[Registry] 无匹配意图，走默认流程")
        return None
    
    def handoff(self, agent_id: str, user_message: str, 
                model_func, conversation_history: list = None) -> dict:
        """任务移交：根据 agent 类型执行（script→执行脚本，否则→LLM 生成）"""
        agent = self._agents.get(agent_id)
        if not agent:
            return {"success": False, "error": f"Agent '{agent_id}' 不存在"}
        
        # ★ 2026-07-03: 禁用的 agent 拒绝执行（防御性 guard）
        if agent.get("model_pref") == "disabled":
            logger.info(f"[Registry] Agent '{agent_id}' 已禁用，handoff 拒绝执行")
            return {"success": False, "disabled": True, "reason": "Agent 已禁用，请走垂直面板参数提取"}
        
        agent["status"] = "busy"
        agent["call_count"] = agent.get("call_count", 0) + 1
        self._save()
        
        try:
            # ★ 脚本型 Agent：直接执行脚本，不调 LLM
            if agent.get("model_pref") == "script" and agent.get("script_name"):
                from src.core.agent_runner import run_script_agent
                result = run_script_agent(agent["script_name"], user_message)
                if result.get("success"):
                    agent["status"] = "idle"
                    self._save()
                    return {
                        "success": True,
                        "agent_id": agent_id,
                        "agent_name": agent["name"],
                        "result": result.get("output", ""),
                        "file": result.get("file"),
                        "model_pref": "script",
                    }
                else:
                    agent["status"] = "idle"
                    self._save()
                    return {"success": False, "error": result.get("error", "脚本执行失败")}
            
            # LLM 型 Agent：用 system_prompt + LLM 生成
            messages = [
                {"role": "system", "content": agent["system_prompt"]},
            ]
            if conversation_history:
                messages.extend(conversation_history[-4:])
            messages.append({"role": "user", "content": user_message})
            
            result = model_func(messages, temperature=0.3)
            
            if isinstance(result, str) and "[SKIP]" in result:
                logger.info(f"[Registry] Agent {agent_id} 拒绝处理，回落默认")
                return {"success": False, "skipped": True, "reason": result}
            
            agent["status"] = "idle"
            self._save()
            
            return {
                "success": True,
                "agent_id": agent_id,
                "agent_name": agent["name"],
                "result": result,
                "model_pref": agent.get("model_pref", "local"),
            }
        except Exception as e:
            agent["status"] = "idle"
            self._save()
            return {"success": False, "error": str(e)}
    
    def list_agents(self) -> List[dict]:
        """列出所有已注册 Agent（★ 2026-09-13: 补 core/enabled/custom/builtin 字段，修复前端全当内置的 bug）"""
        return [
            {
                "id": aid,
                "name": a["name"],
                "role": a["role"],
                "status": a["status"],
                "model_pref": a.get("model_pref", "local"),
                "call_count": a.get("call_count", 0),
                "triggers": a.get("triggers", []),
                "core": aid in CORE_AGENTS,
                "builtin": aid in DEFAULT_AGENTS,
                "enabled": a.get("model_pref") != "disabled",
                "custom": bool(a.get("custom", False)),
            }
            for aid, a in self._agents.items()
        ]

    def set_enabled(self, agent_id: str, enabled: bool) -> dict:
        """★ 2026-09-13: 停用/启用开关 — 核心层拒绝，非核心可随时切换（停用只改路由与执行，数据不丢）"""
        a = self._agents.get(agent_id)
        if not a:
            return {"success": False, "error": "Agent 不存在"}
        if agent_id in CORE_AGENTS:
            return {"success": False, "error": "核心助手不可停用（电脑操作/系统管家/程序员是哈文根基）"}
        if enabled:
            if a.get("model_pref") == "disabled":
                a["model_pref"] = a.get("_pref_backup", "local")
            a["user_toggled"] = True
        else:
            if a.get("model_pref") != "disabled":
                a["_pref_backup"] = a.get("model_pref", "local")
                a["model_pref"] = "disabled"
            a["user_toggled"] = True
        self._save()
        logger.info(f"[Registry] Agent {agent_id} {'启用' if enabled else '停用'}")
        return {"success": True, "enabled": enabled}
    
    def get_agent(self, agent_id: str) -> Optional[dict]:
        return self._agents.get(agent_id)


# 单例
_REGISTRY = None

def get_registry() -> AgentRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AgentRegistry()
    return _REGISTRY
