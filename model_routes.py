"""
模型管理 API 路由（Blueprint 版本）
包含：
  GET  /api/models/hardware      硬件检测
  GET  /api/models/available      推荐模型列表
  GET  /api/models/installed      已安装模型列表
  POST /api/models/pull           拉取模型
  POST /api/models/delete         删除模型
  POST /api/models/switch         切换当前模型
"""
import logging
logger = logging.getLogger(__name__)
import os
import sys
import json
import subprocess
import threading
import re
import yaml
from flask import Blueprint, request, jsonify, Response

bp = Blueprint('models', __name__, url_prefix='/api/models')


# ── 硬件检测 ────────────────────────────────────────────────────────────────

def _detect_hardware():
    """检测 GPU 显存 / 系统 RAM，返回推荐配置"""
    hw = {"gpu": None, "vram_mb": 0, "ram_gb": 0, "platform": sys.platform}

    # RAM
    try:
        if sys.platform == "win32":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
                def __init__(self):
                    self.dwLength = ctypes.sizeof(self)
                    super().__init__()
            mem = MEMORYSTATUSEX()
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            hw["ram_gb"] = int(mem.ullTotalPhys // (1024 ** 3))
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        hw["ram_gb"] = int(int(line.split()[1]) / 1024 / 1024)
                        break
    except Exception:
        hw["ram_gb"] = 32  # 默认

    # GPU 显存（nvidia-smi）
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            gpus = []
            for line in lines:
                parts = line.split(",")
                if len(parts) >= 2:
                    name = parts[0].strip()
                    vram = int(float(parts[1].strip()))
                    gpus.append({"name": name, "vram_mb": vram})
            if gpus:
                best = max(gpus, key=lambda g: g["vram_mb"])
                hw["gpu"] = best["name"]
                hw["vram_mb"] = best["vram_mb"]
    except Exception as _e: logger.debug(f"[model_routes] 忽略: {_e}")

    return hw


def _recommend_models(vram_mb):
    return []
    """根据显存推荐模型"""
    models = [
        {"name": "qwen3.6:35b-a3b", "vram_need": 8000,
         "description": "代码/Agent 专精，MoE 架构速度快", "tags": ["推荐", "代码"]},
        {"name": "qwen3.5:14b", "vram_need": 9000,
         "description": "最新多模态，支持文字+图片+音频", "tags": ["推荐", "多模态"]},
        {"name": "qwen3:14b", "vram_need": 9000,
         "description": "通用强，支持思考模式开关", "tags": ["通用"]},
        {"name": "qwen2.5:14b", "vram_need": 9000,
         "description": "稳定可靠，速度快", "tags": ["稳定"]},
        {"name": "deepseek-r1:14b", "vram_need": 9000,
         "description": "推理/数学/代码强，输出带思考过程", "tags": ["推理"]},
        {"name": "deepseek-r1:32b", "vram_need": 20000,
         "description": "更大推理模型，需卸载到内存", "tags": ["推理", "大显存"]},
        {"name": "qwen3.5:9b", "vram_need": 6000,
         "description": "轻量版，速度快，能力接近 14B", "tags": ["轻量"]},
        {"name": "deepseek-r1:8b", "vram_need": 5000,
         "description": "最轻量推理模型", "tags": ["轻量", "推理"]},
    ]

    for m in models:
        m["installable"] = vram_mb >= m["vram_need"] * 0.7
        m["recommended"] = vram_mb >= m["vram_need"]

    return models


@bp.route("/hardware")
def api_models_hardware():
    """返回本机硬件信息 + 推荐模型"""
    hw = _detect_hardware()
    recs = _recommend_models(hw["vram_mb"])
    return jsonify({
        "hardware": hw,
        "recommendations": recs
    })


@bp.route("/installed")
def api_models_installed():
    """返回 Ollama 已安装模型列表"""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr or "ollama 命令失败"}), 500

        models = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("NAME"):
                continue
            parts = line.split()
            if parts:
                name = parts[0]
                size = parts[2] if len(parts) > 2 else "?"
                models.append({"name": name, "size": size})
        return jsonify({"models": models})
    except FileNotFoundError:
        return jsonify({"error": "Ollama 未安装或未加入 PATH"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/pull", methods=["POST"])
def api_models_pull():
    """拉取模型"""
    data = request.get_json(force=True, silent=True) or {}
    model_name = data.get("model", "").strip()
    if not model_name:
        return jsonify({"error": "缺少 model 参数"}), 400

    def generate_progress():
        try:
            proc = subprocess.Popen(
                ["ollama", "pull", model_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    yield f"data: {json.dumps({'status': 'progress', 'msg': line})}\n\n"
            proc.wait()
            if proc.returncode == 0:
                yield f"data: {json.dumps({'status': 'done', 'model': model_name})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'error', 'msg': '拉取失败'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'msg': str(e)})}\n\n"

    return Response(
        generate_progress(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/delete", methods=["POST"])
def api_models_delete():
    """删除已安装模型"""
    data = request.get_json(force=True, silent=True) or {}
    model_name = data.get("model", "").strip()
    if not model_name:
        return jsonify({"error": "缺少 model 参数"}), 400

    try:
        result = subprocess.run(
            ["ollama", "rm", model_name],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return jsonify({"status": "ok", "model": model_name})
        else:
            return jsonify({"error": result.stderr or "删除失败"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/switch", methods=["POST"])
def api_models_switch():
    """切换 Harven AI 当前使用的模型（写入 config.yaml）"""
    data = request.get_json(force=True, silent=True) or {}
    model_name = data.get("model", "").strip()
    if not model_name:
        return jsonify({"error": "缺少 model 参数"}), 400

    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        if "ollama" not in cfg:
            cfg["ollama"] = {}
        cfg["ollama"]["model"] = model_name

        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

        return jsonify({"status": "ok", "model": model_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/quickpick")
def api_quickpick():
    """@ 快速切换：返回所有可用功能分类（LLM + 领域 + 专用模型 + 技能）"""
    import requests
    items = []
    cur = ''
    try:
        r = requests.get('http://localhost:11434/api/tags', timeout=3)
        if r.status_code == 200:
            for m in r.json().get('models', []):
                if 'embed' in m['name'].lower(): continue
                items.append({'id':'ollama:'+m['name'],'name':m['name'],'icon':'🦙','category':'llm','label':'对话 · '+m['name'],'desc':str(round(m['size']/1e9,1))+'GB','action':'switch_model','is_current':False})
    except Exception as _e: logger.debug(f"[model_routes] 忽略: {_e}")
    try:
        from src.core.agent_registry import AGENT_DEFINITIONS
        for aid, ag in AGENT_DEFINITIONS.items():
            tr = ag.get('triggers',[])
            if not tr: continue
            items.append({'id':aid,'name':ag.get('name',aid),'icon':ag.get('icon','⚡'),'category':ag.get('kind','skill'),'label':ag.get('name',aid),'desc':'、'.join(tr[:3]),'action':'insert_keyword','keyword':tr[0]})
    except Exception as _e: logger.debug(f"[model_routes] 忽略: {_e}")
    for did,icon,label,desc in [('law','⚖️','法律文书','合同审查、诉讼'),('medical','🏥','医疗咨询','健康建议'),('finance','💰','金融分析','股票基金'),('programming','💻','编程调试','代码修复'),('architecture','🏗️','建筑工程','图纸计算'),('education','📚','教育培训','论文润色'),('business','📊','商业分析','市场调研')]:
        items.append({'id':'domain:'+did,'name':label,'icon':icon,'category':'domain','label':label,'desc':desc,'action':'info','keyword':label})
    return jsonify({'items':items,'current':cur})


# ── 模型推荐 ────────────────────────────────────────────────────────────

def _recommend_models(vram_mb):
    return []
