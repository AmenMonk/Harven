# -*- coding: utf-8 -*-
"""#1418 三件套齐=出厂自带大脑 冒烟：
钉选空时 → 底座 ready + 出厂 understand 通道（模型真实存在）→ 直接答；
任一不满足 → 分层报错。测完还原 current_model。"""
import os
import sys
import json
from unittest import mock

sys.path.insert(0, r'H:\HarvenAI')
os.chdir(r'H:\HarvenAI')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))


# ── 准备：真实会话 + Engine + 主模型临时切 harveneye ──
from src.models.database import create_conversation
from src.core.engine import ConversationEngine as Engine
from src.core.model_manager import ModelManager
from src.core.multi_model_manager import get_multi_model_manager
import src.core.vision_link as vl

mm = get_multi_model_manager()
_saved_mm = mm.current_model
cid = create_conversation('#1418 冒烟')
eng = Engine(ModelManager(), cid)
_saved_yaml = getattr(eng.model, 'model', '')
eng.model.model = 'harveneye'
print(f"[准备] 会话 {cid}；model → harveneye（原 mm={_saved_mm} / yaml={_saved_yaml}，测完还原）")

FAKE_TAGS = {"models": [{"name": "qwen3-vl:8b-instruct"},
                        {"name": "nomic-embed-text:latest"}]}


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield json.dumps({"message": {"content": "出厂大脑"}}).encode('utf-8')
        yield json.dumps({"message": {"content": "在线"}, "done": True}).encode('utf-8')


captured = {}


def _spy_chat(msgs, stream=True, **kw):
    captured['msgs'] = msgs
    return _FakeResp()


def _patch_pinned_empty():
    return mock.patch.object(vl, 'resolve_pinned_channel', return_value={})


try:
    # ── T1 守卫实测：desktop 真实状态（底座在/ollama 缺 qwen3-vl）→ 分层报错不硬闯 ──
    print("\n[T1] 底座在 + 出厂模型缺 → 分层报错")
    with _patch_pinned_empty():
        events1 = list(eng.send('你好', pure_chat=True))
    texts1 = [e[1] for e in events1 if e[0] == 'text']
    errs1 = [str(e[1]) for e in events1 if e[0] == 'error']
    check("T1a 不出正文（不硬闯 ollama）", not texts1, f'texts={texts1[:1]}')
    check("T1b 报错含大脑/出路引导", any(('大脑' in e or '出路' in e) for e in errs1),
          (errs1[0][:70] if errs1 else '(no error?)'))

    # ── T2 出厂通道在线（mock tags 含 qwen3-vl + mock ollama 流）→ 直接答 ──
    print("\n[T2] 三件套齐（mock）→ 出厂大脑直答")
    _resp = mock.Mock()
    _resp.json.return_value = FAKE_TAGS
    with _patch_pinned_empty(), \
            mock.patch('requests.get', return_value=_resp), \
            mock.patch.object(vl, 'chat', _spy_chat):
        events2 = list(eng.send('你好', pure_chat=True))
    src2 = [e[1] for e in events2 if e[0] == 'model_source']
    texts2 = ''.join(str(e[1]) for e in events2 if e[0] == 'text')
    eye_srcs = [s for s in src2 if 'qwen3-vl' in s.get('label', '')]
    label2 = eye_srcs[0].get('label', '') if eye_srcs else f'(全部src={ [s.get("label") for s in src2] })'
    check("T2a 标注真实模型名（qwen3-vl·本地）",
          bool(eye_srcs) and '本地' in label2, label2[:60])
    check("T2b 正文流式产出", ('出厂大脑在线' in texts2), texts2[:40])
    check("T2c 通道透传（vision_link.chat 被调）", 'msgs' in captured)
    is_ext = eye_srcs[0].get('is_external', True) if eye_srcs else True
    check("T2d is_external=False（本地属性）", is_ext is False)

    # ── T3 底座缺（mock engine_status ready=False）→ 「还没安装」分层 ──
    print("\n[T3] 底座缺 → 还没安装分层报错")
    import src.vision.installer as vi
    with _patch_pinned_empty(), \
            mock.patch.object(vi, 'engine_status', return_value={'ready': False, 'groups': []}):
        events3 = list(eng.send('你好', pure_chat=True))
    errs3 = [str(e[1]) for e in events3 if e[0] == 'error']
    check("T3a 报「还没安装」", any('还没安装' in e for e in errs3),
          (errs3[0][:70] if errs3 else '(no error?)'))
finally:
    eng.model.model = _saved_yaml
    mm.current_model = _saved_mm
    print(f"\n[还原] mm={mm.current_model} / yaml={eng.model.model}")

print(f"\n═══ #1418 冒烟 {len(PASS)}/{len(PASS) + len(FAIL)} 通过 ═══")
if FAIL:
    print("FAIL 项：", FAIL)
    sys.exit(1)
