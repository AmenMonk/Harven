# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
哈文模型自动分类器
扫描本地模型 → 读config.json+README → 自动分类 → 注册到discovered_models.json
"""

import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
DISCOVERED_FILE = BASE_DIR / "data" / "discovered_models.json"

# 架构 → 分类映射
ARCH_CLASSIFY = {
    # 通用对话
    'qwen2': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    'qwen': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    'llama': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    'mistral': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    'gemma': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    'phi': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    'yi': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    'chatglm': {'category': 'general', 'loader': 'transformers.AutoModelForCausalLM', 'icon': '🧠'},
    # 语音
    'whisper': {'category': 'voice', 'loader': 'transformers.AutoModelForSpeechSeq2Seq', 'icon': '🎤'},
    # 音乐
    'musicgen': {'category': 'music', 'loader': 'audiocraft', 'icon': '🎵'},
    # 图片
    'stable-diffusion': {'category': 'image', 'loader': 'diffusers.StableDiffusionPipeline', 'icon': '🎨'},
    'sd': {'category': 'image', 'loader': 'diffusers.StableDiffusionPipeline', 'icon': '🎨'},
}

# README关键词 → 垂直分类
README_KEYWORDS = {
    'medical': {'category': 'medical', 'icon': '🏥', 'keywords': ['medical', '华佗', '医疗', 'huatuo', 'health', 'clinical', 'doctor']},
    'code': {'category': 'code', 'icon': '💻', 'keywords': ['code', '代码', 'coding', 'programmer', 'developer']},
    'translate': {'category': 'translate', 'icon': '🌐', 'keywords': ['translate', '翻译', 'translation', 'bilingual']},
    'legal': {'category': 'legal', 'icon': '⚖️', 'keywords': ['legal', '法律', 'law', 'attorney']},
    'finance': {'category': 'finance', 'icon': '💰', 'keywords': ['finance', '金融', 'financial', 'stock', 'accounting']},
    'education': {'category': 'education', 'icon': '📚', 'keywords': ['education', '教育', 'teaching', 'student', 'tutor']},
}


def classify_model(model_info):
    """根据config.json+README分类模型"""
    arch = model_info.get('architecture', '').lower()
    model_type = model_info.get('model_type', '').lower()
    readme_text = model_info.get('readme_first_line', '').lower()
    model_name = model_info.get('name', '').lower()

    # 1. 先按架构分类
    for key, classify in ARCH_CLASSIFY.items():
        if key in arch or key in model_type:
            result = {
                'category': classify['category'],
                'loader': classify['loader'],
                'icon': classify['icon'],
                'architecture': arch,
            }
            # 2. 如果是通用对话模型，再检查README是否是垂直领域
            if classify['category'] == 'general':
                for vk_name, vk_info in README_KEYWORDS.items():
                    for kw in vk_info['keywords']:
                        if kw in readme_text or kw in model_name:
                            result['category'] = vk_info['category']
                            result['icon'] = vk_info['icon']
                            result['subcategory'] = vk_name
                            break
                    if result.get('subcategory'):
                        break
            return result

    # 3. 检查是否有diffusers配置（图片模型）
    model_path = model_info.get('path', '')
    if model_path and os.path.isdir(model_path):
        files = os.listdir(model_path) if os.path.isdir(model_path) else []
        if 'unet' in files or 'scheduler' in files or 'vae' in files:
            return {
                'category': 'image',
                'loader': 'diffusers.StableDiffusionPipeline',
                'icon': '🎨',
                'architecture': arch,
            }
        # 检查是否有Modelfile（Ollama格式）
        if 'Modelfile' in files:
            return {
                'category': 'general',
                'loader': 'ollama',
                'icon': '🦙',
                'architecture': arch,
            }

    # 4. 默认分类为通用
    return {
        'category': 'general',
        'loader': 'transformers.AutoModelForCausalLM',
        'icon': '🧠',
        'architecture': arch or 'unknown',
    }


def read_model_info(model_path):
    """读取模型的config.json和README"""
    info = {'path': model_path}
    files = os.listdir(model_path) if os.path.isdir(model_path) else []

    # 读config.json
    config_path = os.path.join(model_path, 'config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            info['architecture'] = cfg.get('architectures', [''])[0]
            info['model_type'] = cfg.get('model_type', '')
            info['hidden_size'] = cfg.get('hidden_size', 0)
        except Exception:
            info['architecture'] = ''
            info['model_type'] = ''

    # 读README第一行
    readme_path = os.path.join(model_path, 'README.md')
    if os.path.exists(readme_path):
        try:
            with open(readme_path, 'r', encoding='utf-8', errors='ignore') as f:
                info['readme_first_line'] = f.readline().strip()
        except Exception:
            info['readme_first_line'] = ''

    return info


def auto_classify_and_register():
    """扫描所有本地模型 → 分类 → 注册到discovered_models.json"""
    from src.core.system_knowledge import scan_all

    logger.info("[AutoClassifier] 开始自动分类注册...")

    # 获取系统知识
    knowledge = scan_all()

    discovered = []

    # 处理ModelScope模型
    for m in knowledge.get('modelscope_models', []):
        model_info = {
            'id': m['id'],
            'name': m['name'],
            'path': m['path'],
            'size_gb': m.get('size_gb', 0),
            'source': 'modelscope',
        }
        # 读取详细信息
        extra = read_model_info(m['path'])
        model_info.update(extra)

        # 分类
        classification = classify_model(model_info)
        model_info.update(classification)

        discovered.append(model_info)
        logger.info(f"[AutoClassifier] {m['id']} → {classification['category']} ({classification['icon']})")

    # 处理HuggingFace模型
    for m in knowledge.get('huggingface_models', []):
        model_info = {
            'id': m['id'],
            'name': m['name'],
            'path': m['path'],
            'source': 'huggingface',
        }
        extra = read_model_info(m['path'])
        model_info.update(extra)

        classification = classify_model(model_info)
        model_info.update(classification)

        discovered.append(model_info)
        logger.info(f"[AutoClassifier] {m['id']} → {classification['category']} ({classification['icon']})")

    # 处理Ollama模型
    for m in knowledge.get('ollama_models', []):
        model_info = {
            'id': m['name'],
            'name': m['name'],
            'source': 'ollama',
            'category': 'general',
            'loader': 'ollama',
            'icon': '🦙',
        }
        # 检查Ollama模型类型
        name_lower = m['name'].lower()
        if 'minicpm' in name_lower or 'llava' in name_lower:
            model_info['category'] = 'vision'
            model_info['icon'] = '👁️'
        elif 'embed' in name_lower:
            model_info['category'] = 'embedding'
            model_info['icon'] = '📐'
        elif 'coder' in name_lower or 'code' in name_lower:
            model_info['category'] = 'code'
            model_info['icon'] = '💻'

        discovered.append(model_info)
        logger.info(f"[AutoClassifier] {m['name']} → {model_info['category']} ({model_info['icon']})")

    # 保存
    result = {
        '_meta': {
            'version': '1.0',
            'updated': knowledge.get('scan_time', ''),
            'total': len(discovered),
        },
        'models': discovered,
    }

    try:
        DISCOVERED_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DISCOVERED_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"[AutoClassifier] 已保存 {len(discovered)} 个模型到 {DISCOVERED_FILE}")
    except Exception as e:
        logger.error(f"[AutoClassifier] 保存失败: {e}")

    return result


def get_discovered_models(category=None):
    """获取已发现的模型列表（供vertical_api.py调用）"""
    if not DISCOVERED_FILE.exists():
        return []

    try:
        with open(DISCOVERED_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        models = data.get('models', [])
        if category:
            models = [m for m in models if m.get('category') == category]
        return models
    except Exception:
        return []


def auto_classify_async():
    """后台异步分类"""
    import threading

    def _run():
        try:
            auto_classify_and_register()
        except Exception as e:
            logger.error(f"[AutoClassifier] 异步分类失败: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    logger.info("[AutoClassifier] 后台分类已启动")


if __name__ == '__main__':
    result = auto_classify_and_register()
    print(f"\n分类完成: {result['_meta']['total']} 个模型")
    for m in result['models']:
        print(f"  {m.get('icon','')} {m['id']:40} → {m.get('category',''):10} ({m.get('loader','')})")
