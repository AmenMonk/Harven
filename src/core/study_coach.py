# -*- coding: utf-8 -*-
"""学习成长中心（2026-10-21 定版拍板开发，借鉴蜜蜂家校）
· 拍作业即批改：作业照+答案照 → GLM-4V 两步管线（提答案→对照批改）→ 批改卡+错题入库
· 错题本：按知识点聚合 + 错误次数 + 掌握标记
· 学情周报：正确率趋势 + 外脑教练建议（强制含休息/鼓励项，语气按沟通偏好）
· 一键设目标：薄弱知识点 → Goals 向导（前端跳 /goals?panel_id=study_center&wish=…）
诚实定位：AI 批改供参考，手写识别有误差，待人工确认的题不硬判。
"""
import os, json, logging, sqlite3, base64
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'sqlite' / 'lingzhi.db'

_ddl_done = False


def ensure_tables():
    global _ddl_done
    if _ddl_done:
        return
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS study_wrong_book (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child TEXT DEFAULT '', subject TEXT DEFAULT '',
        question TEXT DEFAULT '', student_answer TEXT DEFAULT '', correct_answer TEXT DEFAULT '',
        knowledge_point TEXT DEFAULT '',
        wrong_count INTEGER DEFAULT 1,
        status TEXT DEFAULT 'active',            -- active|resolved
        grade_id INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        last_review TEXT DEFAULT '')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS study_grades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child TEXT DEFAULT '', subject TEXT DEFAULT '',
        total INTEGER DEFAULT 0, wrong INTEGER DEFAULT 0, correct_rate REAL DEFAULT 0,
        knowledge_points TEXT DEFAULT '[]',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')))""")
    # ★ #1252（2026-10-27 定版拍板「批改展示标准」）：逐题明细落库（豆包式展示的数据家）
    # ★ #1253（2026-10-27）：photo 列——批改照片回填，电脑「学习成长中心」点开看原作业
    _cols = {r[1] for r in conn.execute('PRAGMA table_info(study_grades)').fetchall()}
    if 'detail' not in _cols:
        conn.execute("ALTER TABLE study_grades ADD COLUMN detail TEXT DEFAULT '[]'")
    if 'photo' not in _cols:
        conn.execute("ALTER TABLE study_grades ADD COLUMN photo TEXT DEFAULT ''")
    # ★ #1490（2026-11-12）A 套餐：AI 分析卡（按 grade_id 缓存，一次批改一份分析）
    conn.execute("""CREATE TABLE IF NOT EXISTS study_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grade_id INTEGER UNIQUE,
        child TEXT DEFAULT '',
        content TEXT DEFAULT '',
        gen_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')))""")
    # ★ #1490 A 套餐：学情周报存档（每孩子每周一份，可回看往期）
    conn.execute("""CREATE TABLE IF NOT EXISTS study_weekly (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child TEXT DEFAULT '',
        week_start TEXT DEFAULT '',
        content TEXT DEFAULT '',
        stats TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(child, week_start))""")
    conn.commit()
    conn.close()
    _ddl_done = True


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _zhipu_key():
    try:
        mm = json.load(open(Path(__file__).resolve().parent.parent.parent / 'data' / 'multi_models.json', encoding='utf-8'))

        def find(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if 'zhipu' in str(k).lower():
                        if isinstance(v, dict) and v.get('api_key'):
                            return v['api_key']
                        r = find(v)
                        if r:
                            return r
                    elif isinstance(v, (dict, list)):
                        r = find(v)
                        if r:
                            return r
            elif isinstance(o, list):
                for v in o:
                    r = find(v)
                    if r:
                        return r
        return find(mm) or ''
    except Exception:
        return ''


def _is_eye_selected() -> bool:
    """★ #1265（2026-10-29）双源判定 → #1267 起委托 eye_client.is_selected()（五处看图分支共用一份实现）。"""
    from src.core import eye_client
    return eye_client.is_selected()


def _current_vision_provider():
    """★ 2026-10-22 方案A（定版拍板）→ #1202（2026-10-25）删智谱特例：批改完全跟随当前外脑。
    返回 (provider_id, model_name, api_key, base_url)；当前外脑不支持看图 → 抛错（不偷偷回退智谱）。
    ★ #1202 实测推翻旧特例前提：glm-5.3-Flash 本身就能看图（35+27=63 判断题实测，
    5.3-Flash 判对、4v-flash 反而判错）——智谱不再偷换 4v-flash，选谁给谁，与 #1155 看图链原则统一。"""
    from src.core.multi_model_manager import get_multi_model_manager, PROVIDERS
    mm = get_multi_model_manager()
    # ★ #1265：删旧版 `mm.current_model or 'zhipu'` 隐式代选（违反铁律 0——没选就报，绝代选）
    provider_id = (mm.current_model or '').strip()
    if not provider_id:
        raise RuntimeError('还没有选择外脑模型，批改无法使用。请到 设置→模型中心 选一个支持看图的外脑（如智谱 GLM / 豆包 / 通义）')
    if provider_id == 'harveneye':
        # ★ #1265 防线：正常不应到达（_is_eye_selected 已拦截走 Eye 通道）——到达说明
        #   双源同时错位/异常，给可执行话术，不再撞「PROVIDERS 查无此人→不支持看图」误导报错
        raise RuntimeError('批改状态异常（当前模型=视觉引擎但本地通道未接上）。请重启哈文后重试；'
                           '仍失败请到 设置→模型中心 选一个支持看图的外脑（如智谱 GLM）')
    if provider_id == 'ollama':
        raise RuntimeError('当前是本地模型模式。批改需要看图能力——请把外脑切换到支持看图的云端模型（如智谱 GLM）')
    prov = PROVIDERS.get(provider_id, {})
    if not prov.get('vision'):
        label = prov.get('name', provider_id)
        raise RuntimeError(f'当前外脑「{label}」不支持看图，批改功能无法使用。请在设置→模型中心切换到支持看图的外脑（如智谱 GLM / 豆包 / 通义）')
    cfg = mm.config.get('models', {}).get(provider_id, {})
    model_name = cfg.get('model_name') or (prov.get('models') or [''])[0]
    key = mm.config.get('model_keys', {}).get(provider_id, '')
    if not key:
        import yaml
        cfgp = Path(__file__).resolve().parent.parent.parent / 'config.yaml'
        yk = (yaml.safe_load(cfgp.read_text(encoding='utf-8')) or {}).get('model_keys') or {}
        key = yk.get(provider_id, '')
    return provider_id, model_name, key, prov.get('base_url', '')


def _cloud_slot_vision_provider():
    """★ #1302 分流第二级（2026-10-29）：当前主模型是本地时，读外脑槽位
    （current_model_by_mode.cloud，用户自己在云槽选过的模型，非代选）找支持看图的外脑。
    没 key / 槽位不支持看图 → 抛错（由 _vision_call_raw 落回本地 Eye）。"""
    from src.core.multi_model_manager import get_multi_model_manager, PROVIDERS
    mm = get_multi_model_manager()
    pid = ''
    try:
        pid = str((mm.config.get('current_model_by_mode') or {}).get('cloud') or '').strip()
    except Exception:
        pass
    if not pid or pid in ('harveneye', 'ollama'):
        raise RuntimeError('外脑槽没有可用模型')
    prov = PROVIDERS.get(pid, {})
    if not prov.get('vision'):
        raise RuntimeError(f'外脑槽「{pid}」不支持看图')
    cfg = mm.config.get('models', {}).get(pid, {})
    model_name = cfg.get('model_name') or (prov.get('models') or [''])[0]
    key = mm.config.get('model_keys', {}).get(pid, '')
    if not key:
        import yaml as _yaml
        cfgp = Path(__file__).resolve().parent.parent.parent / 'config.yaml'
        yk = (_yaml.safe_load(cfgp.read_text(encoding='utf-8')) or {}).get('model_keys') or {}
        key = yk.get(pid, '')
    if not key:
        raise RuntimeError(f'外脑槽「{pid}」没配密钥')
    return pid, model_name, key, prov.get('base_url', '')


def _eye_vision_call_raw(images_b64: list, prompt: str):
    """★ #1250（2026-10-27）：HarvenEye 批改通道——主模型=Eye 时批改走本地（选谁给谁）。
    Ollama /api/chat 原生多图（messages[].images = b64 数组），Eye vlm_engine.chat 原样透传。
    返回 (content, finish_reason)；Ollama 无 length 截断概念，恒 ''（截断续批退化为单轮）。"""
    from src.core import eye_client
    imgs = []
    for b in images_b64:
        if b.startswith('data:'):
            b = b.split(',', 1)[1]
        imgs.append(b)
    msgs = [{'role': 'user', 'content': prompt, 'images': imgs}]
    ok, ans = eye_client.chat(msgs, timeout=180.0)
    # ★ #1260（A1）：重试条件放宽——旧版只认「还没就绪」（冷加载窗口），
    #   120s 截断的「处理超时」不重试 → 用户手动重试还是败（批改 503 实锤）。
    #   现在超时类也自动等 15s 重试一次（Eye 端可能还在跑前一轮长任务/模型重载）
    if not ok and ('还没就绪' in ans or '超时' in ans):
        # 冷加载窗口（Eye 刚启动/Ollama 闲置卸载）或推理超时 → 等一次再试
        #   （20:57 实锤：批改正好在模型加载中，38 秒后就绪了——差一步）
        import time as _t
        _t.sleep(15)
        ok, ans = eye_client.chat(msgs, timeout=180.0)
    if not ok:
        raise RuntimeError(f'视觉引擎批改失败: {ans}')
    return ans, ''


def _normalize_grade_images(images_b64: list, max_side: int = 2048) -> list:
    """★ #1300（2026-10-29）批改图受控归一化：手机原图 3000px+ 直接喂视觉引擎，
    视觉 token 爆炸把 prefill 拖死（批改慢+超时实锤）；统一缩到 2048px 长边——
    卷子手写小字仍清晰（旧版看图链 1280px 压糊是脑补根因），token 量受控。
    单张失败原图兜底（base64 裸串），绝不拦批改。"""
    out = []
    for b in images_b64:
        raw = b.split(',', 1)[1] if b.startswith('data:') else b
        try:
            import base64 as _b64
            import io as _io
            from PIL import Image as _PImage
            im = _PImage.open(_io.BytesIO(_b64.b64decode(raw))).convert('RGB')
            w, h = im.size
            if max(w, h) > max_side:
                _sc = max_side / float(max(w, h))
                im = im.resize((max(1, int(w * _sc)), max(1, int(h * _sc))), _PImage.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, 'JPEG', quality=90)
            out.append(_b64.b64encode(buf.getvalue()).decode())
        except Exception:
            out.append(raw)   # 归一化失败 → 原图兜底，不拦批改
    return out


# ★ #1302 最近一次批改实际用的视觉方（grade_homework 收尾读它写进返回标注）
_last_vision_route = ''


def _vision_call_raw(images_b64: list, prompt: str):
    """视觉调用（多图）——返回 (content, finish_reason)。
    finish_reason='length' 表示输出被 max_tokens 截断（#1196 实锤：部分视觉模型如 glm-4v-flash 上限 1024，
    #1202 起批改跟随当前外脑，各模型上限不同，截断续批逻辑按 finish_reason 通吃）。
    ★ #1302（2026-10-29 定版拍板）批改分流：卷子批改=高精度活 → **外脑视觉优先**
    （8B 本地读整页密集卷子在能力边缘，没做的题被脑补成对错实锤）；
    没配外脑 / 当前外脑不支持看图 → 落回本地 Eye 通道（如实标注 _last_vision_route，绝不静默冒充）。
    普通看图（eye_ask 等）不受影响，仍跟随用户选择。"""
    global _last_vision_route
    try:
        provider_id, model_name, key, base_url = _current_vision_provider()
        _last_vision_route = f'外脑视觉 {model_name}'
    except Exception:
        # 第二级：当前主模型是本地 → 试外脑槽位（用户在云槽选过的支持看图的外脑）
        try:
            provider_id, model_name, key, base_url = _cloud_slot_vision_provider()
            _last_vision_route = f'外脑视觉 {model_name}（外脑槽）'
        except Exception:
            # 第三级：真没有可用外脑视觉 → 落回本地 Eye（如实标注，绝不冒充）
            _last_vision_route = '视觉引擎本地视觉'
            return _eye_vision_call_raw(images_b64, prompt)
    import urllib.request
    content = [{'type': 'text', 'text': prompt}]
    for b in images_b64:
        if b.startswith('data:'):
            b = b.split(',', 1)[1]
        content.append({'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b}'}})
    body = {'model': model_name, 'messages': [{'role': 'user', 'content': content}], 'stream': False}
    # ★ 2026-11-12 慢批改真凶实锤：glm-5.3-Flash 是「始终思考」模型（thinking:disabled 被
    #   智谱拒 400），视觉请求不带 reasoning_effort 会触发默认深度思考——同一张作业图
    #   裸调 300s+（手机批改 120s 必超时实锤，TimeoutError 英文原文直接冒给手机）；
    #   带 low 档对照实验 66.4s 批完。与对话链 2026-10-21 治理完全同款：
    #   读 config.yaml zhipu_reasoning_effort（low/high/max），默认 low。其余外脑不受影响。
    if provider_id == 'zhipu':
        try:
            _effort = 'low'
            _cfgp = Path(__file__).resolve().parent.parent.parent / 'config.yaml'
            if _cfgp.exists():
                import yaml as _yaml
                _zc = (_yaml.safe_load(_cfgp.read_text(encoding='utf-8')) or {}).get('zhipu_reasoning_effort')
                if _zc in ('low', 'high', 'max'):
                    _effort = _zc
            body['reasoning_effort'] = _effort
        except Exception:
            body['reasoning_effort'] = 'low'
    req = urllib.request.Request(f'{base_url}/chat/completions',
                                 data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
    try:
        # ★ 2026-11-12 timeout 120→180：low 档实测 66.4s，留大卷面/多图波动余量
        with urllib.request.urlopen(req, timeout=180) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8')[:150]
        except Exception:
            pass
        raise RuntimeError(f'视觉调用失败({provider_id}/{model_name}): {detail or e}')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # ★ 2026-11-12 超时/网络异常转中文友好话术（不回落通道——选谁给谁铁律，
        #   老谭 11-12 拍板：用户选定的外脑就是外脑，失败如实报，绝不偷偷换通道）
        raise RuntimeError(f'外脑「{model_name}」批改请求超时或网络异常（已等 180 秒）。'
                           '这页题可能太多/照片太大——请离题目远一点拍（一页少些题）或分开拍两张再试；'
                           '反复失败可到 设置→模型中心 换一个外脑再试')
    ch = out['choices'][0]
    return ch['message']['content'], ch.get('finish_reason') or ''


def _vision_call(images_b64: list, prompt: str) -> str:
    """视觉调用旧签名（兼容既有调用方）——只返回 content"""
    content, _fr = _vision_call_raw(images_b64, prompt)
    return content


def _vision_json(images_b64: list, prompt: str, max_rounds: int = 3, detect_subject: bool = False):
    """★ #1196（2026-10-25）截断自动续批：finish_reason=length 时带着已批题号再打一枪，最多 max_rounds 轮。
    背景：glm-4v-flash max_tokens 锁死 1024，题多（25+）的作业页 JSON 必截断 → 解析 0 条 → 误报照片问题。
    ★ #1252 detect_subject=True：从模型原文抽「@@科目:XX」标记（subject='自动' 时配合使用），
    返回 (items, subject)；否则返回 items（兼容旧调用方）。"""
    items, done_nos = [], []
    subject_hit = ''
    p = prompt
    for rnd in range(max_rounds):
        content, fr = _vision_call_raw(images_b64, p)
        if detect_subject and not subject_hit:
            import re as _re
            _m = _re.search(r'@@科目[:：]\s*([^\n,@#]{1,10})', content)
            if _m:
                subject_hit = _m.group(1).strip()[:10]
        batch = _extract_json(content)
        if batch:
            seen = {str(it.get('no')) for it in items}
            for it in batch:
                no = str(it.get('no'))
                if no and no in seen:
                    continue
                items.append(it)
                if no:
                    done_nos.append(no)
        if fr != 'length':
            break
        # 截断了：下一枪只批剩余题号（题多分枪打，一枪装不下）
        remain = ', '.join(done_nos[-30:]) if done_nos else '（前一批输出被截断）'
        p = (prompt + f"\n\n【重要】上一批输出在 {len(items)} 题处被截断，已批题号：{remain}。"
             "请只输出还没批过的剩余题目（从中断的下一题继续），同样只输出 JSON 数组，不要重复已批题号。")
    return (items, subject_hit) if detect_subject else items


def _extract_json(text: str):
    """从模型回复里抠 JSON 数组（容忍 ```json 包裹/前后废话）"""
    import re
    m = re.search(r'\[[\s\S]*\]', text)
    if not m:
        return _extract_json_lenient(text)   # ★ #1201：数组没闭合（截断）也能抠
    try:
        return json.loads(m.group(0))
    except Exception:
        return _extract_json_lenient(text)


def _extract_json_lenient(text: str):
    """★ #1201（2026-10-25）截断容错解析：E2E 实锤 glm-4v-flash 输出在 1024 token 拦腰断，
    数组没有闭合 ] → 旧正则永远解析空 → 续批循环每枪都是空，白续。
    现在用花括号配对从残缺文本里抠出所有【完整】的 {…} 对象（残缺的半截对象自动丢弃）。"""
    objs, depth, start = [], 0, -1
    in_str, esc = False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        objs.append(json.loads(text[start:i + 1]))
                    except Exception:
                        pass
                    start = -1
    return objs


def grade_homework(images_b64: list, child: str = '', subject: str = '数学', note: str = '') -> dict:
    """两步批改管线：
    ① 答案图（最后一张，或唯一一张）→ 提取标准答案 [{no, answer, kp}]
    ② 作业图 + 标准答案 → 对照批改 [{no, question, student_answer, correct_answer, wrong, knowledge_point}]
    只有一张图时：一步批改（视觉直接判对错，标注待人工确认）。"""
    ensure_tables()
    if not images_b64:
        return {'success': False, 'error': '缺少图片（拍作业，最好连老师的答案一起拍）'}
    # ★ #1300 批改图受控归一化（2048px 长边）：治手机原图 token 爆炸 + 分辨率受控不再压糊
    try:
        images_b64 = _normalize_grade_images(images_b64)
    except Exception as _nge:
        logger.debug(f'[StudyCoach] 批改图归一化跳过: {_nge}')
    imgs = images_b64[:3]
    try:
        # ① 标准答案提取
        std = []
        if len(imgs) >= 2:
            ans_img = imgs[-1]
            t1 = ("这是老师发的作业答案页。提取每道题的标准答案，只输出 JSON 数组，"
                  "格式：[{\"no\":\"题号\",\"answer\":\"正确答案\",\"kp\":\"考察知识点\"}]，不要其他文字。")
            std = _vision_json([ans_img], t1)
        # ② 对照批改
        work_imgs = imgs[:-1] if len(imgs) >= 2 else imgs
        std_txt = json.dumps(std, ensure_ascii=False) if std else '（无标准答案图，请按题目常识判断，并把不确定的项 uncertain 设为 true）'
        _auto_subject = (subject or '').strip() in ('', '自动', '自动识别')
        t2 = (f"这是学生的作业页。学科：{subject}。{'学生：' + child if child else ''}\n"
              f"标准答案：{std_txt}\n"
              "逐题对照批改，只输出 JSON 数组，格式："
              "[{\"no\":\"题号\",\"question\":\"题目摘要(20字内)\",\"student_answer\":\"学生答案\","
              "\"correct_answer\":\"正确答案\",\"wrong\":true/false,\"kp\":\"考察知识点\",\"uncertain\":true/false}]。"
              "字迹看不清的题 uncertain=true 且不要判错。不要其他文字。")
        if _auto_subject:
            # ★ #1252 科目自动识别：用户没说科目 → 让模型判断，末行输出 @@科目:XX 标记
            t2 += ("\n\n【学科判断】上面写的学科是「自动」——请根据作业内容判断真实学科（语文/数学/英语/物理/化学等），"
                   "按上述格式输出批改 JSON 数组后，另起一行单独输出：@@科目:学科名（如 @@科目:语文）")
        if _auto_subject:
            items, subject = _vision_json(work_imgs, t2, detect_subject=True)
            subject = subject or '数学'
        else:
            items = _vision_json(work_imgs, t2)
        if not items:
            # ★ #1196 诚实报错：区分「模型调用失败」和「真没识别出题目」（旧文案把截断误报成照片问题）
            return {'success': False,
                    'error': '没批出题目：可能是照片不够清晰，也可能是这页题太多批不完——请离题目远一点拍（一页少些题），或分开拍两张再试'}
        # ★ #1194（2026-10-25）数据归一：child 空兜底取家庭第一位成员（旧账 id=3 child='' 根因）
        if not child:
            try:
                from src.core.panel_manager import get_panel_manager
                _ms = get_panel_manager().get_members()
                if _ms:
                    child = str(_ms[0].get('name') or '')[:20]
            except Exception:
                child = ''
        # ③ 入库：错题进错题本（同知识点+同题干 → wrong_count+1）
        wrongs = [it for it in items if it.get('wrong') and not it.get('uncertain')]
        conn = _conn()
        for it in wrongs:
            kp = str(it.get('kp') or '').strip()[:60]
            q = str(it.get('question') or '').strip()[:120]
            old = conn.execute(
                "SELECT id, wrong_count FROM study_wrong_book WHERE child=? AND knowledge_point=? AND question=? AND status='active'",
                (child, kp, q)).fetchone()
            if old:
                conn.execute("UPDATE study_wrong_book SET wrong_count=wrong_count+1 WHERE id=?", (old['id'],))
            else:
                conn.execute(
                    "INSERT INTO study_wrong_book (child, subject, question, student_answer, correct_answer, knowledge_point) VALUES (?,?,?,?,?,?)",
                    (child, subject, q, str(it.get('student_answer') or '')[:200],
                     str(it.get('correct_answer') or '')[:200], kp))
        total = len(items)
        wrong_n = len(wrongs)
        rate = round((total - wrong_n) / total * 100, 1) if total else 0
        # ★ #1252 逐题明细落库（豆包式展示的数据家——网页/手机详情都从这里读）
        # ★ #1253 photo 字段：批改完由照片链回填（attach_photo），先落记录占位
        cur = conn.execute(
            "INSERT INTO study_grades (child, subject, total, wrong, correct_rate, knowledge_points, note, detail) VALUES (?,?,?,?,?,?,?,?)",
            (child, subject, total, wrong_n, rate,
             json.dumps([it.get('kp') for it in items if it.get('kp')], ensure_ascii=False), note[:200],
             json.dumps(items, ensure_ascii=False)))
        _grade_id = cur.lastrowid
        conn.commit()
        conn.close()
        # ★ #1194 数据真家归一：成绩→成长档案「作业记录」域，错题→「错题」域
        #   （insert_domain 钩子自动汇入薄弱点图谱），电脑「学习成长中心」可见。失败不挡批改。
        archived = False
        _hw_rid = 0
        try:
            from src.core import kid_archive as _ka
            _kids = _ka.get_children()
            if _kids:
                _cid = next((k['id'] for k in _kids if k.get('name') == child), None)
                if _cid is None:
                    _cid = next((k['id'] for k in _kids if k.get('is_default')), _kids[0]['id'])
                _today = datetime.now().strftime('%Y-%m-%d')
                # ★ #1253 接住作业记录 id——照片回填定位用（不再满表猜最新一条）
                _hw_rid = _ka.insert_domain(_cid, 'homework', {
                    'date': _today, 'subject': subject,
                    'content': f'拍照批改 {total} 题，错 {wrong_n} 题（正确率 {rate}%）',
                    'wrong_count': str(wrong_n),
                    'notes': (note or '手机拍照批改')[:200]}) or 0
                for it in wrongs:
                    _ka.insert_domain(_cid, 'mistakes', {
                        'date': _today, 'subject': subject,
                        'question': str(it.get('question') or '')[:200],
                        'wrong_reason': (str(it.get('kp') or '').strip() or '未标注知识点')[:50],
                        'correct': f"学生答:{it.get('student_answer', '')} / 正确:{it.get('correct_answer', '')}",
                        'notes': '手机拍照批改'})
                archived = True
        except Exception as _ae:
            logger.warning(f'[StudyCoach] 成长档案归一跳过: {_ae}')
        # ★ #1209/#1207 数据中枢挂钩（2026-10-25）：批改完 → 画像趋势写回 + 错题多→管家议程主动开口
        try:
            from src.core.member_hub import after_grade
            _weak = [it.get('kp') for it in items if it.get('kp')]
            _mh = after_grade(child, subject, total, wrong_n, weak_points=_weak, note=note)
            logger.info(f"[StudyCoach] 中枢挂钩: noted={_mh.get('noted')} alerted={_mh.get('alerted')}")
        except Exception as _mhe:
            logger.debug(f'[StudyCoach] 中枢挂钩跳过: {_mhe}')
        uncertain = [it for it in items if it.get('uncertain')]
        return {'success': True, 'items': items, 'total': total, 'wrong': wrong_n,
                'correct_rate': rate, 'uncertain_count': len(uncertain), 'child': child,
                'archived': archived,
                'vision_model': _last_vision_route,   # ★ #1302 批改方如实标注
                'grade_id': _grade_id, 'hw_id': _hw_rid,   # ★ #1253 照片回填定位
                'disclaimer': 'AI 批改供参考' + ('，其中 %d 题字迹待人工确认' % len(uncertain) if uncertain else '')}
    except Exception as e:
        logger.exception('[StudyCoach] 批改失败')
        return {'success': False, 'error': str(e)[:200]}


def attach_photo(grade_id: int, photo_url: str, hw_id: int = 0) -> dict:
    """★ #1253 批改照片回填：study_grades.photo + 成长档案 kid_homework.photo。
    photo_url 是可访问的图片地址（/api/panels/image/xx 或 /data/media/kid/...），
    电脑面板 <img src> 直接可显——修「电脑上作业打不开没照片」实锤。失败不挡批改。"""
    if not grade_id or not photo_url:
        return {'success': False}
    try:
        conn = _conn()
        conn.execute("UPDATE study_grades SET photo=? WHERE id=?", (str(photo_url)[:300], grade_id))
        if hw_id:
            try:
                conn.execute("UPDATE kid_homework SET photo=? WHERE id=?", (str(photo_url)[:300], hw_id))
            except Exception:
                pass
        conn.commit()
        conn.close()
        return {'success': True}
    except Exception as e:
        logger.warning(f'[StudyCoach] 批改照片回填失败: {e}')
        return {'success': False, 'error': str(e)[:120]}


def wrongbook_summary(child: str = '') -> dict:
    """错题本：按知识点聚合（次数排序），含最近题目明细"""
    ensure_tables()
    conn = _conn()
    where, args = ("WHERE child=? AND status='active'", [child]) if child else ("WHERE status='active'", [])
    rows = conn.execute(f"SELECT * FROM study_wrong_book {where} ORDER BY wrong_count DESC, created_at DESC", args).fetchall()
    conn.close()
    kps = {}
    for r in rows:
        k = r['knowledge_point'] or '未标注知识点'
        kps.setdefault(k, {'knowledge_point': k, 'count': 0, 'questions': []})
        kps[k]['count'] += r['wrong_count']
        if len(kps[k]['questions']) < 3:
            kps[k]['questions'].append({'question': r['question'], 'wrong_count': r['wrong_count']})
    return {'success': True, 'total': len(rows),
            'by_knowledge': sorted(kps.values(), key=lambda x: -x['count'])}


def resolve_wrong(item_id: int) -> dict:
    ensure_tables()
    conn = _conn()
    conn.execute("UPDATE study_wrong_book SET status='resolved', last_review=datetime('now','localtime') WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return {'success': True}


def weekly_report(child: str = '') -> dict:
    """学情周报：近 14 天批改统计 + 外脑教练建议（强制含休息/鼓励，语气参考沟通偏好）"""
    ensure_tables()
    conn = _conn()
    where, args = ("WHERE child=? ", [child]) if child else ('', [])
    since = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
    grades = conn.execute(
        f"SELECT * FROM study_grades {where}{'AND' if where else 'WHERE'} created_at>=? ORDER BY created_at",
        args + [since]).fetchall()
    wb = conn.execute(
        f"SELECT knowledge_point, SUM(wrong_count) c FROM study_wrong_book {where}{'AND' if where else 'WHERE'} status='active' GROUP BY knowledge_point ORDER BY c DESC LIMIT 5",
        args).fetchall()
    style = ''
    if child:
        m = conn.execute("SELECT comm_style FROM panel_members WHERE name=?", (child,)).fetchone()
        style = m['comm_style'] if m else ''
    conn.close()
    if not grades:
        return {'success': True, 'has_data': False,
                'message': '近 14 天还没有批改记录——先拍一次作业吧'}
    import statistics
    rates = [g['correct_rate'] for g in grades]
    trend = '上升' if rates[-1] > rates[0] + 3 else ('下降' if rates[-1] < rates[0] - 3 else '平稳')
    weak = [w['knowledge_point'] for w in wb]
    avg = round(statistics.mean(rates), 1)
    # 外脑教练建议（失败降级本地模板）
    advice = ''
    try:
        # ★ 2026-10-22 方案A：教练建议跟随当前外脑（mm 统一管线），失败降级本地模板
        from src.core.multi_model_manager import get_multi_model_manager
        prompt = (f"你是孩子的学习教练（不是监工）。孩子{('叫' + child) if child else ''}，"
                  f"近两周 {len(grades)} 次作业批改：平均正确率 {avg}%，趋势{trend}，"
                  f"薄弱知识点：{'、'.join(weak) if weak else '暂无明显薄弱'}。"
                  f"沟通偏好：{style or '耐心鼓励式'}。"
                  "给家长一段 150 字内的教练建议，必须包含：①先肯定一个进步点 ②薄弱点怎么练（具体可执行）"
                  "③一条休息/放松安排（保护学习兴趣）。语气：先肯定再指不足，不说笨/粗心这类词。")
        mm = get_multi_model_manager()
        chunks = []
        for ev in mm.generate_response([{'role': 'user', 'content': prompt}], stream=True):
            t, c = (ev[0], ev[1]) if isinstance(ev, (tuple, list)) and len(ev) >= 2 else (None, '')
            if t in ('content', 'text') and c:
                chunks.append(str(c))
            elif t in ('error',):
                raise RuntimeError(str(c)[:80])
        advice = ''.join(chunks).strip()
    except Exception as e:
        logger.warning(f'[StudyCoach] 教练建议降级(当前外脑不可用): {e}')
    if not advice:
        advice = (f"近两周平均正确率 {avg}%，趋势{trend}。"
                  + (f"建议重点练「{weak[0]}」，每天 3 题专项即可。" if weak else '保持当前节奏。')
                  + "记得给孩子留出玩的时间——学得开心才学得久。")
    return {'success': True, 'has_data': True, 'count': len(grades), 'avg_rate': avg,
            'trend': trend, 'weak_points': weak, 'advice': advice,
            'grades': [{'id': g['id'], 'date': g['created_at'][:10], 'rate': g['correct_rate'],
                        'total': g['total'], 'wrong': g['wrong'],
                        'child': g['child'] or '', 'subject': g['subject'] or '',
                        'photo': (g['photo'] if 'photo' in g.keys() else '') or ''} for g in grades]}


def list_children() -> dict:
    """★ #1490（2026-11-12）A 套餐①：孩子切换清单。
    来源并集：成长档案 children 表（有档案 id/is_default）∪ 批改/错题表 distinct child（历史账兜底，
    含「小雅」这类在成员表但没建成长档案的名字）。按出现批改次数排序，有档案的排前面。"""
    ensure_tables()
    conn = _conn()
    kids = []
    try:
        from src.core import kid_archive as _ka
        for k in (_ka.get_children() or []):
            kids.append({'name': str(k.get('name') or ''), 'is_default': bool(k.get('is_default')),
                         'has_archive': True, 'grade_n': 0})
    except Exception:
        pass
    known = {k['name'] for k in kids if k['name']}
    cnt = {}
    for r in conn.execute("SELECT child, COUNT(*) n FROM study_grades WHERE child!='' GROUP BY child"):
        cnt[r['child']] = cnt.get(r['child'], 0) + r['n']
    for r in conn.execute("SELECT child, COUNT(*) n FROM study_wrong_book WHERE child!='' AND status='active' GROUP BY child"):
        cnt[r['child']] = cnt.get(r['child'], 0) + r['n']
    for name, n in cnt.items():
        hit = next((k for k in kids if k['name'] == name), None)
        if hit:
            hit['grade_n'] = n
        else:
            kids.append({'name': name, 'is_default': False, 'has_archive': False, 'grade_n': n})
    conn.close()
    kids.sort(key=lambda k: (not k['is_default'], -k['grade_n']))
    return {'success': True, 'children': kids}


def _week_start(d: datetime = None) -> str:
    """本周周一日期（YYYY-MM-DD）"""
    d = d or datetime.now()
    return (d - timedelta(days=d.weekday())).strftime('%Y-%m-%d')


def grade_analysis(grade_id: int, force: bool = False) -> dict:
    """★ #1490（2026-11-12）A 套餐②：AI 分析卡——薄弱知识点排名 / 错因归类 / 同类题练习建议 / 家长辅导话术。
    按 grade_id 缓存落 study_analysis（一次批改分析一次）；跟随当前外脑（mm 统一管线，不回落偷换），
    外脑失败降级本地统计模板并如实标注 gen_by。"""
    ensure_tables()
    conn = _conn()
    if not force:
        row = conn.execute("SELECT * FROM study_analysis WHERE grade_id=?", (grade_id,)).fetchone()
        if row:
            conn.close()
            return {'success': True, 'cached': True, 'content': row['content'],
                    'gen_by': row['gen_by'], 'created_at': row['created_at']}
    g = conn.execute("SELECT * FROM study_grades WHERE id=?", (grade_id,)).fetchone()
    if not g:
        conn.close()
        return {'success': False, 'error': '批改记录不存在'}
    child = g['child'] or ''
    try:
        detail = json.loads(g['detail'] or '[]')
    except Exception:
        detail = []
    bad = [it for it in detail if it.get('wrong') or it.get('uncertain')]
    wb = conn.execute(
        "SELECT knowledge_point, SUM(wrong_count) c FROM study_wrong_book WHERE child=? AND status='active' GROUP BY knowledge_point ORDER BY c DESC LIMIT 5",
        (child,)).fetchall() if child else []
    conn.close()
    weak_txt = '、'.join(f"{w['knowledge_point']}(错{w['c']}次)" for w in wb) or '暂无'
    bad_txt = json.dumps([{'no': it.get('no'), 'question': it.get('question'),
                           'student': it.get('student_answer'), 'correct': it.get('correct_answer'),
                           'kp': it.get('kp'), 'uncertain': bool(it.get('uncertain'))}
                          for it in bad], ensure_ascii=False) or '（本次全对）'
    prompt = (f"你是孩子的学习教练。孩子{('叫' + child) if child else ''}，"
              f"本次批改：共 {g['total']} 题错 {g['wrong']} 题（正确率 {g['correct_rate']}%），学科 {g['subject']}。\n"
              f"错题/待确认明细：{bad_txt}\n"
              f"该孩子近 14 天高频薄弱知识点：{weak_txt}\n"
              "请输出四部分（用这四个小标题，各不超过 100 字，总共 400 字内，说人话不堆术语）：\n"
              "📉 薄弱知识点排名（按严重度排序，说明为什么）\n"
              "🔍 错因归类（计算失误/概念没吃透/审题不仔细/字迹待确认等，对应到题）\n"
              "✏️ 同类题练习建议（针对最弱的知识点出 3~5 道同类题，附答案）\n"
              "💬 家长辅导话术（怎么跟孩子说，先肯定再指不足，不说笨/粗心）")
    content, gen_by = '', ''
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        chunks = []
        for ev in mm.generate_response([{'role': 'user', 'content': prompt}], stream=True):
            t, c = (ev[0], ev[1]) if isinstance(ev, (tuple, list)) and len(ev) >= 2 else (None, '')
            if t in ('content', 'text') and c:
                chunks.append(str(c))
            elif t in ('error',):
                raise RuntimeError(str(c)[:80])
        content = ''.join(chunks).strip()
        gen_by = '外脑'
    except Exception as e:
        logger.warning(f'[StudyCoach] AI 分析卡当前模型不可用: {e}')
    # ★ #1490 第二级（照 #1302 批改分流先例）：当前模型是本地/Eye 用不了 → 试外脑槽
    #   （用户在云槽自己选过的外脑，非代选）；再不行才落本地模板并如实标注
    if not content:
        try:
            pid, model_name, key, base_url = _cloud_slot_vision_provider()
            import urllib.request as _ur
            body = {'model': model_name, 'messages': [{'role': 'user', 'content': prompt}], 'stream': False}
            if pid == 'zhipu':
                body['reasoning_effort'] = 'low'   # ★ 与批改链同款：5.3-Flash 始终思考治理
            req = _ur.Request(f'{base_url}/chat/completions',
                              data=json.dumps(body).encode(),
                              headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
            with _ur.urlopen(req, timeout=90) as resp:
                out = json.loads(resp.read())
            content = (out['choices'][0]['message'].get('content') or '').strip()
            if content:
                gen_by = f'外脑槽 {model_name}'
        except Exception as e:
            logger.warning(f'[StudyCoach] AI 分析卡外脑槽也不可用: {e}')
    if not content:
        kps = {}
        for it in bad:
            k = (it.get('kp') or '').strip() or '未标注知识点'
            kps[k] = kps.get(k, 0) + 1
        rank = sorted(kps.items(), key=lambda x: -x[1])
        content = (f"📉 薄弱知识点排名\n" + ('\n'.join(f"{i+1}. {k}（本次错 {n} 题）" for i, (k, n) in enumerate(rank[:5])) or '本次全对，无明显薄弱点') +
                   f"\n\n🔍 错因归类\n本次错 {g['wrong']} 题" + ('，其中 %d 题字迹待人工确认。' % sum(1 for it in bad if it.get('uncertain')) if any(it.get('uncertain') for it in bad) else '。') +
                   "\n\n✏️ 同类题练习建议\n（当前外脑不可用，暂无 AI 出题——可针对上面薄弱点在练习册找同类题，每天 3 题。）" +
                   "\n\n💬 家长辅导话术\n先肯定孩子这次的正确率（%.0f%%），再用「我们一起看看这几道」的口吻陪改错，别用审问语气。" % (g['correct_rate'] or 0))
        gen_by = '本地模板'
    conn = _conn()
    conn.execute("INSERT INTO study_analysis (grade_id, child, content, gen_by) VALUES (?,?,?,?) "
                 "ON CONFLICT(grade_id) DO UPDATE SET content=excluded.content, gen_by=excluded.gen_by",
                 (grade_id, child, content, gen_by))
    conn.commit()
    conn.close()
    return {'success': True, 'cached': False, 'content': content, 'gen_by': gen_by,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M')}


def weekly_report_archived(child: str = '', week: str = '') -> dict:
    """★ #1490 A 套餐③：学情周报（每孩子每周一份，存档可回看）。
    week='' → 本周（有缓存直接回，没有生成落库）；week='YYYY-MM-DD' → 回看那份历史周报。
    stats 摘要落 study_weekly.stats（前端周报卡头部展示，不用再查一遍）。"""
    ensure_tables()
    week = week or _week_start()
    conn = _conn()
    row = conn.execute("SELECT * FROM study_weekly WHERE child=? AND week_start=?",
                       (child, week)).fetchone()
    if row:
        conn.close()
        try:
            stats = json.loads(row['stats'] or '{}')
        except Exception:
            stats = {}
        return {'success': True, 'cached': True, 'week_start': week, 'content': row['content'],
                'stats': stats, 'created_at': row['created_at']}
    r = weekly_report(child=child)   # 复用 14 天统计+教练建议管线
    if not r.get('has_data'):
        conn.close()
        return {'success': False, 'error': r.get('message', '近 14 天还没有批改记录')}
    content = (f"📊 本周学情（{week} 起）\n"
               f"近两周 {r['count']} 次批改 · 平均正确率 {r['avg_rate']}% · 趋势{r['trend']}\n"
               + (f"薄弱知识点：{'、'.join(r['weak_points'])}" if r['weak_points'] else '暂无明显薄弱点')
               + f"\n\n🧑‍🏫 教练建议\n{r['advice']}")
    stats = {'count': r['count'], 'avg_rate': r['avg_rate'], 'trend': r['trend'],
             'weak_points': r['weak_points']}
    conn = _conn()
    conn.execute("INSERT OR IGNORE INTO study_weekly (child, week_start, content, stats) VALUES (?,?,?,?)",
                 (child, week, content, json.dumps(stats, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return {'success': True, 'cached': False, 'week_start': week, 'content': content,
            'stats': stats, 'created_at': datetime.now().strftime('%Y-%m-%d %H:%M')}


def weekly_list(child: str = '') -> dict:
    """★ #1490 A 套餐③：往期周报清单（该孩子所有存过的周报，新的在前）"""
    ensure_tables()
    conn = _conn()
    rows = conn.execute("SELECT id, child, week_start, stats, created_at FROM study_weekly WHERE child=? ORDER BY week_start DESC",
                        (child,)).fetchall() if child else \
        conn.execute("SELECT id, child, week_start, stats, created_at FROM study_weekly ORDER BY week_start DESC").fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            stats = json.loads(r['stats'] or '{}')
        except Exception:
            stats = {}
        out.append({'id': r['id'], 'child': r['child'], 'week_start': r['week_start'],
                    'avg_rate': stats.get('avg_rate'), 'count': stats.get('count'),
                    'created_at': r['created_at']})
    return {'success': True, 'total': len(out), 'items': out}


def weekly_by_id(wid: int) -> dict:
    ensure_tables()
    conn = _conn()
    row = conn.execute("SELECT * FROM study_weekly WHERE id=?", (wid,)).fetchone()
    conn.close()
    if not row:
        return {'success': False, 'error': '周报不存在'}
    try:
        stats = json.loads(row['stats'] or '{}')
    except Exception:
        stats = {}
    return {'success': True, 'week': {'id': row['id'], 'child': row['child'],
            'week_start': row['week_start'], 'content': row['content'], 'stats': stats}}


# ════════════════ 路由（Blueprint）════════════════
from flask import Blueprint, request, jsonify

bp = Blueprint('study_coach', __name__, url_prefix='/api/study')


@bp.route('/grade', methods=['POST'])
def api_grade():
    body = request.get_json(silent=True) or {}
    imgs = body.get('images') or []
    r = grade_homework([str(x) for x in imgs][:3],
                       child=str(body.get('child', '')).strip()[:20],
                       subject=str(body.get('subject', '自动')).strip()[:20],
                       note=str(body.get('note', '')).strip()[:200])
    return jsonify(r), (200 if r.get('success') else 500)


@bp.route('/grades/<int:gid>', methods=['GET'])
def api_grade_detail(gid):
    """★ #1252 批改详情（逐题明细）——学习成长中心点记录展开用"""
    ensure_tables()
    conn = _conn()
    row = conn.execute("SELECT * FROM study_grades WHERE id=?", (gid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': '记录不存在'}), 404
    d = dict(row)
    try:
        d['detail'] = json.loads(d.get('detail') or '[]')
    except Exception:
        d['detail'] = []
    return jsonify({'success': True, 'grade': d})


@bp.route('/grades/<int:gid>/photo', methods=['POST'])
def api_grade_photo(gid):
    """★ #1253 电脑端批改照片挂载：前端传作业图 dataURL → 落图片库 → 回填 photo。
    （手机链照片已存图片库，cloud_link 直接用已有 url 调 attach_photo，不走这里）"""
    body = request.get_json(silent=True) or {}
    data_url = str(body.get('dataUrl', ''))
    if not data_url.startswith('data:image'):
        return jsonify({'success': False, 'error': '没有图片'}), 400
    try:
        from src.core import panel_images
        r = panel_images.save_image('study_center', data_url, note='作业批改照片', tag='learn')
        if not r:
            return jsonify({'success': False, 'error': '照片保存失败'}), 500
        attach_photo(gid, r['url'])
        return jsonify({'success': True, 'url': r['url']})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:120]}), 500


@bp.route('/wrongbook', methods=['GET'])
def api_wrongbook():
    return jsonify(wrongbook_summary(child=request.args.get('child', '').strip()))


@bp.route('/wrongbook/resolve', methods=['POST'])
def api_resolve():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(resolve_wrong(int(body.get('id', 0))))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/report', methods=['GET'])
def api_report():
    return jsonify(weekly_report(child=request.args.get('child', '').strip()))


# ═══ ★ #1490（2026-11-12）A 套餐新路由：孩子清单 / AI 分析卡 / 周报存档 ═══

@bp.route('/children', methods=['GET'])
def api_children():
    return jsonify(list_children())


@bp.route('/analysis', methods=['GET'])
def api_analysis():
    try:
        gid = int(request.args.get('grade_id', 0))
    except ValueError:
        return jsonify({'success': False, 'error': 'grade_id 不合法'}), 400
    if not gid:
        return jsonify({'success': False, 'error': '缺少 grade_id'}), 400
    force = request.args.get('force', '') in ('1', 'true')
    r = grade_analysis(gid, force=force)
    return jsonify(r), (200 if r.get('success') else 500)


@bp.route('/weekly', methods=['GET'])
def api_weekly():
    r = weekly_report_archived(child=request.args.get('child', '').strip(),
                               week=request.args.get('week', '').strip())
    return jsonify(r), (200 if r.get('success') else 404)


@bp.route('/weekly/list', methods=['GET'])
def api_weekly_list():
    return jsonify(weekly_list(child=request.args.get('child', '').strip()))


@bp.route('/weekly/<int:wid>', methods=['GET'])
def api_weekly_detail(wid):
    r = weekly_by_id(wid)
    return jsonify(r), (200 if r.get('success') else 404)
