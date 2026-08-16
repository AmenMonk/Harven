#!/usr/bin/env python3
"""
★ 2026-08-09: 定时任务调度器
后台线程，每分钟检查data/scheduler.json，到时间则执行对应Skill
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCHED_FILE = PROJECT_ROOT / 'data' / 'scheduler.json'

_scheduler_thread = None
_scheduler_running = False


def _check_and_run():
    """检查定时任务并执行"""
    try:
        if not SCHED_FILE.exists():
            return

        with open(SCHED_FILE, 'r', encoding='utf-8') as f:
            tasks = json.load(f)

        now = datetime.now()
        current_time = now.strftime('%H:%M')
        current_date = now.strftime('%Y-%m-%d')

        for task in tasks:
            if not task.get('enabled', True):
                continue

            task_time = task.get('time', '')
            last_run = task.get('last_run', '')

            # 支持格式："07:00" 或 "daily:07:00"
            if ':' in task_time:
                parts = task_time.split(':')
                if len(parts) == 2 and parts[0] == 'daily':
                    target_time = parts[1]
                elif len(parts) == 2 and parts[0] not in ('daily', 'weekly', 'monthly'):
                    target_time = task_time
                else:
                    target_time = parts[-1] if len(parts) > 1 else ''
            else:
                target_time = task_time

            # 检查是否到时间（且今天没执行过）
            if target_time == current_time and current_date not in last_run:
                skill_name = task.get('skill', '')
                if skill_name:
                    logger.info(f"[Scheduler] 执行定时任务: {task.get('name', skill_name)} (skill={skill_name})")
                    try:
                        from src.core.scripts_runner import run_skill_script
                        result = run_skill_script(skill_name, 'main', {}, timeout=300)
                        task['last_run'] = current_date + ' ' + current_time
                        task['last_result'] = 'success' if result.get('success') else 'failed'
                        logger.info(f"[Scheduler] 任务完成: {task.get('name')} → {task['last_result']}")
                    except Exception as e:
                        logger.error(f"[Scheduler] 任务执行失败: {e}")
                        task['last_run'] = current_date + ' ' + current_time
                        task['last_result'] = f'error: {e}'

        # 保存更新后的任务
        with open(SCHED_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"[Scheduler] 检查失败: {e}")


def _scheduler_loop():
    """调度器主循环"""
    global _scheduler_running
    logger.info("[Scheduler] 定时任务调度器已启动")
    while _scheduler_running:
        try:
            _check_and_run()
        except Exception as e:
            logger.error(f"[Scheduler] 循环异常: {e}")
        # 每60秒检查一次
        time.sleep(60)


def start_scheduler():
    """启动定时任务调度器"""
    global _scheduler_thread, _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    logger.info("[Scheduler] 调度器线程已启动")


def stop_scheduler():
    """停止调度器"""
    global _scheduler_running
    _scheduler_running = False
    logger.info("[Scheduler] 调度器已停止")
