# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI 系统知识自检模块
启动时自动扫描已安装库/模型，注入到提示词让哈文知道自己有什么
"""

import os
import json
import logging
import importlib
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

KNOWLEDGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'system_knowledge.json')

# 要检查的库
CHECK_LIBS = [
    'modelscope', 'transformers', 'torch', 'flask', 'faiss', 'requests',
    'PIL', 'yaml', 'openai', 'numpy', 'pandas', 'openai_whisper',
    'faster_whisper', 'diffusers', 'accelerate', 'soundfile', 'scipy',
    'audiocraft', 'TTS', 'cv2', 'PyPDF2', 'docx', 'pptx',
]


def scan_installed_libs():
    """扫描已安装的库"""
    libs = {}
    for lib_name in CHECK_LIBS:
        try:
            mod = importlib.import_module(lib_name)
            version = getattr(mod, '__version__', 'unknown')
            libs[lib_name] = version
        except ImportError as _e:
            logger.debug(f"[system_knowledge] 忽略: {_e}")
        except Exception as _e: logger.debug(f"[system_knowledge] 忽略: {_e}")
    return libs


def scan_modelscope_cache():
    """扫描ModelScope缓存"""
    models = []
    cache_dir = os.path.expanduser('~/.cache/modelscope/hub/models')
    if not os.path.isdir(cache_dir):
        return models

    try:
        for author in os.listdir(cache_dir):
            author_dir = os.path.join(cache_dir, author)
            if not os.path.isdir(author_dir):
                continue
            for model_name in os.listdir(author_dir):
                model_path = os.path.join(author_dir, model_name)
                if not os.path.isdir(model_path):
                    continue

                files = os.listdir(model_path)
                has_safetensors = any(f.endswith('.safetensors') for f in files)
                has_bin = any(f.endswith('.bin') for f in files)
                has_config = 'config.json' in files

                if not (has_safetensors or has_bin or has_config):
                    continue

                # 计算大小
                total_size = 0
                try:
                    for f in files:
                        fp = os.path.join(model_path, f)
                        if os.path.isfile(fp):
                            total_size += os.path.getsize(fp)
                except Exception as _e: logger.debug(f"[system_knowledge] 忽略: {_e}")

                # 读config
                arch = ''
                model_type = ''
                config_path = os.path.join(model_path, 'config.json')
                if os.path.exists(config_path):
                    try:
                        with open(config_path, 'r', encoding='utf-8') as f:
                            cfg = json.load(f)
                        arch = cfg.get('architectures', [''])[0]
                        model_type = cfg.get('model_type', '')
                    except Exception as _e: logger.debug(f"[system_knowledge] 忽略: {_e}")

                models.append({
                    'id': f'{author}/{model_name}',
                    'name': model_name,
                    'path': model_path.replace('\\', '/'),
                    'size_gb': round(total_size / 1024 / 1024 / 1024, 1),
                    'architecture': arch,
                    'model_type': model_type,
                    'source': 'modelscope',
                })
    except Exception as e:
        logger.warning(f"[Knowledge] ModelScope扫描失败: {e}")

    return models


def scan_huggingface_cache():
    """扫描HuggingFace缓存"""
    models = []
    cache_dir = os.path.expanduser('~/.cache/huggingface/hub')
    if not os.path.isdir(cache_dir):
        return models

    try:
        for d in os.listdir(cache_dir):
            if not d.startswith('models--'):
                continue
            parts = d.split('--')
            if len(parts) < 3:
                continue
            author = parts[1]
            model_name = parts[2]

            snapshot_dir = os.path.join(cache_dir, d, 'snapshots')
            if not os.path.isdir(snapshot_dir):
                continue

            snapshots = os.listdir(snapshot_dir)
            if not snapshots:
                continue

            # ★ 2026-07-12: 遍历所有snapshot，找到有文件的
            for snap_name in snapshots:
                snapshot_path = os.path.join(snapshot_dir, snap_name)
                if not os.path.isdir(snapshot_path):
                    continue

                files = os.listdir(snapshot_path)
                if not files:
                    continue  # 空目录跳过，检查下一个

                has_safetensors = any(f.endswith('.safetensors') for f in files)
                has_config = 'config.json' in files
                has_gguf = any(f.endswith('.gguf') for f in files)

                if has_safetensors or has_config or has_gguf:
                    gguf_files = [f for f in files if f.endswith('.gguf')]
                    model_display = model_name
                    if gguf_files:
                        model_display = f'{model_name} ({gguf_files[0]})'
                    models.append({
                        'id': f'{author}/{model_name}',
                        'name': model_display,
                        'path': snapshot_path.replace('\\', '/'),
                        'source': 'huggingface',
                        'has_gguf': has_gguf,
                    })
                    break  # 找到了就不再检查其他snapshot
    except Exception as e:
        logger.warning(f"[Knowledge] HuggingFace扫描失败: {e}")

    return models


def scan_ollama_models():
    """扫描Ollama模型列表"""
    models = []
    try:
        result = subprocess.run(
            ['ollama', 'list'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:  # 跳过表头
                parts = line.split()
                if parts:
                    # ★ #1325（2026-10-30）：hv-pin-* 是钉选 GGUF 的 Ollama 注册件
                    #   （内容寻址内部名，钉选链路自动管理），不进系统知识/模型发现清单
                    if parts[0].startswith('hv-pin-'):
                        continue
                    models.append({
                        'name': parts[0],
                        'size': parts[1] if len(parts) > 1 else '',
                        'source': 'ollama',
                    })
    except FileNotFoundError:
        logger.info("[Knowledge] Ollama未安装，跳过")
    except subprocess.TimeoutExpired:
        logger.warning("[Knowledge] Ollama响应超时")
    except Exception as e:
        logger.warning(f"[Knowledge] Ollama扫描失败: {e}")

    return models


def scan_all():
    """执行全部扫描"""
    logger.info("[Knowledge] 开始系统知识扫描...")

    knowledge = {
        'scan_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'installed_libs': scan_installed_libs(),
        'modelscope_models': scan_modelscope_cache(),
        'huggingface_models': scan_huggingface_cache(),
        'ollama_models': scan_ollama_models(),
    }

    # 统计
    total_libs = len(knowledge['installed_libs'])
    total_ms = len(knowledge['modelscope_models'])
    total_hf = len(knowledge['huggingface_models'])
    total_ollama = len(knowledge['ollama_models'])

    logger.info(f"[Knowledge] 扫描完成: 库{total_libs}个, "
                f"ModelScope模型{total_ms}个, "
                f"HuggingFace模型{total_hf}个, "
                f"Ollama模型{total_ollama}个")

    # 保存到文件
    try:
        os.makedirs(os.path.dirname(KNOWLEDGE_FILE), exist_ok=True)
        with open(KNOWLEDGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(knowledge, f, ensure_ascii=False, indent=2)
        logger.info(f"[Knowledge] 已保存到 {KNOWLEDGE_FILE}")
    except Exception as e:
        logger.error(f"[Knowledge] 保存失败: {e}")

    return knowledge


def scan_async():
    """后台异步扫描（不阻塞启动）"""
    def _scan():
        try:
            scan_all()
        except Exception as e:
            logger.error(f"[Knowledge] 异步扫描失败: {e}")

    thread = threading.Thread(target=_scan, daemon=True)
    thread.start()
    logger.info("[Knowledge] 后台扫描已启动")


def get_knowledge_summary():
    """获取知识摘要（注入到提示词）"""
    if not os.path.exists(KNOWLEDGE_FILE):
        return ""

    try:
        with open(KNOWLEDGE_FILE, 'r', encoding='utf-8') as f:
            knowledge = json.load(f)
    except Exception:
        return ""

    lines = ["\n## 🔍 系统自检结果（哈文实际拥有的能力）\n"]
    lines.append(f"扫描时间: {knowledge.get('scan_time', '未知')}\n")

    # 已安装库
    libs = knowledge.get('installed_libs', {})
    if libs:
        lines.append("### 已安装的库:")
        for name, version in libs.items():
            lines.append(f"  - {name} ({version})")
        lines.append("")

    # ModelScope模型
    ms_models = knowledge.get('modelscope_models', [])
    if ms_models:
        lines.append("### 已下载的模型（ModelScope）:")
        for m in ms_models:
            lines.append(f"  - {m['id']} ({m.get('size_gb',0)}GB, {m.get('model_type','')}, 路径: {m['path']})")
        lines.append("")

    # HuggingFace模型
    hf_models = knowledge.get('huggingface_models', [])
    if hf_models:
        lines.append("### 已下载的模型（HuggingFace）:")
        for m in hf_models:
            lines.append(f"  - {m['id']} (路径: {m['path']})")
        lines.append("")

    # Ollama模型
    ollama_models = knowledge.get('ollama_models', [])
    if ollama_models:
        lines.append("### Ollama模型:")
        for m in ollama_models:
            lines.append(f"  - {m['name']} ({m.get('size','')})")
        lines.append("")

    if not libs and not ms_models and not hf_models and not ollama_models:
        lines.append("（暂无已安装的库或模型）\n")

    # 加载提示
    lines.append("""### ★ 模型加载方式：
- safetensors/bin模型 → 用transformers加载（不走Ollama）:
  `from transformers import AutoModelForCausalLM, AutoTokenizer`
  `model = AutoModelForCausalLM.from_pretrained('路径', device_map='auto', torch_dtype='auto')`
- Ollama模型 → `ollama run 模型名`
- ★ 用户说"安装/使用XX模型"时，先看上面的列表，已有就直接加载，没有才下载！""")

    return '\n'.join(lines)


if __name__ == '__main__':
    # 手动测试
    knowledge = scan_all()
    print("\n" + "=" * 50)
    print(get_knowledge_summary())
