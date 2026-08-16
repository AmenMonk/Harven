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
    (r"(审查|review(?![\da-zA-Z_])|检查代码|代码检查|code\s*review|lint(?![\da-zA-Z_])|bug(?![\da-zA-Z_])|debug(?![\da-zA-Z_])|排查|cr(?![\da-zA-Z_]))", "code_reviewer"),
    (r"(写代码|帮我写|生成代码|实现.*功能|写个|写一个|编码|coding(?![\da-zA-Z_])|python(?![\da-zA-Z_])|函数|class(?![\da-zA-Z_]))", "coder"),
    (r"(翻译|translate(?![\da-zA-Z_])|译|英文|中文|日语|法语)", "translator"),
    # 分析类
    (r"(分析数据|分析文件|csv(?![\da-zA-Z_])|excel(?![\da-zA-Z_])|表格分析|统计|数据分析|报表)", "data_analyst"),
    (r"(研究|调研|research(?![\da-zA-Z_])|论文|报告|对比|竞品分析)", "researcher"),
    # 批判类（优先级 > planner，防止"漏洞"被"方案"抢走）
    (r"(批判|找问题|挑刺|反对意见|漏洞分析|漏洞|批评)", "critic"),
    # 规划类
    (r"(计划|规划|方案|拆解|路线图|实现步骤)", "planner"),
    # 运维/系统类
    (r"(日志|log(?![\da-zA-Z_])|报错|error\s*trace|排查|debug\s*log|监控)", "ops_agent"),
    (r"(安装|install(?![\da-zA-Z_])|pip(?![\da-zA-Z_])|环境|配置环境|setup(?![\da-zA-Z_])|依赖)", "ops_agent"),
    # 文件浏览器（★ 2026-07-13: 缩小匹配，避免正常问题被误判）
    (r"(列出.*文件|list.*file|有什么文件|有哪些文件|看看.*目录|查看.*目录)", "file_explorer"),
    # 电脑操作（靠后，宽泛匹配做兜底）
    (r"(打开.*电脑|控制电脑|操作电脑|截图|截屏|打开.*盘|打开.*目录|打开.*文件夹|列出.*盘|查看.*盘|整理桌面|整理文件|输入文字|窗口|最大化|最小化)", "computer_use"),
    # 教学类（★ 2026-07-13: 缩小匹配范围，避免正常问题被误判为教学）
    (r"(入门|教程|指南|教我| teach)", "teacher"),
]

