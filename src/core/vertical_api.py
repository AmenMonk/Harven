# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 垂直模型统一转换接口
Phase 1: 统一 API + 6个内置适配器
Phase 2: 动态适配器注册 (importlib 热加载)
Phase 3: 自动分析模型文档 → 生成适配器代码
Phase 4: 动态画板模板生成

API:
  GET  /api/vertical/models                 — 获取全部垂直模型列表(内置+动态)
  GET  /api/vertical/layout/<id>            — 获取模型的画板布局 JSON
  POST /api/vertical/execute                — 执行垂直模型
  POST /api/vertical/register               — 注册动态适配器 (Phase 2)
  GET  /api/vertical/adapters               — 列出已注册的动态适配器
  DELETE /api/vertical/adapters/<id>        — 删除动态适配器
  POST /api/vertical/adapters/<id>/reload   — 热重载适配器代码
  POST /api/vertical/auto-generate          — 自动分析文档生成适配器 (Phase 3)
  POST /api/vertical/generate-canvas        — 生成画板模板 (Phase 4)
  GET  /api/vertical/canvas-types           — 获取所有可用画板类型
"""

import json
import os
import sys
import types
import importlib
import importlib.util
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint('vertical', __name__, url_prefix='/api/vertical')

# 适配器存放目录
_ADAPTERS_DIR = Path(__file__).parent.parent.parent / "data" / "vertical_adapters"
_LAYOUTS_DIR = Path(__file__).parent.parent.parent / "data" / "ui_layouts"

# ═══════════════════════════════════════════════════════════════
# 6 个内置垂直模型定义（元数据驱动）
# ═══════════════════════════════════════════════════════════════

_BUILTIN_MODELS = [
    {
        "id": "stable-diffusion",
        "name": "Stable Diffusion",
        "icon": "🎨",
        "license": "OpenRAIL-M",
        "commercial_use": False,
        "license_warning": "⚠️ 本模型为 OpenRAIL-M 许可，商用受限，请查阅许可条款",
        "type": "image_generation",
        "canvas_type": "image_generation",
        "description": "AI 图片生成 — 输入文字描述，生成图片",
        "engine": "tool_executor",
        "executor_method": "run_image_gen",
        "task_keywords": ["画", "生成图片", "画图", "绘制", "插画", "海报", "logo", "图片", "image", "draw", "paint"],
        # ★ 2026-07-04: 能力边界 + 专业建议（参谋模式）
        "capability": {
            "can_do": ["生成静态图片", "支持写实/动漫/油画/水彩等风格", "分辨率 256-1024px"],
            "cannot_do": ["动态视频/动画", "精确文字渲染（文字会模糊）", "超高分辨率(>1024px)", "3D模型"],
            "max_limits": "单张图片，步数 10-50，分辨率最大 1024x1024",
            "advisor_tips": "描述越具体效果越好。建议用英文描述（如 'a serene mountain landscape at sunset'）。加 negative_prompt 排除不想要的元素。步数 20-30 性价比最高。",
        },
        "params": {
            "prompt": {"type": "textarea", "label": "画面描述", "default": "", "placeholder": "描述你想要的画面...", "required": True},
            "negative_prompt": {"type": "textarea", "label": "排除内容", "default": "", "placeholder": "不希望出现的元素，如: blurry, low quality"},
            "style": {"type": "select", "label": "风格", "default": "realistic", "options": ["realistic 写实", "anime 动漫", "oil-painting 油画", "watercolor 水彩", "3d-render 3D渲染", "pixel-art 像素艺术", "cyberpunk 赛博朋克", "chinese-style 中国风"]},
            "width": {"type": "range", "label": "宽度", "default": 512, "min": 256, "max": 1024, "step": 64},
            "height": {"type": "range", "label": "高度", "default": 512, "min": 256, "max": 1024, "step": 64},
            "steps": {"type": "range", "label": "步数", "default": 20, "min": 10, "max": 50, "step": 1},
            "cfg_scale": {"type": "range", "label": "CFG 引导强度", "default": 7.5, "min": 1, "max": 20, "step": 0.5},
            "seed": {"type": "text", "label": "随机种子(可选)", "default": "-1", "placeholder": "-1=随机, 固定数字=复现"},
        }
    },
    {
        "id": "musicgen-small",
        "name": "MusicGen Small",
        "icon": "🎵",
        "license": "CC-BY-NC 4.0",
        "commercial_use": False,
        "license_warning": "⚠️ 本模型为 CC-BY-NC 4.0 许可，不可用于商业用途",
        "type": "music_generation",
        "canvas_type": "music_generation",
        "description": "AI 音乐生成 — 根据参数生成原创音乐",
        "engine": "tool_executor",
        "executor_method": "run_musicgen",
        "task_keywords": ["作曲", "音乐", "写曲", "生成音乐", "编曲", "曲子", "旋律", "music", "song", "compose",
                          # ★ 2026-07-03 扩展：覆盖用户常见表达（首歌/风格/乐器/场景）
                          "首歌", "一首歌", "写歌", "做歌", "做曲", "写一首", "生成歌", "唱首歌",
                          "乡村", "摇滚", "爵士", "流行乐", "古典", "电子乐", "布鲁斯", "民谣", "国风", "古风", "交响", "管弦", "史诗",
                          "钢琴曲", "吉他曲", "小提琴", "古筝", "二胡", "琵琶", "笛子", "贝斯", "萨克斯", "非洲鼓",
                          "BGM", "背景音乐", "配乐", "原声带",
                          "欢快", "悲伤", "忧伤", "舒缓", "激昂", "浪漫", "忧郁"],
        # ★ 2026-07-04: 能力边界 + 专业建议（参谋模式）
        "capability": {
            "can_do": ["生成纯伴奏音乐", "5-30秒短曲", "支持 blues/jazz/rock/classical/electronic/folk/country/pop/metal/中国传统/史诗 风格", "可调 BPM/情绪/乐器"],
            "cannot_do": ["人声演唱", "歌词生成", "长曲(>30秒)", "多乐段拼接", "精确旋律控制（只能描述风格让AI自由生成）"],
            "max_limits": "单次最长 30 秒，CPU 模式生成约 2-5 分钟",
            "advisor_tips": "忧伤→小调+慢板(60-80BPM)+钢琴/弦乐；欢快→大调+快板(120-140BPM)+吉他/合成器；史诗→大调+中板(90-110BPM)+管弦；浪漫→大调+慢板(60-80BPM)+钢琴/吉他。首次生成建议用 auto 让AI自由发挥。",
        },
        "params": {
            "style": {"type": "select", "label": "风格", "default": "auto", "options": ["auto 自动", "blues 布鲁斯", "jazz 爵士乐", "rock 摇滚", "classical 古典", "electronic 电子", "folk 民谣", "country 乡村", "pop 流行", "metal 金属", "chinese-traditional 中国传统", "epic 史诗"]},
            "mood": {"type": "select", "label": "情绪", "default": "auto", "options": ["auto 自动", "happy 欢快", "sad 悲伤", "calm 平静", "energetic 充满活力", "mysterious 神秘", "romantic 浪漫", "melancholic 忧郁"]},
            "instrument": {"type": "select", "label": "乐器", "default": "auto", "options": ["auto 自动", "piano 钢琴", "guitar 吉他", "violin 小提琴", "drums 架子鼓", "flute 长笛", "orchestra 管弦乐团", "synth 合成器"]},
            "duration": {"type": "range", "label": "时长(秒)", "default": 15, "min": 5, "max": 60, "step": 5},
            "bpm": {"type": "range", "label": "BPM 速度", "default": 120, "min": 60, "max": 200, "step": 10},
            "seed": {"type": "text", "label": "随机种子(可选)", "default": "-1", "placeholder": "-1=随机, 固定数字=复现"},
        }
    },
    {
        "id": "bark-tts",
        "name": "Bark TTS",
        "icon": "🗣️",
        "license": "MIT",
        "commercial_use": True,
        "license_warning": "",
        "type": "speech_synthesis",
        "canvas_type": "speech_synthesis",
        "description": "AI 语音合成 — 文本转语音",
        "engine": "tool_executor",
        "executor_method": "run_bark",
        "task_keywords": ["朗读", "语音", "配音", " tts", "说话", "合成语音", "朗读文本", "speech", "voice"],
        # ★ 2026-07-04: 能力边界 + 专业建议（参谋模式）
        "capability": {
            "can_do": ["中英文文本转语音", "多种音色选择", "支持中英混读"],
            "cannot_do": ["歌唱合成", "多说话人对话", "精确语调控制", "超长文本(>500字建议分段)"],
            "max_limits": "单次建议 <500 字，CPU 模式约 1-3 分钟",
            "advisor_tips": "中文用 zh_speaker 系列，英文用 en_speaker 系列。女声 0/3/7，男声 1/5。文本加标点符号能让停顿更自然。",
        },
        "params": {
            "text": {"type": "textarea", "label": "要朗读的文本", "default": "", "placeholder": "输入要合成的文字...", "required": True},
            "voice_preset": {"type": "select", "label": "音色", "default": "v2/zh_speaker_0", "options": ["v2/zh_speaker_0 中文女声", "v2/zh_speaker_1 中文男声", "v2/zh_speaker_3 中文女声2", "v2/zh_speaker_5 中文男声2", "v2/zh_speaker_7 中文女声3", "v2/en_speaker_0 英文女声", "v2/en_speaker_3 英文男声", "v2/en_speaker_6 英文男声2"]},
            "language": {"type": "select", "label": "语言", "default": "zh", "options": ["zh 中文", "en English", "ja 日本語", "ko 한국어"]},
            "speed": {"type": "range", "label": "语速", "default": 1.0, "min": 0.5, "max": 2.0, "step": 0.1},
        }
    },
    {
        "id": "code-generator",
        "name": "代码生成",
        "icon": "💻",
        "license": "MIT",
        "commercial_use": True,
        "license_warning": "",
        "type": "code_generation",
        "canvas_type": "code_generation",
        "description": "AI 代码生成 — 描述需求，生成代码",
        "engine": "model",
        "task_keywords": ["写代码", "生成代码", "编程", "代码", "函数", "脚本", "code", "function", "script"],
        # ★ 2026-07-04: 能力边界 + 专业建议（参谋模式）
        "capability": {
            "can_do": ["生成函数/类/脚本/API/UI组件/算法", "支持 13 种编程语言", "可指定框架"],
            "cannot_do": ["完整项目搭建", "数据库设计", "部署配置", "大型系统架构"],
            "max_limits": "单次生成建议 <200 行，复杂需求请分段描述",
            "advisor_tips": "描述越具体越好（输入/输出/边界条件）。Python 适合数据处理/AI/脚本；JavaScript 适合前端交互；SQL 要指明数据库类型。加框架名能让代码更规范。",
        },
        "params": {
            "description": {"type": "textarea", "label": "需求描述", "default": "", "placeholder": "描述你要生成的代码功能...", "required": True},
            "language": {"type": "select", "label": "编程语言", "default": "python", "options": ["python", "javascript", "typescript", "html", "css", "sql", "java", "go", "rust", "c++", "c#", "php", "shell"]},
            "framework": {"type": "text", "label": "框架(可选)", "default": "", "placeholder": "如 Flask, React, Vue..."},
            "task_type": {"type": "select", "label": "任务类型", "default": "function", "options": ["function 函数", "class 类", "script 脚本", "api API接口", "ui 界面组件", "algorithm 算法"]},
        }
    },
    {
        "id": "data-analysis",
        "name": "数据分析",
        "icon": "📊",
        "license": "MIT",
        "commercial_use": True,
        "license_warning": "",
        "type": "data_analysis",
        "canvas_type": "data_analysis",
        "description": "AI 数据分析 — 上传数据，自动分析并生成图表",
        "engine": "model",
        "task_keywords": ["分析数据", "数据分析", "统计", "图表", "可视化", "数据", "analysis", "chart", "statistics"],
        # ★ 2026-07-04: 能力边界 + 专业建议（参谋模式）
        "capability": {
            "can_do": ["统计分析", "图表生成", "趋势分析", "数据描述"],
            "cannot_do": ["实时数据库查询", "大数据(>10万行)", "机器学习建模", "预测性分析"],
            "max_limits": "单次建议 <1000 行数据",
            "advisor_tips": "柱状图适合对比，折线图适合趋势，饼图适合占比。先明确分析目标能让结果更精准。粘贴 CSV 时保留表头。",
        },
        "params": {
            "data_source": {"type": "textarea", "label": "数据描述/粘贴数据", "default": "", "placeholder": "粘贴 CSV/JSON 数据或描述数据来源...", "required": True},
            "chart_type": {"type": "select", "label": "图表类型", "default": "auto", "options": ["auto 自动", "bar 柱状图", "line 折线图", "pie 饼图", "scatter 散点图", "histogram 直方图", "heatmap 热力图"]},
            "analysis_goal": {"type": "text", "label": "分析目标", "default": "", "placeholder": "如: 找出销售额最高的月份"},
            "output_format": {"type": "select", "label": "输出格式", "default": "report", "options": ["report 文字报告", "chart 图表", "table 表格", "both 图表+报告"]},
        }
    },
    {
        "id": "translation",
        "name": "文档翻译",
        "icon": "🌐",
        "license": "MIT",
        "commercial_use": True,
        "license_warning": "",
        "type": "translation",
        "canvas_type": "translation",
        "description": "AI 文档翻译 — 多语言互译",
        "engine": "model",
        "task_keywords": ["翻译", "translate", "译成", "翻译成", "互译"],
        # ★ 2026-07-04: 能力边界 + 专业建议（参谋模式）
        "capability": {
            "can_do": ["多语言互译", "保持原文格式", "支持 8 种语言"],
            "cannot_do": ["专业术语校对（医学/法律）", "诗歌押韵翻译", "方言翻译"],
            "max_limits": "单次建议 <3000 字",
            "advisor_tips": "正式文档用 formal 风格，日常对话用 casual。文言文用 literary。翻译后建议人工校对专业术语。",
        },
        "params": {
            "text": {"type": "textarea", "label": "要翻译的文本", "default": "", "placeholder": "输入要翻译的内容...", "required": True},
            "source_lang": {"type": "select", "label": "源语言", "default": "auto", "options": ["auto 自动检测", "中文", "English", "日本語", "한국어", "Français", "Deutsch", "Español", "Русский"]},
            "target_lang": {"type": "select", "label": "目标语言", "default": "English", "options": ["中文", "English", "日本語", "한국어", "Français", "Deutsch", "Español", "Русский", "العربية"]},
            "style": {"type": "select", "label": "翻译风格", "default": "natural", "options": ["natural 自然", "literal 直译", "formal 正式", "casual 口语化", "literary 文学"]},
        }
    },
    {
        "id": "huatuo-medical",
        "name": "华佗医疗模型",
        "icon": "🏥",
        "license": "MIT",
        "commercial_use": True,
        "license_warning": "",
        "type": "medical_qa",
        "canvas_type": "chat",
        "description": "医疗问答 — 专业的医疗健康咨询",
        "engine": "ollama",
        "ollama_model": "huatuo",
        "executor_method": "run_huatuo",
        "task_keywords": ["医疗", "健康", "生病", "感冒", "吃药", "症状", "诊断",
                          "发烧", "咳嗽", "头痛", "胃痛", "过敏", "血压", "血糖",
                          "huatuo", "华佗", "医生", "看病", "医院", "药品", "处方"],
        "capability": {
            "can_do": ["医疗健康咨询", "症状分析", "用药建议参考", "健康知识问答"],
            "cannot_do": ["开具处方", "替代医生诊断", "紧急医疗处理", "手术建议"],
            "max_limits": "仅供参考，不能替代专业医生",
            "advisor_tips": "描述症状时尽量详细（持续时间/严重程度/伴随症状），华佗会给出参考建议。紧急情况请立即就医。"
        },
        "params": {
            "question": {
                "type": "textarea",
                "label": "问题描述",
                "default": "",
                "placeholder": "详细描述你的症状或健康问题...",
                "required": True
            }
        }
    }
]


# ═══════════════════════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════════════════════
# 注意: list_vertical_models 定义在文件末尾，支持动态模型


@bp.route('/layout/<model_id>', methods=['GET'])
def get_model_layout(model_id):
    """获取模型的画板布局 JSON"""
    # 先从内置模型找
    model = next((m for m in _BUILTIN_MODELS if m['id'] == model_id), None)
    # 再从动态模型找
    if not model:
        model = _dynamic_models.get(model_id)
    if not model:
        return jsonify({"error": "模型不存在"}), 404

    # 默认布局（未来可从 data/ui_layouts/ 读取用户自定义布局）
    layout = {
        "model_id": model_id,
        "canvas_type": model.get("canvas_type", "generic"),
        "sections": [
            {"type": "form", "position": "top"},
            {"type": "preview", "position": "bottom", "height": "auto"},
        ]
    }

    # 检查用户自定义布局
    layout_path = Path(__file__).parent.parent.parent / "data" / "ui_layouts" / f"{model_id}.json"
    if layout_path.exists():
        try:
            custom = json.loads(layout_path.read_text(encoding="utf-8"))
            layout = custom
        except Exception as _e: logger.debug(f"[vertical_api] 忽略: {_e}")

    return jsonify(layout)


@bp.route('/execute', methods=['POST'])
def execute_vertical_model():
    """
    统一执行入口 — 根据 model_id 路由到对应适配器
    请求体: {model_id, params, action}
    支持三种引擎: tool_executor / model / dynamic
    
    ★ 2026-07-04: tool_executor 类型改用后台线程执行，避免 WinError 10060 超时
    """
    data = request.get_json(silent=True) or {}
    model_id = data.get('model_id', '')
    params = data.get('params', {})
    action = data.get('action', 'generate')

    # 先从内置模型找
    model = next((m for m in _BUILTIN_MODELS if m['id'] == model_id), None)
    if not model:
        # 再从动态注册的模型找
        model = _dynamic_models.get(model_id)
    if not model:
        # ★ 2026-07-10: 自动发现的模型 — 尝试用transformers执行
        if model_id.startswith('discovered_'):
            # 从discovered_models.json查找
            try:
                from src.core.auto_classifier import get_discovered_models
                all_discovered = get_discovered_models()
                # 提取原始ID
                raw_id = model_id.replace('discovered_', '').replace('_', '/').replace('_8b', ':8b')
                # 尝试匹配
                dm = None
                for d in all_discovered:
                    check_id = d['id'].replace('/', '_').replace(':', '_')
                    if check_id == model_id.replace('discovered_', ''):
                        dm = d
                        break
                if dm and dm.get('loader', '').startswith('transformers'):
                    # ★ 用transformers执行
                    result = _execute_discovered_transformers(dm, params)
                    return jsonify(result)
                elif dm and dm.get('loader') == 'ollama':
                    return jsonify({
                        "success": False,
                        "error": f"Ollama模型 {dm['id']} 请在模型设置里选择使用"
                    }), 200
            except Exception as e:
                logger.error(f"[Vertical] discovered模型执行失败: {e}")
            return jsonify({
                "success": False,
                "error": "模型暂不支持直接执行，请通过对话让哈文加载使用"
            }), 200  # ★ 200不是400，避免浏览器报红
        return jsonify({"success": False, "error": f"未知模型: {model_id}"}), 400

    engine = model.get('engine', 'tool_executor')

    try:
        if engine == 'tool_executor':
            # ★ 2026-07-04: tool_executor 类型用后台线程 + 5分钟超时
            import threading
            import time
            result_box = {"result": None, "error": None}
            def _worker():
                try:
                    result_box["result"] = _execute_via_tool_executor(model, params)
                except Exception as e:
                    result_box["error"] = e
            
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            t.join(timeout=600)  # ★ 2026-07-04: 10 分钟超时（CPU 模式需要 5-8 分钟）
            
            if t.is_alive():
                # 超时
                return jsonify({"success": False, "error": "执行超时（10分钟），MusicGen 可能仍在后台运行，请稍后检查 data/media/ 目录"}), 504
            if result_box["error"]:
                raise result_box["error"]
            result = result_box["result"]
        elif engine == 'model':
            result = _execute_via_model(model, params)
        elif engine == 'ollama':
            result = _execute_via_ollama(model, params)
        elif engine == 'llama_cpp':
            result = _execute_via_llama_cpp(model, params)
        elif engine == 'dynamic':
            result = _execute_via_dynamic_adapter(model, params)
        else:
            return jsonify({"success": False, "error": f"未知引擎类型: {engine}"}), 400

        # ★ 2026-07-04: 执行成功后写入历史记录
        try:
            _save_to_history(model_id, model.get('name', model_id), params, result)
        except Exception as he:
            logger.warning(f"[Vertical] 历史记录写入失败（不影响主流程）: {he}")

        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"[Vertical] 执行失败 {model_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# 历史记录（2026-07-04 新增）
# ═══════════════════════════════════════════════════════════════

def _get_history_db():
    """获取历史记录数据库连接"""
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).parent.parent.parent / 'data' / 'sqlite' / 'lingzhi.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # 自动建表
    conn.execute('''
        CREATE TABLE IF NOT EXISTS vertical_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT NOT NULL,
            model_name TEXT,
            params_json TEXT,
            file_path TEXT,
            file_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn


def _save_to_history(model_id: str, model_name: str, params: dict, result: dict):
    """执行成功后保存到历史记录"""
    import json
    # 从result提取文件路径
    file_path = ''
    file_type = ''
    if isinstance(result, dict):
        data = result.get('data') or result.get('output') or {}
        if isinstance(data, dict):
            file_path = data.get('file') or data.get('url') or data.get('path') or ''
        elif isinstance(data, str):
            file_path = data
    # 推断文件类型
    if file_path:
        ext = file_path.lower().split('.')[-1] if '.' in file_path else ''
        if ext in ['wav', 'mp3', 'ogg', 'flac']:
            file_type = 'audio'
        elif ext in ['png', 'jpg', 'jpeg', 'webp', 'gif']:
            file_type = 'image'
        elif ext in ['mp4', 'webm', 'mov']:
            file_type = 'video'
        else:
            file_type = 'other'

    conn = _get_history_db()
    try:
        conn.execute(
            'INSERT INTO vertical_history (model_id, model_name, params_json, file_path, file_type) VALUES (?, ?, ?, ?, ?)',
            (model_id, model_name, json.dumps(params, ensure_ascii=False), file_path, file_type)
        )
        conn.commit()
    finally:
        conn.close()


@bp.route('/history', methods=['GET'])
def get_history():
    """获取历史记录列表
    
    查询参数:
        model_id: 可选，按模型过滤
        limit: 可选，默认50
    """
    try:
        model_id = request.args.get('model_id', '')
        limit = min(int(request.args.get('limit', 50)), 200)

        conn = _get_history_db()
        try:
            if model_id:
                rows = conn.execute(
                    'SELECT * FROM vertical_history WHERE model_id = ? ORDER BY id DESC LIMIT ?',
                    (model_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM vertical_history ORDER BY id DESC LIMIT ?',
                    (limit,)
                ).fetchall()

            import json
            history = []
            for r in rows:
                params = {}
                try:
                    params = json.loads(r['params_json']) if r['params_json'] else {}
                except Exception as _e: logger.debug(f"[vertical_api] 忽略: {_e}")
                history.append({
                    'id': r['id'],
                    'model_id': r['model_id'],
                    'model_name': r['model_name'],
                    'params': params,
                    'file_path': r['file_path'],
                    'file_type': r['file_type'],
                    'created_at': r['created_at'],
                })
        finally:
            conn.close()

        return jsonify({"success": True, "history": history})
    except Exception as e:
        logger.error(f"[Vertical] 获取历史记录失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/history/<int:history_id>', methods=['DELETE'])
def delete_history(history_id):
    """删除单条历史记录"""
    try:
        conn = _get_history_db()
        try:
            cur = conn.execute('DELETE FROM vertical_history WHERE id = ?', (history_id,))
            conn.commit()
            if cur.rowcount == 0:
                return jsonify({"success": False, "error": "记录不存在"}), 404
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"[Vertical] 删除历史记录失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# 批量执行 — 2026-07-04 已移除（伪需求，12GB显存无法真并行）
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 适配器：tool_executor 路径（图片/音乐/语音）
# ═══════════════════════════════════════════════════════════════

def _execute_via_tool_executor(model, params):
    """通过 tool_executor 执行（MusicGen/Bark/SD 等本地模型）"""
    from src.core.tool_executor import ToolExecutor
    executor = ToolExecutor()
    method_name = model.get('executor_method', '')
    if not method_name or not hasattr(executor, method_name):
        raise RuntimeError(f"tool_executor 没有 {method_name} 方法")

    method = getattr(executor, method_name)
    model_id = model['id']

    if model['type'] == 'image_generation':
        prompt = params.get('prompt', '')
        style = params.get('style', 'realistic')
        result = method(model_id, prompt, style=style)
    elif model['type'] == 'music_generation':
        prompt = _build_music_prompt(params)
        duration = int(params.get('duration', 15))
        result = method(model_id, prompt, duration=duration)
    elif model['type'] == 'speech_synthesis':
        text = params.get('text', '')
        voice = params.get('voice_preset', 'v2/zh_speaker_0')
        result = method(model_id, text, voice_preset=voice)
    else:
        raise RuntimeError(f"不支持的类型: {model['type']}")

    if not result or not result.get('success'):
        raise RuntimeError(result.get('error', '执行器返回空') if result else '执行器返回空')

    file_path = result.get('file', '')
    return {
        'file': '/' + file_path.replace('\\', '/').lstrip('/') if file_path else '',
        'output': result.get('output', ''),
        'duration': params.get('duration', ''),
    }


def _build_music_prompt(params):
    """从参数构建音乐生成提示词"""
    parts = []
    if params.get('style', 'auto') != 'auto':
        parts.append(params['style'])
    if params.get('mood', 'auto') != 'auto':
        parts.append(params['mood'])
    if params.get('instrument', 'auto') != 'auto':
        parts.append(params['instrument'])
    if params.get('bpm'):
        parts.append(f"BPM:{params['bpm']}")
    return ', '.join(parts) if parts else '一段原创音乐'


# ═══════════════════════════════════════════════════════════════
# 适配器：model 路径（代码/数据/翻译 — 通过语言模型生成）
# ═══════════════════════════════════════════════════════════════

def _execute_via_ollama(model, params):
    """通过Ollama执行垂直模型（如华佗医疗模型）"""
    import requests
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    question = params.get('question', '') or params.get('prompt', '')
    if not question:
        return {"error": "请输入问题描述", "output": ""}

    ollama_model = model.get('ollama_model', model.get('id', ''))
    system_prompt = "你是一个专业的医疗AI助手，请基于医学知识回答用户的健康问题。回答要准确、专业、易懂。如果问题超出你的能力范围，请建议用户咨询专业医生。"

    try:
        r = requests.post(
            'http://localhost:11434/api/chat',
            json={
                'model': ollama_model,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': question}
                ],
                'stream': False,
                'options': {'temperature': 0.7, 'num_predict': 1024}
            },
            timeout=120
        )
        if r.status_code == 200:
            data = r.json()
            answer = data.get('message', {}).get('content', '')
            return {
                "output": answer,
                "model": model.get('name', '华佗医疗模型'),
                "question": question
            }
        else:
            return {"error": f"Ollama返回错误: {r.status_code}", "output": ""}
    except Exception as e:
        _logger.error(f"[Vertical] Ollama执行失败: {e}")
        return {"error": f"执行失败: {str(e)[:200]}", "output": ""}


def _execute_via_llama_cpp(model, params):
    """通过llama.cpp加载GGUF模型执行（如华佗医疗模型）"""
    import os
    
    # 获取问题
    question = params.get('question', '') or params.get('prompt', '')
    if not question:
        return {"error": "请输入问题描述", "output": ""}
    
    # 获取GGUF模型路径
    model_path = model.get('model_path', '')
    if not model_path:
        # 默认华佗路径
        model_path = os.path.expanduser(
            '~/.cache/huggingface/hub/models--QuantFactory--HuatuoGPT-o1-7B-GGUF/snapshots/main/HuatuoGPT-o1-7B.Q8_0.gguf'
        )
    else:
        model_path = os.path.expanduser(model_path)
    
    # 查找实际GGUF文件
    if not os.path.exists(model_path):
        snapshot_dir = os.path.dirname(model_path)
        if os.path.isdir(snapshot_dir):
            for d in os.listdir(snapshot_dir):
                full = os.path.join(snapshot_dir, d)
                if os.path.isdir(full):
                    for f in os.listdir(full):
                        if f.endswith('.gguf'):
                            model_path = os.path.join(full, f)
                            break
    
    if not os.path.exists(model_path):
        return {"error": f"GGUF模型文件不存在: {model_path}", "output": ""}
    
    logger.info(f"[Vertical] 加载GGUF模型: {model_path}")
    
    try:
        # ★ 用哈文自己的LlamaEngine（跟model_manager一致）
        from src.core.llama_engine import LlamaEngine
        
        cache_key = f"_llama_cache_{model['id']}"
        cached = getattr(_execute_via_llama_cpp, cache_key, None)
        
        if cached is None or not cached.is_loaded:
            logger.info(f"[Vertical] 首次加载GGUF模型（可能需要几秒）...")
            cached = LlamaEngine(model_path)
            if not cached.is_loaded:
                return {"error": "模型加载失败，请确认llama-cpp-python已安装", "output": ""}
            setattr(_execute_via_llama_cpp, cache_key, cached)
        
        # 构建提示词
        system_prompt = "你是一个专业的医疗AI助手，请基于医学知识回答用户的健康问题。回答要准确、专业、易懂。如果问题超出你的能力范围，请建议用户咨询专业医生。"
        
        answer = cached.generate(
            prompt=f"User: {question}\nAssistant:",
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=1024
        )
        
        return {
            "output": answer,
            "model": model.get('name', '华佗医疗模型'),
            "question": question
        }
        
    except ImportError:
        return {"error": "llama-cpp-python未安装，请运行: pip install llama-cpp-python", "output": ""}
    except Exception as e:
        logger.error(f"[Vertical] GGUF执行失败: {e}")
        return {"error": f"执行失败: {str(e)[:200]}", "output": ""}


def _execute_via_model(model, params):
    """通过语言模型执行（代码生成/数据分析/翻译）"""
    from src.core.engine import get_conversation_engine
    engine = get_conversation_engine()

    model_type = model['type']

    if model_type == 'code_generation':
        prompt = _build_code_prompt(params)
    elif model_type == 'data_analysis':
        prompt = _build_data_prompt(params)
    elif model_type == 'translation':
        prompt = _build_translation_prompt(params)
    else:
        prompt = json.dumps(params, ensure_ascii=False)

    response = engine.generate_sync(prompt, max_tokens=4096)

    if model_type == 'code_generation':
        return {'code': response, 'language': params.get('language', 'python')}
    elif model_type == 'data_analysis':
        return {'output': response, 'chart': '', 'table': response}
    elif model_type == 'translation':
        return {'original': params.get('text', ''), 'translated': response}
    else:
        return {'output': response}


def _build_code_prompt(params):
    desc = params.get('description', '')
    lang = params.get('language', 'python')
    fw = params.get('framework', '')
    return f"请用 {lang} 编写以下功能的代码{'（使用 ' + fw + ' 框架）' if fw else ''}：\n\n{desc}\n\n只输出代码，不要解释。"


def _build_data_prompt(params):
    data = params.get('data_source', '')
    goal = params.get('analysis_goal', '')
    chart = params.get('chart_type', 'auto')
    return f"请分析以下数据：\n\n{data}\n\n分析目标：{goal}\n图表类型：{chart}\n\n请给出分析结果和关键发现。"


def _build_translation_prompt(params):
    text = params.get('text', '')
    src = params.get('source_lang', 'auto')
    tgt = params.get('target_lang', 'English')
    if src == 'auto':
        return f"请将以下文本翻译成{tgt}，只输出译文：\n\n{text}"
    else:
        return f"请将以下{src}文本翻译成{tgt}，只输出译文：\n\n{text}"


# ═══════════════════════════════════════════════════════════════
# Phase 2: 动态适配器注册系统
# ═══════════════════════════════════════════════════════════════

# 运行时缓存：动态注册的模型定义 + 已加载的适配器模块
_dynamic_models = {}      # model_id -> model metadata dict
_loaded_adapters = {}      # adapter_id -> module object

# 适配器代码安全检查：禁止的危险导入
_DANGEROUS_IMPORTS = {
    'subprocess', 'shutil', 'ctypes', 'multiprocessing',
    'socket', 'socketserver', 'http.server', 'xmlrpc',
    'child_process', 'os.system', 'os.popen', 'os.exec',
    'os.spawn', 'os.kill', 'os.remove', 'os.rmdir',
}
_DANGEROUS_PATTERNS = [
    'os.system(', 'os.popen(', 'os.exec', 'os.spawn',
    'subprocess.', 'shutil.rmtree', '__import__',
    'eval(', 'exec(', 'compile(',
    'open("/', "open('/", 'open("C:', "open('C:",
    'import socket', 'from socket',
]


def _security_check_adapter_code(code: str) -> tuple:
    """安全检查适配器代码，返回 (is_safe, issues)"""
    issues = []

    # 检查危险导入
    for line in code.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        for danger in _DANGEROUS_IMPORTS:
            if f'import {danger}' in stripped or f'from {danger}' in stripped:
                issues.append(f"禁止的导入: {danger}")

    # 检查危险模式
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in code:
            issues.append(f"危险代码模式: {pattern}")

    return (len(issues) == 0, issues)


def _load_adapter_module(adapter_id: str, code: str) -> types.ModuleType:
    """用 importlib 动态加载适配器代码为模块"""
    module_name = f"_vertical_adapter_{adapter_id.replace('-', '_')}"

    spec = importlib.util.spec_from_loader(module_name, loader=None)
    module = importlib.util.module_from_spec(spec)

    # 注入必要的上下文
    module.__dict__['_ADAPTER_META'] = None  # 适配器通过 VERTICAL_MODEL 定义元数据

    exec(compile(code, f'<vertical_adapter:{adapter_id}>', 'exec'), module.__dict__)
    sys.modules[module_name] = module
    return module


def _register_dynamic_model(model_meta: dict, adapter_code: str = None, adapter_id: str = None):
    """将一个动态模型注册到运行时缓存"""
    model_id = model_meta.get('id', '')
    if not model_id:
        raise ValueError("模型元数据缺少 id 字段")

    _dynamic_models[model_id] = model_meta

    if adapter_code:
        # 保存适配器代码到文件
        _ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)
        code_file = _ADAPTERS_DIR / f"{adapter_id or model_id}.py"
        code_file.write_text(adapter_code, encoding='utf-8')

        # 保存元数据
        meta_file = _ADAPTERS_DIR / f"{adapter_id or model_id}.json"
        meta_file.write_text(json.dumps(model_meta, ensure_ascii=False, indent=2), encoding='utf-8')

    logger.info(f"[Vertical] 动态适配器已注册: {model_id}")


def _load_all_adapters_from_disk():
    """启动时从磁盘加载所有已保存的动态适配器"""
    if not _ADAPTERS_DIR.exists():
        return

    for meta_file in _ADAPTERS_DIR.glob("*.json"):
        try:
            model_meta = json.loads(meta_file.read_text(encoding='utf-8'))
            model_id = model_meta.get('id', meta_file.stem)

            # 加载对应的 .py 适配器代码
            code_file = _ADAPTERS_DIR / f"{meta_file.stem}.py"
            if code_file.exists():
                code = code_file.read_text(encoding='utf-8')
                is_safe, issues = _security_check_adapter_code(code)
                if not is_safe:
                    logger.warning(f"[Vertical] 跳过不安全的适配器 {model_id}: {issues}")
                    continue

                module = _load_adapter_module(meta_file.stem, code)
                _loaded_adapters[meta_file.stem] = module

            _dynamic_models[model_id] = model_meta
            logger.info(f"[Vertical] 从磁盘加载适配器: {model_id}")
        except Exception as e:
            logger.error(f"[Vertical] 加载适配器 {meta_file.stem} 失败: {e}")


@bp.route('/register', methods=['POST'])
def register_adapter():
    """
    注册动态适配器
    请求体:
    {
        "model_meta": {模型元数据, 同 _BUILTIN_MODELS 格式},
        "adapter_code": "Python 适配器代码字符串",
        "adapter_id": "可选适配器ID，默认用 model_id"
    }
    """
    data = request.get_json(silent=True) or {}
    model_meta = data.get('model_meta', {})
    adapter_code = data.get('adapter_code', '')
    adapter_id = data.get('adapter_id', model_meta.get('id', ''))

    if not model_meta.get('id'):
        return jsonify({"success": False, "error": "model_meta.id 不能为空"}), 400

    if not adapter_code:
        return jsonify({"success": False, "error": "adapter_code 不能为空"}), 400

    # 安全检查
    is_safe, issues = _security_check_adapter_code(adapter_code)
    if not is_safe:
        return jsonify({"success": False, "error": "适配器代码未通过安全检查", "issues": issues}), 403

    try:
        # 尝试加载模块
        module = _load_adapter_module(adapter_id or model_meta['id'], adapter_code)

        # 检查模块是否定义了必要的接口
        if not hasattr(module, 'execute'):
            return jsonify({"success": False, "error": "适配器代码缺少 execute(params: dict) -> dict 函数"}), 400

        _loaded_adapters[adapter_id or model_meta['id']] = module

        # 保存到磁盘
        _register_dynamic_model(model_meta, adapter_code, adapter_id or model_meta['id'])

        return jsonify({
            "success": True,
            "message": f"适配器 {model_meta['id']} 注册成功",
            "model_id": model_meta['id']
        })
    except Exception as e:
        logger.error(f"[Vertical] 注册适配器失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/adapters', methods=['GET'])
def list_adapters():
    """列出所有动态注册的适配器"""
    adapters = []
    for model_id, meta in _dynamic_models.items():
        adapters.append({
            "model_id": model_id,
            "name": meta.get('name', model_id),
            "icon": meta.get('icon', ''),
            "type": meta.get('type', 'unknown'),
            "description": meta.get('description', ''),
            "is_dynamic": True,
        })
    return jsonify({"adapters": adapters, "count": len(adapters)})


@bp.route('/adapters/<adapter_id>', methods=['DELETE'])
def delete_adapter(adapter_id):
    """删除动态适配器"""
    # 从运行时移除
    _dynamic_models.pop(adapter_id, None)
    _loaded_adapters.pop(adapter_id, None)

    # 从磁盘删除
    for ext in ['.py', '.json']:
        f = _ADAPTERS_DIR / f"{adapter_id}{ext}"
        if f.exists():
            f.unlink()

    # 从 sys.modules 移除
    mod_name = f"_vertical_adapter_{adapter_id.replace('-', '_')}"
    sys.modules.pop(mod_name, None)

    return jsonify({"success": True, "message": f"适配器 {adapter_id} 已删除"})


@bp.route('/adapters/<adapter_id>/reload', methods=['POST'])
def reload_adapter(adapter_id):
    """热重载适配器代码"""
    code_file = _ADAPTERS_DIR / f"{adapter_id}.py"
    meta_file = _ADAPTERS_DIR / f"{adapter_id}.json"

    if not code_file.exists():
        return jsonify({"success": False, "error": f"适配器 {adapter_id} 不存在"}), 404

    try:
        code = code_file.read_text(encoding='utf-8')
        is_safe, issues = _security_check_adapter_code(code)
        if not is_safe:
            return jsonify({"success": False, "error": "代码未通过安全检查", "issues": issues}), 403

        # 先移除旧模块
        mod_name = f"_vertical_adapter_{adapter_id.replace('-', '_')}"
        sys.modules.pop(mod_name, None)

        # 重新加载
        module = _load_adapter_module(adapter_id, code)
        _loaded_adapters[adapter_id] = module

        if meta_file.exists():
            model_meta = json.loads(meta_file.read_text(encoding='utf-8'))
            _dynamic_models[model_meta.get('id', adapter_id)] = model_meta

        return jsonify({"success": True, "message": f"适配器 {adapter_id} 已热重载"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# 修改 list_vertical_models 以包含动态注册的模型
# ═══════════════════════════════════════════════════════════════

def _get_all_models():
    """获取全部模型列表（内置 + 动态 + 自动发现的本地模型）"""
    all_models = list(_BUILTIN_MODELS)

    # 动态注册的模型
    for model_id, meta in _dynamic_models.items():
        meta_copy = dict(meta)
        meta_copy['is_dynamic'] = True
        all_models.append(meta_copy)

    # ★ 2026-07-10: 自动发现的本地模型（从discovered_models.json读取）
    # 只显示垂直模型（medical/music/voice/image/code/translate），不显示general/embedding
    VERTICAL_CATEGORIES = {'medical', 'music', 'voice', 'image', 'code', 'translate', 'legal', 'finance', 'education', 'vision'}
    try:
        from src.core.auto_classifier import get_discovered_models
        discovered = get_discovered_models()
        for dm in discovered:
            # ★ 过滤：只显示垂直模型，不显示通用/嵌入
            category = dm.get('category', 'general')
            if category not in VERTICAL_CATEGORIES:
                continue
            # 跳过已经是内置的模型（如musicgen-small）
            if any(bm['id'] == dm['id'] for bm in _BUILTIN_MODELS):
                continue
            # ★ 2026-07-12: 也跳过名字跟内置模型重复的（如 facebook/musicgen-small）
            _dm_name_lower = dm.get('name', '').lower()
            _dm_id_lower = dm.get('id', '').lower()
            _skip = False
            for bm in _BUILTIN_MODELS:
                if bm['id'] in _dm_name_lower or bm['id'] in _dm_id_lower:
                    _skip = True
                    break
            if _skip:
                continue
            model_entry = {
                'id': f"discovered_{dm['id'].replace('/', '_').replace(':', '_')}",
                'name': f"{dm.get('icon','📦')} {dm['name']}",
                'icon': dm.get('icon', '📦'),
                'type': dm.get('category', 'general'),
                'canvas_type': 'generic',
                'description': f"自动发现: {dm['id']} ({dm.get('architecture', 'unknown')}) - {dm.get('source', '')}",
                'engine': 'tool_executor',
                'executor_method': 'run_discovered_model',
                'source': dm.get('source', ''),
                'model_path': dm.get('path', ''),
                'loader': dm.get('loader', ''),
                'is_discovered': True,
                'params': {
                    'prompt': {'type': 'textarea', 'label': '输入', 'default': '', 'required': True},
                }
            }
            all_models.append(model_entry)
    except Exception as e:
        logger.warning(f"[Vertical] 加载自动发现模型失败: {e}")

    return all_models


# ═══════════════════════════════════════════════════════════════
# ★ 2026-07-10: discovered模型执行器（transformers加载）
# ═══════════════════════════════════════════════════════════════

_discovered_model_cache = {}  # 模型缓存（避免每次重新加载）

def _execute_discovered_transformers(model_info, params):
    """用transformers执行discovered模型"""
    import time
    model_path = model_info.get('path', '')
    prompt = params.get('prompt', '')

    if not model_path or not prompt:
        return {"success": False, "error": "缺少模型路径或输入"}

    cache_key = model_path
    t0 = time.time()

    try:
        # ★ 模型懒加载 + 缓存
        if cache_key not in _discovered_model_cache:
            logger.info(f"[Discovered] 首次加载模型: {model_path}")
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import torch

            tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                device_map='auto',
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                trust_remote_code=True
            )
            _discovered_model_cache[cache_key] = {'model': model, 'tokenizer': tokenizer}
            logger.info(f"[Discovered] 模型加载完成 ({time.time()-t0:.1f}s)")

        cached = _discovered_model_cache[cache_key]
        model = cached['model']
        tokenizer = cached['tokenizer']

        # ★ 生成回答
        t1 = time.time()
        messages = [{"role": "user", "content": prompt}]

        try:
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        except Exception:
            # 如果不支持chat_template，直接用prompt
            text = prompt

        inputs = tokenizer(text, return_tensors='pt').to(model.device)
        outputs = model.generate(
            **inputs,
            max_new_tokens=512,
            temperature=0.7,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id
        )
        response = tokenizer.decode(outputs[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True)
        gen_time = time.time() - t1

        logger.info(f"[Discovered] 生成完成 ({gen_time:.1f}s)")

        return {
            "success": True,
            "output": response,
            "model": model_info.get('id', ''),
            "time": f"{gen_time:.1f}s"
        }

    except Exception as e:
        logger.error(f"[Discovered] 执行失败: {e}")
        return {"success": False, "error": str(e)}# 修改 execute 以支持动态适配器
def _execute_via_dynamic_adapter(model_meta, params):
    """通过动态加载的适配器模块执行"""
    model_id = model_meta['id']
    adapter_id = model_meta.get('adapter_id', model_id)

    module = _loaded_adapters.get(adapter_id)
    if module is None:
        raise RuntimeError(f"适配器 {adapter_id} 未加载")

    if not hasattr(module, 'execute'):
        raise RuntimeError(f"适配器 {adapter_id} 缺少 execute() 函数")

    result = module.execute(params)
    if not isinstance(result, dict):
        raise RuntimeError("适配器 execute() 必须返回 dict")
    return result


# ═══════════════════════════════════════════════════════════════
# Phase 3: 自动分析模型文档 → 生成适配器代码
# ═══════════════════════════════════════════════════════════════

# 适配器代码模板
_ADAPTER_TEMPLATE = '''# Auto-generated vertical adapter for {model_name}
# Generated at {timestamp}
# Source: {source}
"""
垂直模型适配器: {model_name}
模型类型: {model_type}
描述: {description}
"""
import logging

logger = logging.getLogger(__name__)

# ═══ 模型元数据 ═══
VERTICAL_MODEL = {model_meta_json}

# ═══ 适配器实现 ═══

def execute(params: dict) -> dict:
    """
    执行垂直模型
    Args:
        params: 参数字典，键名对应 VERTICAL_MODEL['params'] 中定义的参数
    Returns:
        dict: 至少包含以下键:
            - output: 主要输出内容（文本/路径）
            - file: 生成的文件路径（如有）
            - metadata: 额外元数据
    """
    {execute_body}
    return result
'''


def _analyze_model_documentation(doc_text: str, model_name: str) -> dict:
    """
    分析模型文档，提取关键信息用于生成适配器
    返回: {model_type, params, execute_strategy, description}
    """
    doc_lower = doc_text.lower()

    # ── 推断模型类型 ──
    type_hints = {
        'image_generation': ['image', 'diffusion', 'gan', 'stylegan', 'stable diffusion', '图片生成', '文生图'],
        'music_generation': ['music', 'audio', 'midi', 'waveform', '音乐生成', '音频生成'],
        'speech_synthesis': ['tts', 'speech', 'voice', 'bark', '语音合成', '文本转语音'],
        'speech_recognition': ['asr', 'whisper', 'speech recognition', '语音识别'],
        'code_generation': ['code', 'programming', 'copilot', '代码生成'],
        'data_analysis': ['data', 'analysis', 'chart', '数据分析'],
        'translation': ['translation', 'translate', '翻译'],
        'text_to_video': ['video', '帧序列', '视频生成'],
        'text_to_3d': ['3d', 'mesh', 'point cloud', '三维'],
        'text_summarization': ['summariz', '摘要', '总结'],
    }

    detected_type = 'generic'
    type_scores = {}
    for mtype, keywords in type_hints.items():
        score = sum(1 for kw in keywords if kw in doc_lower)
        if score > 0:
            type_scores[mtype] = score
    if type_scores:
        detected_type = max(type_scores, key=type_scores.get)

    # ── 推断执行策略 ──
    has_python_api = any(kw in doc_lower for kw in ['transformers', 'from_pretrained', 'pipeline', 'torch', 'model.load'])
    has_cli = any(kw in doc_lower for kw in ['cli', 'command line', 'terminal', '--input', '--output'])
    has_http_api = any(kw in doc_lower for kw in ['http', 'api', 'endpoint', 'rest', 'curl'])

    if has_python_api:
        execute_strategy = 'python_api'
    elif has_cli:
        execute_strategy = 'cli'
    elif has_http_api:
        execute_strategy = 'http_api'
    else:
        execute_strategy = 'python_api'  # 默认

    # ── 提取参数提示 ──
    params_hint = {}
    # 常见参数关键词检测
    if 'prompt' in doc_lower or '描述' in doc_lower or 'input text' in doc_lower:
        params_hint['prompt'] = {"type": "textarea", "label": "输入描述", "default": "", "placeholder": "请输入..."}
    if 'duration' in doc_lower or '时长' in doc_lower or 'length' in doc_lower:
        params_hint['duration'] = {"type": "range", "label": "时长", "default": 15, "min": 5, "max": 60}
    if 'size' in doc_lower or 'resolution' in doc_lower or '尺寸' in doc_lower:
        params_hint['size'] = {"type": "select", "label": "尺寸", "default": "512x512", "options": ["256x256", "512x512", "768x768", "1024x1024"]}
    if 'language' in doc_lower or '语言' in doc_lower:
        params_hint['language'] = {"type": "select", "label": "语言", "default": "auto", "options": ["auto", "中文", "English", "日本語"]}
    if 'style' in doc_lower or '风格' in doc_lower:
        params_hint['style'] = {"type": "select", "label": "风格", "default": "auto", "options": ["auto", "realistic", "anime", "oil-painting", "classical"]}

    if not params_hint:
        params_hint['input'] = {"type": "textarea", "label": "输入", "default": "", "placeholder": "请输入..."}

    return {
        'model_type': detected_type,
        'canvas_type': detected_type,
        'params': params_hint,
        'execute_strategy': execute_strategy,
        'description': f'自动适配的{detected_type}模型',
    }


def _generate_adapter_code(model_meta: dict, analysis: dict) -> str:
    """根据模型元数据和分析结果生成适配器代码"""
    execute_body = _build_execute_body(analysis)

    return _ADAPTER_TEMPLATE.format(
        model_name=model_meta.get('name', model_meta.get('id', 'unknown')),
        timestamp=datetime.now().isoformat(),
        source=model_meta.get('source', 'auto-generated'),
        model_type=analysis.get('model_type', 'generic'),
        description=analysis.get('description', ''),
        model_meta_json=json.dumps(model_meta, ensure_ascii=False, indent=4),
        execute_body=execute_body,
    )


def _build_execute_body(analysis: dict) -> str:
    """根据分析结果生成 execute() 函数体"""
    strategy = analysis.get('execute_strategy', 'python_api')
    model_type = analysis.get('model_type', 'generic')

    if strategy == 'python_api':
        return _PYTHON_API_TEMPLATE
    elif strategy == 'cli':
        return _CLI_API_TEMPLATE
    elif strategy == 'http_api':
        return _HTTP_API_TEMPLATE
    else:
        return _GENERIC_TEMPLATE


_PYTHON_API_TEMPLATE = '''# TODO: 根据模型文档实现实际调用逻辑
    # 以下为模板代码，请根据模型 API 修改

    # 示例：通过 transformers 加载模型
    # from transformers import pipeline
    # _pipe = pipeline("{model_type}", model="model_path")
    # output = _pipe(params.get("prompt", ""))

    # 占位实现 — 返回输入参数作为参考
    logger.info(f"[Adapter] 执行参数: {params}")
    result = {
        "output": f"适配器已加载，但尚未实现实际调用。\\n接收到的参数: {params}",
        "file": "",
        "metadata": {"status": "template", "strategy": "python_api"}
    }'''


_CLI_API_TEMPLATE = '''# TODO: 通过命令行调用模型
    # import subprocess
    # cmd = ["python", "model_cli.py", "--input", params.get("prompt", "")]
    # result = subprocess.run(cmd, capture_output=True, text=True)

    logger.info(f"[Adapter] CLI 执行参数: {params}")
    result = {
        "output": f"CLI 适配器模板，尚未实现。\\n参数: {params}",
        "file": "",
        "metadata": {"status": "template", "strategy": "cli"}
    }'''


_HTTP_API_TEMPLATE = '''# TODO: 通过 HTTP API 调用模型
    # import urllib.request
    # import json
    # data = json.dumps(params).encode()
    # req = urllib.request.Request("http://localhost:8080/generate", data=data)
    # resp = urllib.request.urlopen(req)
    # output = json.loads(resp.read())

    logger.info(f"[Adapter] HTTP 执行参数: {params}")
    result = {
        "output": f"HTTP 适配器模板，尚未实现。\\n参数: {params}",
        "file": "",
        "metadata": {"status": "template", "strategy": "http_api"}
    }'''


_GENERIC_TEMPLATE = '''logger.info(f"[Adapter] 通用执行参数: {params}")
    result = {
        "output": f"通用适配器模板。\\n参数: {params}",
        "file": "",
        "metadata": {"status": "template"}
    }'''


@bp.route('/auto-generate', methods=['POST'])
def auto_generate_adapter():
    """
    自动分析模型文档，生成适配器代码
    请求体:
    {
        "model_id": "my-custom-model",
        "model_name": "My Custom Model",
        "doc_text": "模型文档/README 内容",
        "doc_path": "可选，文档文件路径",
        "icon": "可选，图标 emoji",
        "auto_register": true  // 是否自动注册
    }
    """
    data = request.get_json(silent=True) or {}
    model_id = data.get('model_id', '')
    model_name = data.get('model_name', model_id)
    doc_text = data.get('doc_text', '')
    doc_path = data.get('doc_path', '')
    icon = data.get('icon', '🔧')
    auto_register = data.get('auto_register', True)

    if not model_id:
        return jsonify({"success": False, "error": "model_id 不能为空"}), 400

    # 如果提供了文件路径而非文本内容，读取文件
    if not doc_text and doc_path:
        try:
            p = Path(doc_path)
            if p.exists():
                doc_text = p.read_text(encoding='utf-8')
        except Exception as e:
            return jsonify({"success": False, "error": f"读取文档失败: {e}"}), 400

    if not doc_text:
        return jsonify({"success": False, "error": "需要提供 doc_text 或 doc_path"}), 400

    try:
        # 分析文档
        analysis = _analyze_model_documentation(doc_text, model_name)

        # 构建模型元数据
        model_meta = {
            "id": model_id,
            "name": model_name,
            "icon": icon,
            "type": analysis['model_type'],
            "canvas_type": analysis['canvas_type'],
            "description": analysis['description'],
            "engine": "dynamic",
            "adapter_id": model_id,
            "params": analysis['params'],
            "source": "auto-generated",
            "execute_strategy": analysis['execute_strategy'],
        }

        # 生成适配器代码
        adapter_code = _generate_adapter_code(model_meta, analysis)

        # 安全检查生成的代码
        is_safe, issues = _security_check_adapter_code(adapter_code)
        if not is_safe:
            return jsonify({"success": False, "error": "生成的代码未通过安全检查", "issues": issues}), 500

        # 自动注册
        if auto_register:
            module = _load_adapter_module(model_id, adapter_code)
            _loaded_adapters[model_id] = module
            _register_dynamic_model(model_meta, adapter_code, model_id)

        return jsonify({
            "success": True,
            "model_meta": model_meta,
            "adapter_code": adapter_code,
            "analysis": analysis,
            "registered": auto_register,
            "message": f"适配器 {model_id} 已自动生成" + ("并注册" if auto_register else "（未注册）")
        })
    except Exception as e:
        logger.error(f"[Vertical] 自动生成适配器失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# Phase 4: 动态画板模板生成
# ═══════════════════════════════════════════════════════════════

# 内置画板类型列表
_BUILTIN_CANVAS_TYPES = [
    {"type": "image_generation", "name": "图片画板", "renderer": "renderImageCanvas"},
    {"type": "music_generation", "name": "音乐画板", "renderer": "renderMusicCanvas"},
    {"type": "speech_synthesis", "name": "语音画板", "renderer": "renderAudioCanvas"},
    {"type": "code_generation", "name": "代码画板", "renderer": "renderCodeCanvas"},
    {"type": "data_analysis", "name": "数据画板", "renderer": "renderDataCanvas"},
    {"type": "translation", "name": "翻译画板", "renderer": "renderTranslationCanvas"},
    {"type": "generic", "name": "通用画板", "renderer": "renderGenericCanvas"},
]

# 动态注册的画板类型
_dynamic_canvas_types = {}

# 动态画板渲染器 JS 代码缓存
_dynamic_canvas_renderers = {}


@bp.route('/canvas-types', methods=['GET'])
def list_canvas_types():
    """获取所有可用画板类型（内置 + 动态）"""
    all_types = list(_BUILTIN_CANVAS_TYPES)
    for ct in _dynamic_canvas_types.values():
        all_types.append(ct)
    return jsonify({"canvas_types": all_types, "count": len(all_types)})


@bp.route('/generate-canvas', methods=['POST'])
def generate_canvas_template():
    """
    为新模型类型生成画板模板
    请求体:
    {
        "canvas_type": "新画板类型名称，如 text_to_video",
        "display_name": "显示名称",
        "description": "画板描述",
        "renderer_js": "可选，自定义渲染器 JS 代码",
        "layout_json": "可选，布局 JSON"
    }
    """
    data = request.get_json(silent=True) or {}
    canvas_type = data.get('canvas_type', '')
    display_name = data.get('display_name', canvas_type)
    description = data.get('description', '')
    renderer_js = data.get('renderer_js', '')
    layout_json = data.get('layout_json', {})

    if not canvas_type:
        return jsonify({"success": False, "error": "canvas_type 不能为空"}), 400

    # 检查是否已存在
    existing = [c for c in _BUILTIN_CANVAS_TYPES if c['type'] == canvas_type]
    if existing:
        return jsonify({"success": False, "error": f"画板类型 {canvas_type} 已存在"}), 409

    if canvas_type in _dynamic_canvas_types:
        return jsonify({"success": False, "error": f"动态画板类型 {canvas_type} 已存在"}), 409

    try:
        # 如果没有提供渲染器 JS，生成一个默认模板
        if not renderer_js:
            renderer_js = _generate_default_renderer_js(canvas_type, display_name)

        # 注册到动态画板类型
        canvas_meta = {
            "type": canvas_type,
            "name": display_name,
            "renderer": f"renderDynamic_{canvas_type.replace('-', '_')}",
            "is_dynamic": True,
            "description": description,
        }
        _dynamic_canvas_types[canvas_type] = canvas_meta
        _dynamic_canvas_renderers[canvas_type] = renderer_js

        # 保存到磁盘
        _ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)
        canvas_dir = _ADAPTERS_DIR / "canvas_templates"
        canvas_dir.mkdir(exist_ok=True)

        canvas_file = canvas_dir / f"{canvas_type}.js"
        canvas_file.write_text(renderer_js, encoding='utf-8')

        if layout_json:
            layout_file = canvas_dir / f"{canvas_type}_layout.json"
            layout_file.write_text(json.dumps(layout_json, ensure_ascii=False, indent=2), encoding='utf-8')

        # 保存画板类型注册信息
        registry_file = canvas_dir / "_registry.json"
        registry = {}
        if registry_file.exists():
            registry = json.loads(registry_file.read_text(encoding='utf-8'))
        registry[canvas_type] = canvas_meta
        registry_file.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')

        return jsonify({
            "success": True,
            "canvas_type": canvas_type,
            "renderer_js": renderer_js,
            "message": f"画板模板 {canvas_type} 已生成并注册"
        })
    except Exception as e:
        logger.error(f"[Vertical] 生成画板模板失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _generate_default_renderer_js(canvas_type: str, display_name: str) -> str:
    """生成默认的画板渲染器 JS 代码"""
    safe_name = canvas_type.replace('-', '_')
    return f'''// Auto-generated canvas renderer for {canvas_type}
// Display name: {display_name}
function renderDynamic_{safe_name}(container, data) {{
    const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    container.innerHTML =
        '<div style="margin-bottom:8px;font-size:12px;color:var(--text-secondary);">' +
        '🔧 {display_name} 结果</div>' +
        '<pre style="background:var(--bg-tertiary);padding:12px;border-radius:8px;' +
        'font-size:11px;overflow-x:auto;max-height:300px;">' +
        escapeHtml(text) + '</pre>';
}}
// Register globally so renderVerticalResult can find it
window.renderDynamic_{safe_name} = renderDynamic_{safe_name};'''


@bp.route('/canvas-renderers', methods=['GET'])
def get_dynamic_canvas_renderers():
    """获取所有动态画板渲染器 JS 代码（前端注入用）"""
    return jsonify({
        "renderers": _dynamic_canvas_renderers,
        "count": len(_dynamic_canvas_renderers)
    })


def _load_canvas_templates_from_disk():
    """启动时从磁盘加载所有动态画板模板"""
    canvas_dir = _ADAPTERS_DIR / "canvas_templates"
    if not canvas_dir.exists():
        return

    registry_file = canvas_dir / "_registry.json"
    if registry_file.exists():
        try:
            registry = json.loads(registry_file.read_text(encoding='utf-8'))
            for ct, meta in registry.items():
                _dynamic_canvas_types[ct] = meta

                js_file = canvas_dir / f"{ct}.js"
                if js_file.exists():
                    _dynamic_canvas_renderers[ct] = js_file.read_text(encoding='utf-8')

                logger.info(f"[Vertical] 从磁盘加载画板模板: {ct}")
        except Exception as e:
            logger.error(f"[Vertical] 加载画板模板注册表失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 初始化：启动时自动加载
# ═══════════════════════════════════════════════════════════════

def init_vertical_system():
    """初始化垂直模型系统 — 在 Flask app 启动时调用"""
    _load_all_adapters_from_disk()
    _load_canvas_templates_from_disk()
    logger.info(f"[Vertical] 系统初始化完成: {len(_dynamic_models)} 个动态适配器, {len(_dynamic_canvas_types)} 个动态画板模板")


# ═══════════════════════════════════════════════════════════════
# 修改原有路由以支持动态模型
# ═══════════════════════════════════════════════════════════════

# 覆写 list_vertical_models 以返回全部模型
@bp.route('/models', methods=['GET'])
def list_vertical_models():
    """获取全部垂直模型列表（内置 + 动态注册）"""
    return jsonify({"models": _get_all_models()})
