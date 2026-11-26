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
# 内置垂直模型定义（元数据驱动）
# ★ A批A2(2026-09-13): 翻译/代码生成/数据分析是 engine="model" 的伪模型（只是提示词模板套
#   当前对话模型），不再冒充垂直模型 —— pseudo=True 不进垂直列表；翻译/写代码直接在聊天框说，
#   数据分析走 pandas FC 工具。定义保留是为了兼容旧画板/历史记录的 model_id 引用（仍可执行）。
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
        # ★ 2026-11-15: 清空触发词（方案 A ③）——本条目为旧画板/历史引用保留户口（executor_method 仍可执行），
        #   但触发词与 z-image 条目几乎全同，且模型不可用时仍参与抢答（_detect_vertical_task 平分先注册者赢），
        #   导致「画…」消息被死条目截胡。清空后不再参与匹配，画图由 z-image 正常接管。
        "task_keywords": [],
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
        # ★ 2026-11-14: MusicGen Small 已卸载，替换为 ACE-Step v1-3.5B（Apache 2.0，支持人声歌曲）
        "id": "ace-step",
        "name": "ACE-Step 音乐",
        "icon": "🎵",
        "license": "Apache 2.0",
        "commercial_use": True,
        "license_warning": "",
        "type": "music_generation",
        "canvas_type": "music_generation",
        "description": "AI 音乐生成 — 纯音乐/带人声歌曲（3.5B 本地模型）",
        "engine": "tool_executor",
        "executor_method": "run_ace_step",
        "task_keywords": ["作曲", "音乐", "写曲", "生成音乐", "编曲", "曲子", "旋律", "music", "song", "compose",
                          "首歌", "一首歌", "写歌", "做歌", "做曲", "写一首", "生成歌", "唱首歌",
                          "乡村", "摇滚", "爵士", "流行乐", "古典", "电子乐", "布鲁斯", "民谣", "国风", "古风", "交响", "管弦", "史诗",
                          "钢琴曲", "吉他曲", "小提琴", "古筝", "二胡", "琵琶", "笛子", "贝斯", "萨克斯", "非洲鼓",
                          "BGM", "背景音乐", "配乐", "原声带",
                          "欢快", "悲伤", "忧伤", "舒缓", "激昂", "浪漫", "忧郁"],
        "capability": {
            "can_do": ["纯音乐/伴奏", "带人声歌曲（输入歌词即可演唱）", "10-240 秒长曲", "中英日韩多语言歌词", "国风/流行/古典/电子等全风格"],
            "cannot_do": ["克隆指定歌手音色", "精确旋律控制（哼唱转写）", "多轨分拆（伴奏/人声分离）"],
            "max_limits": "单次最长 240 秒，4070 显卡 30 秒曲约 10-30 秒生成",
            "advisor_tips": "纯音乐歌词填 [inst] 或留空。带人声：写 [verse] [chorus] 段落歌词放 lyrics，风格标签放 prompt（如 'chinese traditional, guzheng, peaceful'）。想要人声清晰可加 'vocal, female voice'。",
        },
        "params": {
            "prompt": {"type": "textarea", "label": "风格描述(英文标签)", "default": "", "placeholder": "如: chinese traditional, guzheng, peaceful, acoustic", "required": True},
            "lyrics": {"type": "textarea", "label": "歌词(可选)", "default": "[inst]", "placeholder": "[inst]=纯音乐；写 [verse]/[chorus] 段落歌词则生成人声歌曲"},
            "duration": {"type": "range", "label": "时长(秒)", "default": 30, "min": 10, "max": 240, "step": 5},
            "steps": {"type": "range", "label": "生成步数(质量)", "default": 27, "min": 8, "max": 60, "step": 1},
            "seed": {"type": "text", "label": "随机种子(可选)", "default": "-1", "placeholder": "-1=随机, 固定数字=复现"},
        }
    },
    {
        # ★ 2026-11-14 新增: Z-Image-Turbo（阿里通义 6B，Apache 2.0，8 步极速出图）
        "id": "z-image-turbo",
        "name": "Z-Image 图片",
        "icon": "🖼️",
        "license": "Apache 2.0",
        "commercial_use": True,
        "license_warning": "",
        "type": "image_generation",
        "canvas_type": "image_generation",
        "description": "AI 图片生成 — 中文理解好，8 步极速出图（6B 本地模型）",
        "engine": "tool_executor",
        "executor_method": "run_zimage",
        "task_keywords": ["画", "生成图片", "画图", "绘制", "插画", "海报", "logo", "图片", "image", "draw", "paint",
                          "画一张", "生成一张", "照片级", "写实照片"],
        "capability": {
            "can_do": ["中文描述直接出图（无需英文）", "写实照片级/插画/海报", "1024×1024 高清", "文字渲染能力较强"],
            "cannot_do": ["动态视频/动画", "精确编辑已有图片（局部重绘）", "超高分辨率(>1536px)"],
            "max_limits": "单张图，8 步约 60 秒/张（1024×1024）",
            "advisor_tips": "Z-Image 中文理解很强，直接用中文描述画面即可，比英文标签更准。描述越具体越好：主体+动作+环境+光线+风格（如'一只橘猫趴在窗台晒太阳，窗外海边雪景，暖色调摄影'）。",
        },
        "params": {
            "prompt": {"type": "textarea", "label": "画面描述", "default": "", "placeholder": "直接用中文描述想要的画面...", "required": True},
            "width": {"type": "range", "label": "宽度", "default": 1024, "min": 512, "max": 1536, "step": 64},
            "height": {"type": "range", "label": "高度", "default": 1024, "min": 512, "max": 1536, "step": 64},
            "steps": {"type": "range", "label": "步数", "default": 8, "min": 4, "max": 20, "step": 1},
            "seed": {"type": "text", "label": "随机种子(可选)", "default": "-1", "placeholder": "-1=随机, 固定数字=复现"},
        }
    },
    {
        # ★ 2026-11-14 新增: Wan2.2 T2V A14B（阿里通义，Apache 2.0，文生视频双专家）
        "id": "wan22-video",
        "name": "Wan2.2 视频",
        "icon": "🎬",
        "license": "Apache 2.0",
        "commercial_use": True,
        "license_warning": "",
        "type": "video_generation",
        "canvas_type": "video_generation",
        "description": "AI 视频生成 — 文字生成短视频（14B 本地模型，较慢）",
        "engine": "tool_executor",
        "executor_method": "run_wan22",
        "task_keywords": ["生成视频", "做视频", "短视频", "文生视频", "动画视频", "video",
                          "视频生成", "一段视频"],
        "capability": {
            "can_do": ["中文描述生成 3-8 秒短视频", "480p 电影感画面", "复杂运镜和物理动态"],
            "cannot_do": ["长视频（>8秒）", "1080p 高清（显存不够）", "图生视频（本条目纯文生）", "带音频"],
            "max_limits": "5 秒 480p 生成约 20-40 分钟，请耐心等待",
            "advisor_tips": "描述用'主体+动作+场景+镜头感'结构（如'一只橘猫在雪地窗台打哈欠，室内暖光，电影感'）。生成较慢（约半小时），适合等得起的场景。",
        },
        "params": {
            "prompt": {"type": "textarea", "label": "画面描述", "default": "", "placeholder": "描述视频内容：主体+动作+场景+镜头感...", "required": True},
            "seconds": {"type": "range", "label": "时长(秒)", "default": 5, "min": 3, "max": 8, "step": 1},
            "steps": {"type": "range", "label": "步数(质量)", "default": 30, "min": 15, "max": 50, "step": 5},
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
        "pseudo": True,
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
        "pseudo": True,
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
        "pseudo": True,
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
    # ★ #1559：组合链（chain:xxx）→ 自动生成 step_timeline 布局（先于普通模型查找）
    if model_id.startswith("chain:"):
        chain_id = model_id.split(":", 1)[1]
        steps_meta = []
        chain_name, target = "", ""
        try:
            from src.core.chain_engine.registry import get_registry
            r = get_registry().get_chain(chain_id)
            if r is not None:
                chain_name, target = r.name, r.target
                for i, s in enumerate(r.steps, 1):
                    steps_meta.append({"index": i, "name": s.name,
                                       "capability": s.capability, "note": s.note})
        except Exception as _e:
            logger.debug(f"[vertical_api] 忽略: {_e}")
        if not chain_name:
            return jsonify({"error": "组合不存在"}), 404
        return jsonify({
            "model_id": model_id, "canvas_type": "chain_pipeline",
            "chain_name": chain_name, "target": target,
            "sections": [
                {"type": "attachments", "position": "top"},
                {"type": "form", "position": "top"},
                {"type": "run_button", "label": "🚀 执行组合", "tip": "组合链执行中：各环节依次点亮…"},
                {"type": "step_timeline", "position": "middle", "steps": steps_meta},
                {"type": "preview", "position": "bottom", "height": "auto"},
                {"type": "capability_card", "position": "bottom"},
            ]})

    # 先从内置模型找
    model = next((m for m in _BUILTIN_MODELS if m['id'] == model_id), None)
    # 再从动态模型找
    if not model:
        model = _dynamic_models.get(model_id)
    # ★ #1560: 底座部件直答条目（mnv4/locate）
    if not model:
        model = _base_engine_model(model_id)
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
            # ★ #1559 回落门：坏 layout（无 sections/非列表）拒载回落默认
            if isinstance(custom, dict) and isinstance(custom.get("sections"), list) \
                    and custom["sections"]:
                layout = custom
            else:
                logger.warning(f"[vertical_api] 坏 layout 拒载回落 generic: {model_id}")
        except Exception as _e:
            logger.warning(f"[vertical_api] layout 解析失败回落默认: {model_id} {_e}")

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
    # ★ 2026-09-15 开发者令#1：附件通道 —— 图片/音频/视频/文章随表单一起发
    attachments = data.get('attachments') or []
    if not isinstance(attachments, list):
        attachments = []
    att_images, att_text = [], ''
    if attachments:
        try:
            att_images, att_text = _process_attachments(attachments[:4])
        except Exception as att_e:
            logger.error(f"[Vertical] 附件处理失败: {att_e}", exc_info=True)
            return jsonify({"success": False, "error": f"附件处理失败: {str(att_e)[:150]}"}), 200

    # 先从内置模型找
    model = next((m for m in _BUILTIN_MODELS if m['id'] == model_id), None)
    if not model:
        # 再从动态注册的模型找
        model = _dynamic_models.get(model_id)
    # ★ #1560: 底座部件直答（mnv4/locate）——附件守卫之前拦截
    if not model:
        be_model = _base_engine_model(model_id)
        if be_model:
            try:
                result = _execute_base_engine(be_model, params, attachments)
            except Exception as e:
                logger.error(f"[Vertical] 底座部件执行失败 {model_id}: {e}", exc_info=True)
                return jsonify({"success": False, "error": str(e)[:200]}), 200
            if result.get("error"):
                return jsonify({"success": False, "error": result["error"]}), 200
            try:
                _save_to_history(model_id, be_model.get('name', model_id), params, result)
            except Exception as he:
                logger.warning(f"[Vertical] 历史记录写入失败（不影响主流程）: {he}")
            return jsonify({"success": True, "data": result})
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
                    # ★ 2026-09-15 方案1（已批准准）：Ollama 来源的发现模型直接走 Ollama 引擎执行
                    #   （原逻辑吐老提示"请在模型设置里选择使用"——列表页 engine='ollama' 但执行入口
                    #    查不到 discovered 模型，掉进这个旧兜底；现在复用已实测的 _execute_via_ollama）
                    _om = {
                        'id': f"discovered_{dm['id'].replace('/', '_').replace(':', '_')}",
                        'name': f"{dm.get('icon', '📦')} {dm['name']}",
                        'ollama_model': dm['id'],  # 原始模型名（minicpm-v:8b）
                        'type': dm.get('category', 'general'),
                    }
                    logger.info(f"[Vertical] discovered Ollama模型执行: {dm['id']}")
                    return jsonify(_execute_via_ollama(_om, params, images=att_images, extra_text=att_text))
            except Exception as e:
                logger.error(f"[Vertical] discovered模型执行失败: {e}")
            return jsonify({
                "success": False,
                "error": "模型暂不支持直接执行，请通过对话让哈文加载使用"
            }), 200  # ★ 200不是400，避免浏览器报红
        return jsonify({"success": False, "error": f"未知模型: {model_id}"}), 400

    engine = model.get('engine', 'tool_executor')

    # ★ #1534（2026-11-24）：组合即模型——组合以动态模型身份出现在垂直页，
    #   engine='chain' 的执行委托给编排器（模型决策全在配方单，铁律0 不碰）。
    #   注意走原始 attachments（视频附件整段给 video_brief，不在这里被抽帧摊平）。
    if engine == 'chain':
        from src.core.chain_engine.binding import execute_chain_model
        return jsonify(execute_chain_model(model, params, attachments))

    # ★ 2026-09-15: 附件只支持 Ollama 引擎模型（minicpm-v 等看图/转写模型）；其他引擎给出友好提示
    if attachments and engine != 'ollama':
        return jsonify({"success": False,
                        "error": "这个模型暂时只支持文字输入。图片/音频/视频请用看图·理解类模型（如 👁️ minicpm-v:8b）"}), 200

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
                return jsonify({"success": False, "error": "执行超时（10分钟），生成任务可能仍在后台运行，请稍后检查 data/media/ 目录"}), 504
            if result_box["error"]:
                raise result_box["error"]
            result = result_box["result"]
        elif engine == 'model':
            result = _execute_via_model(model, params)
        elif engine == 'ollama':
            result = _execute_via_ollama(model, params, images=att_images, extra_text=att_text)
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

    # ★ 2026-11-14: 按方法签名智能传参（修复 run_zimage 不认 style 的 TypeError）
    #   旧代码硬传 style= 给所有 image_generation，Z-Image 签名没有 style 直接炸
    import inspect as _inspect
    sig = _inspect.signature(method)
    accepted = set(sig.parameters.keys()) - {'self', 'model_id'}

    if model['type'] == 'image_generation':
        kwargs = {}
        if 'prompt' in accepted:
            kwargs['prompt'] = params.get('prompt', '')
        # 通用数值/文本参数：签名里有才传（width/height/steps/seed/style/size/negative_prompt...）
        for key in ('width', 'height', 'steps', 'seed', 'style', 'negative_prompt', 'guidance_scale'):
            if key in accepted and key in params:
                kwargs[key] = params[key]
        # 参数名别名：size -> width/height（老 SD 面板）
        if 'size' in accepted and 'size' in params and 'width' not in kwargs:
            kwargs['size'] = params['size']
        result = method(model_id, **kwargs)
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

# ═══════════════════════════════════════════════════════════════
# ★ 2026-09-15 开发者令#1: 附件通道 —— 图片直传 / 视频ffmpeg抽帧 / 音频faster-whisper转写 / 文章抽文
# ═══════════════════════════════════════════════════════════════

_WHISPER_MODEL = None  # 懒加载单例（base 模型 ~145MB，首次用时下载）


_IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')
_VID_EXTS = ('.mp4', '.webm', '.mkv', '.mov', '.avi')
_AUD_EXTS = ('.mp3', '.wav', '.m4a', '.flac', '.ogg')


def _dataurl_to_bytes(dataurl: str, ext: str = '.bin'):
    """dataURL → (bytes, 后缀)；纯 base64（无前缀）也兼容。ext 优先用文件名后缀"""
    import base64, re as _re
    m = _re.match(r'^data:[^;]+;base64,(.+)$', dataurl or '', _re.S)
    raw = base64.b64decode(m.group(1) if m else dataurl)
    return raw, ext or '.bin'


def _video_to_frame_b64s(raw: bytes, ext: str, max_frames: int = 6):
    """视频 → 均匀抽帧（ffmpeg，最长取前5分钟，缩放到宽640）→ base64 列表"""
    import base64, os, subprocess, tempfile, shutil
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('电脑上没有 ffmpeg，看不了视频（可到 winget 安装：winget install Gyan.FFmpeg）')
    tmpdir = tempfile.mkdtemp(prefix='hv_vid_')
    try:
        src = os.path.join(tmpdir, f'in{ext}')
        with open(src, 'wb') as f:
            f.write(raw)
        # 取时长（ffprobe 失败就默认前 60 秒）
        duration = 60.0
        try:
            probe = subprocess.run(
                [ffmpeg.replace('ffmpeg', 'ffprobe'), '-v', 'error', '-show_entries',
                 'format=duration', '-of', 'csv=p=0', src],
                capture_output=True, text=True, timeout=30)
            duration = min(float(probe.stdout.strip()), 300.0)
        except Exception:
            pass
        b64s = []
        step = max(duration / max_frames, 0.5)
        for i in range(max_frames):
            ts = min(i * step, max(duration - 0.1, 0))
            out = os.path.join(tmpdir, f'f{i}.jpg')
            rr = subprocess.run(
                [ffmpeg, '-y', '-ss', f'{ts:.1f}', '-i', src, '-frames:v', '1',
                 '-vf', 'scale=640:-2', '-q:v', '5', out],
                capture_output=True, timeout=60)
            if rr.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 500:
                with open(out, 'rb') as fimg:
                    b64s.append(base64.b64encode(fimg.read()).decode())
        if not b64s:
            raise RuntimeError('视频抽帧失败：格式可能不支持或文件损坏')
        return b64s
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _transcribe_audio(raw: bytes, ext: str) -> str:
    """音频 → faster-whisper 本地转写文本（模型走 ModelScope 国内直连下载，~138MB）"""
    import os, tempfile
    global _WHISPER_MODEL
    # ★ 2026-09-15 实测：系统代理（127.0.0.1:4720）会把外网下载打断 → 禁代理；
    #   HF 官方/镜像在本机网络全堵（502/SSL断/直连超时）→ 改走 ModelScope（哈文模型安装器同款通道）
    for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        os.environ.pop(_k, None)
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError('本地还没有听音模型（faster-whisper 未安装），暂时听不了音频')
    tmpdir = tempfile.mkdtemp(prefix='hv_aud_')
    try:
        src = os.path.join(tmpdir, f'in{ext}')
        with open(src, 'wb') as f:
            f.write(raw)
        if _WHISPER_MODEL is None:
            _model_src = 'base'  # 兜底：HF 路径（有代理的用户能下）
            try:
                from modelscope import snapshot_download
                _model_src = snapshot_download('pengzhendong/faster-whisper-base')
            except Exception as me:
                logger.warning(f"[Vertical] ModelScope 下载听音模型失败，回退 HF: {me}")
            _WHISPER_MODEL = WhisperModel(_model_src, device='cpu', compute_type='int8')
        segments, _info = _WHISPER_MODEL.transcribe(src, language='zh', beam_size=3)
        text = ''.join(s.text for s in segments).strip()
        return text or '（音频里没有听清可识别的语音内容）'
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_document_text(raw: bytes, name: str) -> str:
    """文章/文档 → 纯文本（txt/md 直接读，docx 用 python-docx，pdf 用 pypdf）"""
    lower = (name or '').lower()
    if lower.endswith(('.docx',)):
        import io, docx
        d = docx.Document(io.BytesIO(raw))
        return '\n'.join(p.text for p in d.paragraphs if p.text.strip())
    if lower.endswith(('.pdf',)):
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return '\n'.join((page.extract_text() or '') for page in reader.pages[:30])
    return raw.decode('utf-8', errors='replace')


def _process_attachments(atts):
    """附件列表 → (图片base64列表, 附加文本)。图片/视频→图，音频→转写文本，文章→文本

    ★ 类型判定：先看文件名后缀，再看 MIME —— 修"txt 被误判成图片"（2026-09-15 测试抓到）
    """
    import os as _os
    images, texts = [], []
    for att in atts:
        if not isinstance(att, dict):
            continue
        name = att.get('name', '附件')
        mime = (att.get('type') or att.get('mime') or '').lower()
        dataurl = att.get('dataUrl') or att.get('data') or ''
        if not dataurl:
            continue
        suffix = _os.path.splitext(name or '')[1].lower()
        if suffix in _IMG_EXTS or mime.startswith('image/'):
            import base64, re as _re
            m = _re.match(r'^data:[^;]+;base64,(.+)$', dataurl, _re.S)
            images.append(m.group(1) if m else dataurl)  # 已是裸 base64
        elif suffix in _VID_EXTS or mime.startswith('video/'):
            raw, ext = _dataurl_to_bytes(dataurl, suffix or '.mp4')
            frames = _video_to_frame_b64s(raw, ext)
            images.extend(frames)
            texts.append(f'\n\n[附件·视频 {name}：已抽 {len(frames)} 帧，按时间顺序排列]')
        elif suffix in _AUD_EXTS or mime.startswith('audio/'):
            raw, ext = _dataurl_to_bytes(dataurl, suffix or '.mp3')
            t = _transcribe_audio(raw, ext)
            texts.append(f'\n\n[附件·音频 {name} 的转写内容：\n{t[:4000]}]')
        else:
            raw, _ = _dataurl_to_bytes(dataurl)
            t = _extract_document_text(raw, name)
            texts.append(f'\n\n[附件·文章 {name} 的内容：\n{t[:8000]}]')
    return images, ''.join(texts)


def _execute_via_ollama(model, params, images=None, extra_text=''):
    """通过Ollama执行垂直模型（如华佗医疗模型）

    ★ 2026-09-15: 支持多模态 —— images 为 base64 列表（不带 data: 前缀，Ollama 原生 images 字段），
      extra_text 为附件转写文本（音频转写/文章内容），拼在用户输入后面。
    """
    import requests
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    question = params.get('question', '') or params.get('prompt', '')
    if not question and not (images or extra_text):
        return {"error": "请输入问题描述", "output": ""}
    if not question:
        question = '请分析我发来的内容。'

    ollama_model = model.get('ollama_model', model.get('id', ''))
    # ★ 2026-09-14: 按模型分流系统提示词（医疗提示词会带偏 minicpm-v 这类视觉/通用模型）
    if 'huatuo' in ollama_model.lower() or (model.get('type') == 'medical'):
        system_prompt = "你是一个专业的医疗AI助手，请基于医学知识回答用户的健康问题。回答要准确、专业、易懂。如果问题超出你的能力范围，请建议用户咨询专业医生。"
    else:
        system_prompt = f"你是{model.get('name', ollama_model)}，一个专项AI模型，请直接、准确地回答用户输入的问题。"

    user_content = question + (extra_text or '')
    user_msg = {'role': 'user', 'content': user_content}
    if images:
        user_msg['images'] = images  # Ollama 原生多模态字段（base64，不带前缀）

    try:
        r = requests.post(
            'http://localhost:11434/api/chat',
            proxies={'http': None, 'https': None},  # ★ 禁代理：系统代理会把 localhost 请求发走导致 502
            json={
                'model': ollama_model,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    user_msg
                ],
                'stream': False,
                'options': {'temperature': 0.7, 'num_predict': 2048}
            },
            timeout=300  # ★ 2026-09-15: 视频抽帧多图/长文理解耗时更长，120→300
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

# ★ 2026-09-14 用户反馈：垂直大脑只显示实际能用的模型——有哪个显示哪个，没有的不显示
# 可用性缓存（30 秒）：Ollama 清单查询避免每次打开页面都打一次接口
_avail_cache = {'ts': 0.0, 'ollama_names': None}


def _ollama_model_names() -> set:
    """查 Ollama 已装模型名集合（短缓存 30 秒；查询失败=空集，宁可不显示也不骗人）"""
    import time
    import urllib.request
    now = time.time()
    if _avail_cache['ollama_names'] is not None and now - _avail_cache['ts'] < 30:
        return _avail_cache['ollama_names']
    names = set()
    try:
        req = urllib.request.Request('http://127.0.0.1:11434/api/tags')
        # ★ 禁代理：系统 http_proxy 会把 127.0.0.1 请求发去代理导致 502
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as resp:
            for m in json.loads(resp.read().decode('utf-8')).get('models', []):
                names.add((m.get('name') or '').split(':')[0].lower())
    except Exception as e:
        logger.warning(f"[Vertical] Ollama 可用性检测失败（Ollama 未跑或未装）: {e}")
    _avail_cache['ts'] = now
    _avail_cache['ollama_names'] = names
    return names


def _model_available(m: dict) -> bool:
    """内置模型的实际可用性判断。假模型 / 未安装的一律返回 False（不进列表）"""
    eng = m.get('engine')
    if eng == 'ollama':
        return (m.get('ollama_model') or '').split(':')[0].lower() in _ollama_model_names()
    if eng == 'tool_executor':
        method = m.get('executor_method')
        if method == 'run_image_gen':
            return False  # SD 执行器是"未安装"占位（tool_executor.run_image_gen 写死返回失败），从未实装
        if method == 'run_bark':
            return False  # Bark 同为占位实现
        if method == 'run_musicgen':
            return False  # ★ 2026-11-14 MusicGen 已卸载（被 ACE-Step 替代），执行器已删除
        if method in ('run_ace_step', 'run_zimage', 'run_wan22'):
            # ★ 2026-11-14: 模型文件在 H:\AI_Models 即可用（脚本在缺一不可）
            # ★ Wan2.2 暂不可用：GGUF 双专家 + enable_model_cpu_offload 在 Windows 触发 C 层 segfault，
            #   待专项解决后把下面这行删掉即可上线
            if method == 'run_wan22':
                return False
            import glob as _glob
            checks = {
                'run_ace_step': [r'H:\AI_Models\acestep_gen.py', r'H:\AI_Models\ACE-Step-v1-3.5B\ace_step_transformer\*.safetensors'],
                'run_zimage': [r'H:\AI_Models\zimage_gen.py', r'H:\AI_Models\Z-Image-Turbo\transformer\*.safetensors'],
                'run_wan22': [r'H:\AI_Models\wan22_gen.py', r'H:\AI_Models\Wan2.2-T2V-A14B-GGUF\HighNoise\*.gguf'],
            }
            for pat in checks.get(method, []):
                if not (os.path.exists(pat) if '*' not in pat else _glob.glob(pat)):
                    return False
            return True
        return True  # run_discovered_model 等来自实际扫描结果，视为可用
    return True


# ═══════════════════════════════════════════════════════════════
# ★ #1560（2026-11-27）：底座部件直答 —— registry 里的 harven.base.* 升格垂直页一等公民
#   mnv4（快速分类）/ locate（目标定位）传图直答；engine='base_engine'
#   C 部分：进了 /api/vertical/models 列表后，模型中心分区三自动可见
# ═══════════════════════════════════════════════════════════════

def _base_engine_entries() -> list:
    """底座部件 → 垂直页直答条目。mnv4 需权重在盘；locate 服务可后启（报错带启动指引）。"""
    entries = []
    try:
        from src.vision.engines.mnv4_engine import WEIGHTS as _MN4W
        if not _MN4W.exists():
            return []   # 没装 MobileNetV4 就别占卡片
    except Exception:
        return []
    entries.append({
        "id": "harven.base.mnv4",
        "name": "⚡ 快速分类",
        "icon": "⚡",
        "license": "Apache 2.0",
        "type": "vision",
        "canvas_type": "generic",
        "description": "传一张图 → 毫秒级告诉你图里是什么（Top5 类别+置信度，MobileNetV4）",
        "engine": "base_engine",
        "base_engine": "mnv4",
        "capabilities": ["classify"],
        "capability": {
            "can_do": ["毫秒级图片分类（ImageNet 1000 类）", "返回 Top5 类别+置信度"],
            "cannot_do": ["说出图中物体的具体位置", "看懂文字/场景描述", "生成图片"],
            "advisor_tips": "它是「反射弧」档：快但糙。想要描述图里发生了什么，用看图理解类模型；想找东西在哪，用 🎯 目标定位。",
        },
        "params": {
            "topk": {"type": "range", "label": "返回类别数", "default": 5, "min": 1, "max": 10, "step": 1},
        },
    })
    entries.append({
        "id": "harven.base.locate",
        "name": "🎯 目标定位",
        "icon": "🎯",
        "license": "NVIDIA 非商用研究用途",
        "license_warning": "⚠️ LocateAnything-3B 为 NVIDIA 非商用研究许可，商用受限",
        "type": "vision",
        "canvas_type": "generic",
        "description": "传图 + 说找什么 → 框出目标位置（像素坐标+标注图，LocateAnything-3B）",
        "engine": "base_engine",
        "base_engine": "locate",
        "show_image_result": True,   # 结果区显示标注图
        "capabilities": ["locate"],
        "capability": {
            "can_do": ["按描述框出图中目标（返回像素坐标框/点）", "在原图上画出标注框"],
            "cannot_do": ["生成/修改图片", "识别人脸身份", "读文字内容（OCR 请用哈文拍照识别）"],
            "advisor_tips": "目标用简短名词（如「红绿灯」「dog」「left person」）。首次执行需先启动服务：双击 H:\\venvs\\locate\\start_locate_service.bat",
        },
        "params": {
            "target": {"type": "text", "label": "找什么", "default": "", "placeholder": "如：红绿灯 / person / the left cup", "required": True},
        },
    })
    return entries


def _base_engine_model(model_id: str):
    """按 id 取底座部件条目（layout/execute 查找共用）。"""
    return next((m for m in _base_engine_entries() if m["id"] == model_id), None)


def _first_image_attachment_file(attachments: list) -> str:
    """附件列表第一张图 → 落临时文件返回路径（无图抛 ValueError）。"""
    import base64 as _b64
    import re as _re
    import tempfile as _tempfile
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        dataurl = att.get("dataUrl") or att.get("data") or ""
        if not dataurl:
            continue
        suffix = Path(att.get("name") or "img.jpg").suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            mime = (att.get("type") or att.get("mime") or "").lower()
            if not mime.startswith("image/"):
                continue
            suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
        m = _re.match(r"^data:[^;]+;base64,(.+)$", dataurl, _re.S)
        raw = _b64.b64decode(m.group(1) if m else dataurl)
        fd, tmp = _tempfile.mkstemp(suffix=suffix)
        import os as _os
        _os.close(fd)
        Path(tmp).write_bytes(raw)
        return tmp
    raise ValueError("请先在「附上文件」里选一张图片")


def _execute_base_engine(model: dict, params: dict, attachments: list) -> dict:
    """底座部件直答执行：mnv4 分类 / locate 定位（标注图落 data/media）。"""
    which = model.get("base_engine", "")
    try:
        image_path = _first_image_attachment_file(attachments)
    except ValueError as ve:
        return {"output": "", "error": str(ve)}
    except Exception as e:
        return {"output": "", "error": f"图片读不出来: {e}"}

    if which == "mnv4":
        try:
            from src.vision.engines.mnv4_engine import Mnv4Engine
            eng = Mnv4Engine()
            eng.load()
            r = eng.infer(image_path, topk=int(params.get("topk") or 5))
            lines = [f"识别结果（{r.elapsed_ms}ms）："]
            for i, c in enumerate(r.data.get("topk") or [], 1):
                lines.append(f"{i}. {c['label']}  {round(c['prob'] * 100, 1)}%")
            return {"output": "\n".join(lines),
                    "top1": r.data.get("top1", ""),
                    "topk": r.data.get("topk") or [],
                    "elapsed_ms": r.elapsed_ms}
        except FileNotFoundError as fe:
            return {"output": "", "error": str(fe)}
        except Exception as e:
            return {"output": "", "error": f"分类执行失败: {e}"}

    if which == "locate":
        try:
            from src.vision.engines.locate_engine import LocateHttpEngine
            eng = LocateHttpEngine()
            eng.load()   # 服务未启动 → RuntimeError 带启动指引
        except RuntimeError as re_:
            return {"output": "", "error": str(re_)}
        target = (params.get("target") or "").strip() or "the main object"
        try:
            r = eng.infer({"image_path": image_path, "prompt": target, "mode": "ground_multi"})
        except RuntimeError as re_:
            return {"output": "", "error": str(re_)}
        except Exception as e:
            return {"output": "", "error": f"定位执行失败: {e}"}
        d = r.data or {}
        boxes, points = d.get("boxes") or [], d.get("points") or []
        w, h = d.get("width") or 0, d.get("height") or 0
        lines = [f"找「{target}」："]
        if boxes:
            lines.append(f"框到 {len(boxes)} 处（图 {w}×{h}）：")
            for i, b in enumerate(boxes, 1):
                if isinstance(b, (list, tuple)) and len(b) == 4:
                    lines.append(f"  框{i}: x={round(b[0])} y={round(b[1])} → x={round(b[2])} y={round(b[3])}")
                else:
                    lines.append(f"  框{i}: {b}")
        elif points:
            lines.append(f"点到 {len(points)} 处（图 {w}×{h}）：")
            for i, p in enumerate(points[:10], 1):
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    lines.append(f"  点{i}: x={round(p[0])} y={round(p[1])}")
                else:
                    lines.append(f"  点{i}: {p}")
        else:
            lines.append("没找到（试试换更短更具体的词，如「traffic light」「person」）")
        # 标注图落 data/media（前端可显示）
        file_rel = ""
        try:
            from PIL import Image as _PILImage, ImageDraw as _PILDraw
            img = _PILImage.open(image_path).convert("RGB")
            draw = _PILDraw.Draw(img)
            for b in boxes:
                if isinstance(b, (list, tuple)) and len(b) == 4:
                    draw.rectangle([b[0], b[1], b[2], b[3]], outline=(255, 60, 40), width=3)
            for p in points:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    draw.ellipse([p[0] - 6, p[1] - 6, p[0] + 6, p[1] + 6], fill=(255, 60, 40))
            media_dir = Path(__file__).parent.parent.parent / "data" / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            import time as _time
            dst = media_dir / f"locate_{int(_time.time())}.jpg"
            img.save(str(dst), quality=92)
            file_rel = f"data/media/{dst.name}"
        except Exception as e:
            logger.warning(f"[Vertical] 定位标注图保存失败（不影响文本结果）: {e}")
        out = {"output": "\n".join(lines), "target": target,
               "boxes": boxes, "points": points, "elapsed_ms": r.elapsed_ms}
        if file_rel:
            out["file"] = file_rel
        return out

    return {"output": "", "error": f"未知底座部件: {which}"}


def _get_all_models():
    """获取全部模型列表（内置 + 动态 + 自动发现的本地模型）"""
    # ★ A批A2: 伪模型（pseudo=True）不进垂直列表，垂直列表只剩真模型
    # ★ 2026-09-14: 只显示实际可用的——SD/Bark 占位假模型、Ollama 里没装的、HF 缓存缺的都不再出现
    all_models = [m for m in _BUILTIN_MODELS if not m.get('pseudo') and _model_available(m)]

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
            # ★ 2026-09-14 用户反馈#4：Ollama 来源的发现模型走 Ollama 引擎执行
            #   （原逻辑一律塞给 transformers，但 Ollama 模型没有本地 path → "缺少模型路径" 必报错）
            if dm.get('source') == 'ollama' or dm.get('loader') == 'ollama':
                model_entry = {
                    'id': f"discovered_{dm['id'].replace('/', '_').replace(':', '_')}",
                    'name': f"{dm.get('icon','📦')} {dm['name']}",
                    'icon': dm.get('icon', '📦'),
                    'type': dm.get('category', 'general'),
                    'canvas_type': 'generic',
                    'description': f"自动发现: {dm['id']} · Ollama 运行中，点开即用",
                    'engine': 'ollama',
                    'ollama_model': dm['id'],  # 原始模型名（minicpm-v:8b），不带 discovered_ 前缀
                    'source': 'ollama',
                    'is_discovered': True,
                    'params': {
                        'prompt': {'type': 'textarea', 'label': '输入', 'default': '', 'required': True},
                    },
                }
                all_models.append(model_entry)
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

    # ★ #1560: 底座部件直答（mnv4/locate）——垂直页一等公民，模型中心分区三自动可见
    try:
        _have = {m["id"] for m in all_models}
        for be in _base_engine_entries():
            if be["id"] not in _have:
                all_models.append(be)
    except Exception as e:
        logger.warning(f"[Vertical] 底座部件条目装载失败: {e}")

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


# ═══════════════════════════════════════════════════════════════
# ★ #1557 模型体检 + #1558 接入包（2026-11-26）
# ═══════════════════════════════════════════════════════════════

@bp.route('/checkup', methods=['GET'])
def model_checkup():
    """模型体检：扫描本地模型目录 → 三态报告（只读零动手）。"""
    try:
        from src.core.model_checkup import run_checkup
        return jsonify(run_checkup())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 500


_PKG_DIR = Path(__file__).parent.parent.parent / "data" / "model_packages"
_PKG_ARCHS = ("timm_classify", "transformers_vl", "gguf_vision", "http_service", "builtin")
_PKG_CHANNELS = ("internal", "llama_cpp", "ollama", "cloud")


@bp.route('/packages', methods=['GET'])
def list_model_packages():
    """列出已导入的接入包 manifest。"""
    pkgs = []
    for p in sorted(_PKG_DIR.glob("*.json")) if _PKG_DIR.is_dir() else []:
        try:
            pkgs.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as _e:
            logger.debug(f"[vertical_api] 忽略: {_e}")
    return jsonify({"packages": pkgs, "count": len(pkgs)})


@bp.route('/packages/import', methods=['POST'])
def import_model_package():
    """导入接入包 manifest（#1558 下载=接入）。

    请求体: manifest JSON（或 {manifest: {...}}）
    校验：manifest_version=1 / 必填字段 / 架构与通道在白名单；
    依赖：逐项检测可导入性，缺失的列 missing_deps 返回（不自动 pip install——
    版本钉死的走独立 venv，由用户/哈文按 service 模板搭，避免砸主 venv）。
    落盘：data/model_packages/<id>.json → registry 动态加载（30s 缓存内 force 刷新）。
    """
    body = request.get_json(force=True, silent=True) or {}
    mf = body.get("manifest") or body
    try:
        if int(mf.get("manifest_version", 0)) != 1:
            return jsonify({"ok": False, "error": "manifest_version 必须为 1"}), 400
        for k in ("id", "name", "architecture", "channel", "capabilities"):
            if not mf.get(k):
                return jsonify({"ok": False, "error": f"缺必填字段: {k}"}), 400
        if mf["architecture"] not in _PKG_ARCHS:
            return jsonify({"ok": False, "error":
                            f"architecture 须为 {'/'.join(_PKG_ARCHS)}"}), 400
        if mf["channel"] not in _PKG_CHANNELS:
            return jsonify({"ok": False, "error":
                            f"channel 须为 {'/'.join(_PKG_CHANNELS)}"}), 400
        if not isinstance(mf.get("capabilities"), list) or not mf["capabilities"]:
            return jsonify({"ok": False, "error": "capabilities 须为非空列表"}), 400
        # 依赖检测（只读，不自动装）
        installed, missing = [], []
        deps = mf.get("deps") or {}
        if isinstance(deps, dict):
            deps = list(deps.keys())
        try:
            import importlib.util
            _imp = {"timm": "timm", "torch": "torch", "transformers": "transformers",
                    "pillow": "PIL", "diffusers": "diffusers", "peft": "peft",
                    "opencv": "cv2", "lmdb": "lmdb", "decord": "decord",
                    "torchvision": "torchvision"}
            for d in deps:
                mod = _imp.get(str(d).lower(), str(d))
                (installed if importlib.util.find_spec(mod) else missing).append(d)
        except Exception:
            pass
        # http_service 必须带 service 段
        if mf["architecture"] == "http_service" and not mf.get("service"):
            return jsonify({"ok": False, "error":
                            "http_service 架构必须带 service 段（base_url/start_bat）"}), 400
        # 落盘
        _PKG_DIR.mkdir(parents=True, exist_ok=True)
        slug = str(mf["id"]).replace("/", "_").replace(":", "_")
        out = _PKG_DIR / f"{slug}.json"
        out.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
        # 刷 registry 缓存
        try:
            from src.core.chain_engine.registry import get_registry
            get_registry().list_models(force=True)
        except Exception as _e:
            logger.debug(f"[vertical_api] 忽略: {_e}")
        return jsonify({"ok": True, "saved": str(out), "registered_id": mf["id"],
                        "deps_installed": installed, "deps_missing": missing,
                        "note": ("缺依赖: " + ", ".join(missing)) if missing
                        else "依赖齐，模型已登记（工坊能力表立即可见）"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 500


@bp.route('/packages/<pkg_id>', methods=['DELETE'])
def delete_model_package(pkg_id):
    """删除接入包（只删 manifest 户口，不动模型文件）。"""
    slug = pkg_id.replace("/", "_").replace(":", "_")
    p = _PKG_DIR / f"{slug}.json"
    if not p.is_file():
        return jsonify({"ok": False, "error": "接入包不存在"}), 404
    p.unlink()
    try:
        from src.core.chain_engine.registry import get_registry
        get_registry().list_models(force=True)
    except Exception as _e:
        logger.debug(f"[vertical_api] 忽略: {_e}")
    return jsonify({"ok": True, "deleted": pkg_id})


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
