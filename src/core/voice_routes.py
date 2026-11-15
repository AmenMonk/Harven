# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - Voice Model Routes (语音识别模型管理)
★ 2026-09-21 (#825 开发者令)：设置页「语音识别」区后端
- GET    /api/voice/models          模型清单（目录 + 已装状态 + 当前使用）
- POST   /api/voice/models/download {"name": "base"}  后台线程下载（ModelScope 国内直连）
- POST   /api/voice/models/delete   {"name": "base"}  删除（最后一个不让删）
下载/删除后自动复位 VoiceInput 单例，下次转写按 MODEL_PREFERENCE 重新择优加载。
"""
import os
import shutil
import threading
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint('voice_models', __name__, url_prefix='/api/voice')

ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent
MODEL_ROOT = ROOT / 'data' / 'models'

# 五档目录（ModelScope pengzhendong/faster-whisper-* 全部实测存在 2026-09-21）
CATALOG = [
    {'name': 'tiny',            'label': '轻量 tiny',            'size': '约 75MB',   'size_mb': 75,   'note': '最快、最省地方，识别一般'},
    {'name': 'base',            'label': '标准 base',            'size': '约 145MB',  'size_mb': 145,  'note': '速度与准确均衡（默认）'},
    {'name': 'small',           'label': '增强 small',           'size': '约 480MB',  'size_mb': 480,  'note': '识别更准，稍慢'},
    {'name': 'medium',          'label': '精准 medium',          'size': '约 1.5GB',  'size_mb': 1530, 'note': '更准，建议 8GB 以上显存'},
    {'name': 'large-v3-turbo',  'label': '旗舰 large-v3-turbo',  'size': '约 1.5GB',  'size_mb': 1620, 'note': '最准，GPU 加速最优（推荐）'},
]
_MS_PREFIX = 'pengzhendong/faster-whisper-'

# 下载状态：{'name': str, 'state': idle|downloading|ok|error, 'msg': str}
_dl_state = {'name': None, 'state': 'idle', 'msg': ''}


def _installed_names():
    """已安装的 whisper 模型名列表（以 model.bin 为准）"""
    names = []
    if MODEL_ROOT.exists():
        for child in sorted(MODEL_ROOT.iterdir()):
            if child.is_dir() and child.name.startswith('whisper') and (child / 'model.bin').exists():
                names.append(child.name.replace('whisper-', '', 1))
    return names


def _active_name():
    """当前实际使用的模型（与 voice_input.MODEL_PREFERENCE 同逻辑）"""
    try:
        from src.core.voice_input import VoiceInput
        path, is_local = VoiceInput()._get_model_path()
        if is_local:
            return Path(path).name.replace('whisper-', '', 1)
        return 'base（未下载，首次用时自动获取）'
    except Exception:
        return None


def _reset_singleton():
    """下载/删除后复位 VoiceInput 单例，下次转写重新择优加载"""
    try:
        from src.core.voice_input import get_voice_input
        vi = get_voice_input()
        vi._whisper_model = None
        vi._model_loaded = False
        logger.info('[VoiceRoutes] VoiceInput 单例已复位')
    except Exception as e:
        logger.warning(f'[VoiceRoutes] 单例复位失败: {e}')


def _download_worker(name: str):
    """后台线程：ModelScope 下载 → 拷贝到 data/models/whisper-<name>"""
    import tempfile
    _dl_state.update({'name': name, 'state': 'downloading', 'msg': '正在从国内源下载…'})
    target = MODEL_ROOT / f'whisper-{name}'
    try:
        for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
            os.environ.pop(_k, None)  # 系统代理会打断国内直连（#797 同款教训）
        from modelscope import snapshot_download
        src = snapshot_download(_MS_PREFIX + name)
        target.mkdir(parents=True, exist_ok=True)
        for f in Path(src).iterdir():
            if f.is_file():
                shutil.copy2(str(f), str(target / f.name))
        _reset_singleton()
        _dl_state.update({'name': name, 'state': 'ok', 'msg': '下载完成，下次使用语音即生效'})
        logger.info(f'[VoiceRoutes] 下载完成: {name} -> {target}')
    except Exception as e:
        _dl_state.update({'name': name, 'state': 'error', 'msg': f'下载失败：{e}'})
        logger.error(f'[VoiceRoutes] 下载失败 {name}: {e}')


@bp.route('/models', methods=['GET'])
def list_models():
    installed = _installed_names()
    try:
        from src.core.voice_input import VoiceInput
        preferred = VoiceInput.get_preferred_model()
    except Exception:
        preferred = 'small'
    items = []
    for c in CATALOG:
        items.append({
            **c,
            'installed': c['name'] in installed,
            'active': _active_name() == c['name'],
            'preferred': c['name'] == preferred,
        })
    return jsonify({
        'success': True,
        'models': items,
        'installed': installed,
        'active': _active_name(),
        'preferred': preferred,
        'download': _dl_state,
    })


# ★ #929-A1（2026-09-29）：设为常用档位——写入 config.yaml → voice.preferred_model，
#   哈文听音一律先用这档（没装会自动回退别的档，不会哑）。name 传空 = 恢复默认 small
@bp.route('/models/prefer', methods=['POST'])
def prefer_model():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if name and not any(c['name'] == name for c in CATALOG):
        return jsonify({'success': False, 'error': f'不认识的模型名：{name}'})
    target = name or 'small'
    cfg_path = ROOT / 'config.yaml'
    try:
        import yaml
        cfg = {}
        if cfg_path.exists():
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
        if not isinstance(cfg.get('voice'), dict):
            cfg['voice'] = {}
        cfg['voice']['preferred_model'] = target
        with open(cfg_path, 'w', encoding='utf-8') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        return jsonify({'success': False, 'error': f'保存设置失败：{e}'})
    _reset_singleton()
    installed = target in _installed_names()
    msg = (f'常用档位已设为 {target}' if installed
           else f'常用档位已设为 {target}（还没下载，暂时先用别的档顶着，下载后自动生效）')
    return jsonify({'success': True, 'preferred': target, 'installed': installed, 'msg': msg})


# ══ ★ #929-A4（2026-09-29）语音档案库 ══
# 开发者原则：语音默认不保存；只有点 💾 或说「这段话存下来」才入库

@bp.route('/archive', methods=['POST'])
def archive_save():
    data = request.get_json(silent=True) or {}
    try:
        from src.core.voice_archive import save_voice_note
        rec = save_voice_note(
            text=data.get('text') or '',
            audio_b64=data.get('audio_b64') or '',
            ext=data.get('ext') or 'webm',
            duration=data.get('duration'),
            source=data.get('source') or 'manual')
        return jsonify({'success': True, 'record': rec, 'msg': '已存入语音档案'})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)})
    except Exception as e:
        logger.error(f'[VoiceRoutes] 档案保存失败: {e}')
        return jsonify({'success': False, 'error': f'保存失败：{e}'})


@bp.route('/archive', methods=['GET'])
def archive_list():
    try:
        from src.core.voice_archive import list_voice_notes
        items = list_voice_notes(query=(request.args.get('query') or '').strip(),
                                 limit=min(int(request.args.get('limit') or 50), 200))
        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'items': []})


@bp.route('/archive/<int:rid>', methods=['DELETE'])
def archive_delete(rid):
    try:
        from src.core.voice_archive import delete_voice_note
        ok = delete_voice_note(rid)
        return jsonify({'success': ok, 'msg': '已删除' if ok else '没找到这条档案'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ══ ★ #929-A6（2026-09-29）外脑听音能力 + 语音直通开关 ══
# 实锤：glm-5.3-Flash 标准接口不吃音频（400）。当前没有外脑能直通 → 机制在位、默认休眠。
# 利弊（给用户的提示口径）：
#   直通=语气语调活、省一步转写，但费钱、语音记录搜不了；
#   本地转写=免费秒转、记录可存可搜可蒸馏，识别全部在家完成。

def _read_voice_cfg():
    import yaml
    try:
        with open(ROOT / 'config.yaml', 'r', encoding='utf-8') as f:
            return (yaml.safe_load(f) or {}).get('voice') or {}
    except Exception:
        return {}


def _write_voice_cfg(key, val):
    import yaml
    cfg_path = ROOT / 'config.yaml'
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg.get('voice'), dict):
        cfg['voice'] = {}
    cfg['voice'][key] = val
    with open(cfg_path, 'w', encoding='utf-8') as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)


@bp.route('/model_capability', methods=['GET'])
def model_capability():
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        from src.core.engine import _model_has_audio
        mm = get_multi_model_manager()
        cur = (getattr(mm, 'current_model', '') or '') if mm else ''
        has_audio = bool(cur and cur != 'ollama' and _model_has_audio(cur))
        cfg = _read_voice_cfg()
        return jsonify({
            'success': True,
            'current_model': cur or '',
            'has_audio': has_audio,
            'direct_enabled': bool(cfg.get('audio_direct', False)),
            'note': ('当前外脑能直接听语音，可在下方开「直通」' if has_audio
                     else '当前外脑不能直接听语音（实测 glm-5.3-Flash 不支持），哈文先在家把语音转成文字再发——免费、快，记录可存可搜'),
        })
    except Exception as e:
        return jsonify({'success': False, 'has_audio': False, 'direct_enabled': False, 'error': str(e)})


@bp.route('/audio_direct', methods=['POST'])
def set_audio_direct():
    data = request.get_json(silent=True) or {}
    # ★ #940 缺 enabled 键拒绝：原先空体会被 bool(None) 静默当成「关闭」写进配置
    if 'enabled' not in data or not isinstance(data.get('enabled'), bool):
        return jsonify({'success': False, 'error': '缺少 enabled 布尔字段（true=开启直通 / false=关闭）'}), 400
    enabled = data['enabled']
    try:
        _write_voice_cfg('audio_direct', enabled)
        return jsonify({'success': True, 'direct_enabled': enabled,
                        'msg': '已开启外脑直通听语音' if enabled else '已关闭直通（语音先在家转文字）'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/direct', methods=['POST'])
def voice_direct():
    """★ A6 语音直通：音频直接发给当前外脑（OpenAI input_audio 格式），不经本地转写。
    双重门：当前模型必须 has_audio 且用户开了 audio_direct，否则诚实拒绝（不偷偷换路）。
    附带并行本地转写（archive_text）供语音档案存档——直通模式音频也不留底的要求由调用方决定。"""
    audio_file = request.files.get('audio')
    question = (request.form.get('question') or '').strip() or '请听这段语音，把内容告诉你听到的原话，并回应它。'
    if not audio_file:
        return jsonify({'success': False, 'error': 'No audio'}), 400
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        from src.core.engine import _model_has_audio
        mm = get_multi_model_manager()
        cur = (getattr(mm, 'current_model', '') or '') if mm else ''
        conf = (mm.config.get('models', {}).get(cur, {}) or {}) if mm else {}
        if not cur or cur == 'ollama':
            return jsonify({'success': False, 'error': '还没有选外脑，直通不知道发给谁。请先选外脑或切回本地转写'}), 400
        if not _model_has_audio(cur):
            return jsonify({'success': False, 'error': f'当前外脑（{cur}）不支持直接听语音。语音会按普通方式先在家转文字'}), 400
        if not _read_voice_cfg().get('audio_direct', False):
            return jsonify({'success': False, 'error': '语音直通没开。到 设置→语音识别 打开「外脑直通」再用'}), 400

        import base64 as _b64
        raw = audio_file.read()
        b64 = _b64.b64encode(raw).decode()
        fmt = 'mp4' if 'mp4' in (audio_file.filename or '').lower() else 'wav'
        import requests as _rq
        url = (conf.get('base_url') or '').rstrip('/') + '/chat/completions'
        resp = _rq.post(url, headers={'Authorization': 'Bearer ' + (conf.get('api_key') or ''),
                                      'Content-Type': 'application/json'},
                        json={'model': conf.get('model_name') or cur,
                              'messages': [{'role': 'user', 'content': [
                                  {'type': 'text', 'text': question},
                                  {'type': 'input_audio', 'input_audio': {'data': b64, 'format': fmt}},
                              ]}]},
                        timeout=120)
        out = resp.json()
        if resp.status_code != 200:
            msg = (out.get('error') or {}).get('message', '') if isinstance(out.get('error'), dict) else str(out.get('error') or out)
            return jsonify({'success': False, 'error': f'外脑直通失败：{msg}'}), 502
        answer = ((out.get('choices') or [{}])[0].get('message') or {}).get('content', '')
        return jsonify({'success': True, 'answer': answer, 'model': cur})
    except Exception as e:
        return jsonify({'success': False, 'error': f'语音直通失败：{e}'}), 500


@bp.route('/models/download', methods=['POST'])
def download_model():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not any(c['name'] == name for c in CATALOG):
        return jsonify({'success': False, 'error': f'不认识的模型名：{name}'})
    if _dl_state.get('state') == 'downloading':
        return jsonify({'success': False, 'error': '已有模型正在下载，请等它完成'})
    if name in _installed_names():
        return jsonify({'success': True, 'already': True, 'msg': '已经装过了，不用再下'})
    th = threading.Thread(target=_download_worker, args=(name,), daemon=True)
    th.start()
    return jsonify({'success': True, 'msg': f'开始下载 {name}，可稍后刷新查看进度'})


@bp.route('/models/delete', methods=['POST'])
def delete_model():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not any(c['name'] == name for c in CATALOG):
        return jsonify({'success': False, 'error': f'不认识的模型名：{name}'})
    if _dl_state.get('state') == 'downloading':
        return jsonify({'success': False, 'error': '正在下载模型，稍等再删'})
    installed = _installed_names()
    if name not in installed:
        return jsonify({'success': False, 'error': '这个模型本来就没安装'})
    if len(installed) <= 1:
        return jsonify({'success': False, 'error': '最后一个听音模型不能删，删了语音就用不了了'})
    target = MODEL_ROOT / f'whisper-{name}'
    try:
        shutil.rmtree(str(target))
        _reset_singleton()
        logger.info(f'[VoiceRoutes] 已删除 {target}')
        return jsonify({'success': True, 'msg': f'已删除 {name}'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'删除失败：{e}'})
