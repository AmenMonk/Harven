# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""Harven AI — L3 习惯模式学习引擎
从对话中自动学习用户的交互模式、偏好和习惯"""
import os
import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_HABITS_PATH = Path(__file__).parent.parent.parent / 'data' / 'habits.json'
_HABITS_CACHE = None
_HABITS_CACHE_TS = 0
_CACHE_TTL = 30  # 秒

# 默认习惯模板
DEFAULT_HABITS = {
    'interaction_style': [],     # 交互模式：先讨论再开发、直接要结果等
    'content_preferences': [],   # 内容偏好：表格、编号、先结论
    'refusal_patterns': [],      # 拒绝模式：不喜欢冗长、不喜欢废话
    'work_hours': [],            # 工作时段
    'language_style': [],        # 语言风格：直接、严谨、轻松
    'tech_stack': [],            # 常用技术栈
    'learned_triggers': [],      # 学习到的触发词：说"讨论"=只要讨论不要开发
}

def _load_habits() -> dict:
    global _HABITS_CACHE, _HABITS_CACHE_TS
    now = time.time()
    if _HABITS_CACHE and (now - _HABITS_CACHE_TS) < _CACHE_TTL:
        return _HABITS_CACHE
    try:
        if _HABITS_PATH.exists():
            with open(_HABITS_PATH, 'r', encoding='utf-8') as f:
                _HABITS_CACHE = json.load(f)
        else:
            _HABITS_CACHE = dict(DEFAULT_HABITS)
    except Exception:
        _HABITS_CACHE = dict(DEFAULT_HABITS)
    _HABITS_CACHE_TS = now
    return _HABITS_CACHE

def _save_habits(habits: dict):
    try:
        _HABITS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_HABITS_PATH, 'w', encoding='utf-8') as f:
            json.dump(habits, f, ensure_ascii=False, indent=2)
    except PermissionError:
        logger.error(f'[Habits] 保存失败: 文件权限不足，请右键 data/habits.json → 属性 → 取消只读')
    except Exception as e:
        logger.error(f'[Habits] 保存失败: {e}')

def learn_from_conversation(user_message: str, ai_response: str):
    """从单轮对话中学习习惯"""
    habits = _load_habits()
    msg = (user_message + ' ' + ai_response).lower()
    
    # 检测交互模式
    if any(w in msg for w in ['先讨论', '讨论一下', '你觉得', '商量']):
        _add_if_new(habits['interaction_style'], '先讨论再开发')
    if any(w in msg for w in ['直接做', '开始开发', '不用讨论', '开干']):
        _add_if_new(habits['interaction_style'], '直接要结果')
    
    # 检测内容偏好
    if any(w in msg for w in ['表格', '表格形式', '做成表']):
        _add_if_new(habits['content_preferences'], '偏好表格')
    if any(w in msg for w in ['先给结论', '先说结果', '简而言之']):
        _add_if_new(habits['content_preferences'], '先结论后详情')
    if any(w in msg for w in ['编号', '列表', '1.', '2.']):
        _add_if_new(habits['content_preferences'], '偏好编号列表')
    
    # 检测拒绝模式
    if any(w in msg for w in ['太长', '废话', '简洁点', '别说那么多']):
        _add_if_new(habits['refusal_patterns'], '不喜欢冗长回答')
    
    # 检测技术栈
    techs = ['python', 'flask', 'react', 'vue', 'docker', 'sqlite', 'javascript',
             'typescript', 'node', 'go', 'rust', 'mongodb', 'redis', 'nginx']
    for t in techs:
        if t in msg:
            _add_if_new(habits['tech_stack'], t)
    
    # 检测触发词
    if '讨论' in user_message and len(user_message) < 20:
        _add_if_new(habits['learned_triggers'], '说"讨论"=只要讨论不要开发')
    if '做' in user_message and '开发' in user_message:
        _add_if_new(habits['learned_triggers'], '说"开发"+需求=直接执行')
    
    _save_habits(habits)

def build_habits_context() -> str:
    """构建习惯上下文（注入到系统提示词）"""
    habits = _load_habits()
    parts = []
    for key, label in [
        ('interaction_style', '已学习的交互模式'),
        ('content_preferences', '内容偏好'),
        ('refusal_patterns', '需要注意'),
        ('learned_triggers', '已学习的触发规则'),
    ]:
        items = habits.get(key, [])
        if items:
            parts.append(f'### {label}\n' + '\n'.join(f'  - {i}' for i in items[-3:]))
    
    if not parts:
        return ''
    
    return '\n\n## 用户习惯（L3 自动学习）\n' + '\n\n'.join(parts)

def _add_if_new(lst, item):
    if item not in lst:
        lst.append(item)
        if len(lst) > 20:
            lst.pop(0)  # 保留最近20条
