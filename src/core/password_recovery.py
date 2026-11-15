# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""密码恢复（#1362，2026-11-02）——安全问题 + 恢复码双通道。

背景：改密后用户忘了自己的密码，页面上没有任何找回通道（本地单机、没绑邮箱）。
本模块提供两条自救路：
  1. 安全问题：改密时设置 2 题（题库可选+自定义），答案哈希存 config
  2. 恢复码：设置时生成 XXXX-XXXX-XXXX-XXXX，用户下载存好（恢复盘）

存储位置：config.yaml → security.recovery（与密码同信任域，config 本地文件）
铁律：
  - 答案比对 = sha256(strip().lower())，不存明文
  - 恢复码明文存（config 里密码本身也是明文，同信任域；哈希无增量安全）
  - 重置密码走与 change_admin_password 相同的三处同步（security.admin_password /
    security.remote_password / 顶层 admin_password），改完全系统一把锁同步生效
"""
import hashlib
import secrets
import os

QUESTIONS_BANK = [
    '你的第一所学校叫什么名字？',
    '你小时候最好的朋友叫什么名字？',
    '你出生的城市是哪里？',
    '你第一辆车的品牌是什么？',
    '你最爱吃的一道菜是什么？',
    '你小学班主任的名字是？',
    '你养过的第一只宠物的名字是？',
    '你的生日是几月几日？（不含年份）',
]


def _cfg_path():
    return os.path.join(os.path.dirname(__file__), '..', '..', 'config.yaml')


def _hash_answer(ans: str) -> str:
    return hashlib.sha256((ans or '').strip().lower().encode('utf-8')).hexdigest()


def _load_recovery() -> dict:
    import yaml
    try:
        with open(_cfg_path(), 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        rec = (cfg.get('security') or {}).get('recovery')
        return rec if isinstance(rec, dict) else {}
    except Exception:
        return {}


def _save_recovery(rec: dict) -> None:
    import yaml
    p = _cfg_path()
    with open(p, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    sec = cfg.get('security') if isinstance(cfg.get('security'), dict) else {}
    # 铁律 4：yaml.dump 必须传 dict()
    sec['recovery'] = dict(rec)
    cfg['security'] = sec
    with open(p, 'w', encoding='utf-8') as f:
        yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)


def get_status() -> dict:
    """设置页用：是否已设置 + 问题文本 + 恢复码（设置页入口有密码验证，码可以给）"""
    rec = _load_recovery()
    qs = rec.get('questions') or []
    return {
        'set': bool(qs),
        'questions': [q.get('q', '') for q in qs],
        'code': rec.get('code', ''),
        'set_at': rec.get('set_at', ''),
    }


def get_hints() -> dict:
    """登录页「忘记密码」用（无门槛）：只给问题文本，不给答案/恢复码"""
    rec = _load_recovery()
    qs = rec.get('questions') or []
    return {'set': bool(qs), 'q1': qs[0].get('q', '') if qs else '', 'q2': qs[1].get('q', '') if len(qs) > 1 else ''}


def setup_questions(q1, a1, q2, a2):
    """设置/更新安全问题 + 生成恢复码。调用方（路由层）必须先验证当前密码。"""
    q1, a1, q2, a2 = (q1 or '').strip(), (a1 or '').strip(), (q2 or '').strip(), (a2 or '').strip()
    if not q1 or not a1 or not q2 or not a2:
        return {'success': False, 'error': '问题和答案都不能为空'}
    if q1 == q2:
        return {'success': False, 'error': '两个问题不能一样'}
    code = '-'.join(secrets.token_hex(2).upper() for _ in range(4))
    from datetime import datetime
    _save_recovery({
        'questions': [{'q': q1, 'a_hash': _hash_answer(a1)}, {'q': q2, 'a_hash': _hash_answer(a2)}],
        'code': code,
        'set_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    return {'success': True, 'code': code}


def verify_answers(a1, a2) -> bool:
    rec = _load_recovery()
    qs = rec.get('questions') or []
    if len(qs) < 2:
        return False
    return _hash_answer(a1) == qs[0].get('a_hash') and _hash_answer(a2) == qs[1].get('a_hash')


def verify_code(code) -> bool:
    rec = _load_recovery()
    stored = (rec.get('code') or '').strip().upper()
    return bool(stored) and (code or '').strip().upper() == stored


def reset_password(new_password):
    """忘记密码重置（绕过旧密码）。三处同步与 change_admin_password 一致。"""
    if not new_password or len(new_password) < 6:
        return {'success': False, 'error': '新密码至少 6 位'}
    import yaml
    p = _cfg_path()
    with open(p, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    sec = cfg.get('security') if isinstance(cfg.get('security'), dict) else {}
    sec['admin_password'] = new_password
    sec['remote_password'] = new_password  # 远程访问锁同步，全系统一把锁
    cfg['security'] = sec
    cfg['admin_password'] = new_password  # 兼容旧读取路径
    with open(p, 'w', encoding='utf-8') as f:
        yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)
    try:
        from src.core.security_guard import log_operation
        log_operation('security', 'reset_password_by_recovery', 'OK')
    except Exception:
        pass
    return {'success': True, 'error': ''}