# ─── 预置 Agent ───
DEFAULT_AGENTS = {
    "code_reviewer": {
        "name": "代码审查员",
        "role": "code_reviewer",
        "model_pref": "local",  # 本地模型，代码不出电脑
        "skills": ["code_review_auto"],
        "system_prompt": """你是哈文AI的专项子Agent——代码审查员。
你只做一件事：审查代码。

规则：
1. 只输出审查结果，不闲聊、不解释背景、不提供非代码建议
2. 输出格式：问题类型 | 严重程度 | 行号 | 问题描述 | 修复建议
3. 严重程度：🔴严重 / 🟡警告 / 🔵建议
4. 如果你看到的不像代码，回复："[SKIP] 非代码输入，请交给其他Agent"

你的能力边界：审查代码，不写代码，不运行代码，不改代码。""",
        "triggers": ["审查代码", "review", "代码检查", "check code", "查bug", "bug"],
        "input_schema": {"type": "code", "language": "str"},
        "output_schema": {"type": "review_report"},
    },
    "data_analyst": {
        "name": "数据分析师",
        "role": "data_analyst",
        "model_pref": "external",  # 分析用外脑，推理强
        "skills": ["data_analysis_auto"],
        "system_prompt": """你是哈文AI的专项子Agent——数据分析师。
你只做一件事：分析数据。

规则：
1. 输出：先结论，后详情
2. 格式：用表格呈现关键数据，用文字解释趋势
3. 不要写代码来实现（那是coder的事），你负责解读数据含义
4. 不确定的数据标注[待验证]

你的能力边界：分析数据，不处理数据（清理/转换/入库由其他Agent负责）。""",
        "triggers": ["分析数据", "数据分析", "报表", "统计"],
        "input_schema": {"type": "data", "format": "str"},
        "output_schema": {"type": "analysis_report"},
    },
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
    "translator": {
        "name": "翻译官",
        "role": "translator",
        "model_pref": "external",
        "skills": [],
        "system_prompt": """你是哈文AI的专项子Agent——翻译官。
你只做一件事：翻译文本。

规则：
1. 输出：只输出译文，不解释不备注
2. 自动检测源语言
3. 保持原文语气、格式、分段
4. 非翻译请求回复："[SKIP] 非翻译任务"

你的能力边界：翻译。不润色、不总结、不评价原文。""",
    },
    "teacher": {
        "name": "教师",
        "role": "teacher",
        "model_pref": "local",
        "skills": [],
        "system_prompt": """你是哈文AI的专项子Agent——教师。
你只做一件事：教人学会东西。

规则：
1. 用类比和例子解释复杂概念
2. 分步骤讲解
3. 不会就说不会，不编造知识
4. 正常回答问题，不要说教，不要问"明白了吗"

你的能力边界：教学解释。不写代码、不分析数据、不操作文件。""",
    },
    "file_explorer": {
        "name": "文件浏览器",
        "role": "file_explorer",
        "model_pref": "local",
        "skills": [],
        "system_prompt": """你是哈文AI的专项子Agent——文件浏览器。
你只做一件事：列出和读取本地文件。

规则：
1. 用户让你"打开某盘""看看有什么"→ 生成Python代码用os.listdir()列出
2. 生成 ```python 代码块，系统会自动执行
3. 输出文件列表后告诉用户可以进一步操作

你的能力边界：列文件、读文件。不修改、不删除、不分析。""",
    },
    "researcher": {
        "name": "研究员",
        "role": "researcher",
        "model_pref": "external",
        "skills": [],
        "system_prompt": """你是哈文AI的专项子Agent——研究员。
你只做一件事：对主题进行深度调研分析。

规则：
1. 输出：背景→现状→分析→建议 四段式
2. 不确定的信息标注[待验证]
3. 非研究类请求回复："[SKIP] 非研究任务"

你的能力边界：研究调研。不执行、不写代码、不处理数据。""",
        "triggers": ["研究", "调研", "论文", "报告", "竞品分析", "对比"],
    },
    "critic": {
        "name": "评论家",
        "role": "critic",
        "model_pref": "external",
        "skills": [],
        "system_prompt": """你是哈文AI的专项子Agent——评论家。
你只做一件事：找出方案/代码/观点的漏洞和问题。

规则：
1. 质疑一切假设，找出逻辑漏洞
2. 考虑反面论点和边缘情况
3. 给出有价值的反面意见，不人身攻击
4. 非批判类请求回复："[SKIP] 非批判任务"

你的能力边界：批判性思考。不说好话，只找问题。""",
        "triggers": ["批判", "找问题", "挑刺", "反对意见", "漏洞", "批评"],
    },
    "planner": {
        "name": "规划师",
        "role": "planner",
        "model_pref": "external",
        "skills": [],
        "system_prompt": """你是哈文AI的专项子Agent——规划师。
你只做一件事：把模糊目标拆成可执行计划。

规则：
1. 输出：目标→约束→步骤→里程碑→风险评估
2. 步骤必须可执行、可衡量
3. 非规划类请求回复："[SKIP] 非规划任务"

你的能力边界：制定计划。不执行计划。""",
        "triggers": ["计划", "规划", "方案", "拆解", "实现步骤", "路线图"],
    },
    "ops_agent": {
        "name": "运维助手",
        "role": "ops",
        "model_pref": "local",
        "skills": [],
        "system_prompt": """你是哈文AI的专项子Agent——运维助手。
你只做一件事：处理系统运维任务。

规则：
1. 列出文件、读取日志、检查进程 → 生成 ```python 代码块（系统自动执行）
2. 不要只给文字说明让主人手动操作
3. 非运维类请求回复："[SKIP] 非运维任务"

你的能力边界：文件操作、日志查看、进程检查。不部署、不修改系统配置。""",
        "triggers": ["日志", "报错", "排查", "进程", "服务状态"],
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
        # 确保预置 Agent 始终存在
        for agent_id, agent_def in DEFAULT_AGENTS.items():
            if agent_id not in self._agents:
                self._agents[agent_id] = dict(agent_def)
                self._agents[agent_id]["status"] = "idle"
                self._agents[agent_id]["registered_at"] = time.time()
                self._agents[agent_id]["call_count"] = 0
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
    
    def discover(self, user_message: str) -> Optional[str]:
        """意图发现：根据用户消息，找到最匹配的 Agent"""
        msg_lower = user_message.lower()
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
        """列出所有已注册 Agent"""
        return [
            {
                "id": aid,
                "name": a["name"],
                "role": a["role"],
                "status": a["status"],
                "model_pref": a.get("model_pref", "local"),
                "call_count": a.get("call_count", 0),
                "triggers": a.get("triggers", []),
            }
            for aid, a in self._agents.items()
        ]
    
    def get_agent(self, agent_id: str) -> Optional[dict]:
        return self._agents.get(agent_id)


# 单例
_REGISTRY = None

def get_registry() -> AgentRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AgentRegistry()
    return _REGISTRY
