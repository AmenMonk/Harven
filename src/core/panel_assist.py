# -*- coding: utf-8 -*-
"""
对话即录入引擎（面板助手 Panel Assist）★ 2026-09-16
====================================================
三通道录入体系的后端核心：
  通道① 主聊天默录：动词触发 + 域关键词路由 + 低置信度进待确认队列（默认开，设置可关）
  通道② 页签对话框：按域 schema 结构化抽取 → 前端确认卡 → 一键入库
  通道③ 手动表单：panel_manager 原有流程不变（兜底）

★ 2026-09-18 三级抽取引擎（已定案：外脑为标准 + 第一优先省 token）：
  ① 个人词典/关键词快车道 —— 命中 0 token、零延迟、离线可用
  ② 外脑抽取 —— 两段式省 token：先发「域名菜单」小 prompt 选域（max_tokens=40），
     再只带命中域的字段抽取（max_tokens=400），历史裁到 2 轮
  ③ 本地模型 —— 兜底（无外脑 key / 断网 / 隐私严格档），免费
  红线不变：数据不出本机可配置（extract_allow_cloud，默认开；严格档用户关掉即纯本地）
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PANELS_DIR = BASE_DIR / 'data' / 'panels'
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'

_lock = threading.Lock()
_home_archiving = set()

# ══════════════════════════ 触发词与域关键词 ══════════════════════════

# 第一层：记录意图触发词（动词/强事件信号）
RECORD_TRIGGERS = [
    '记一笔', '记录', '记一下', '记上', '记个', '帮我记', '存一下', '记下', '打卡',
    '考了', '考砸', '测验', '月考', '交作业', '考试成绩',
    '花了', '花费', '买单', '付款', '充值', '缴费', '发了工资', '收了红包',
    '加油', '保养', '换机油', '洗车', '年检', '轮胎',
    '打了疫苗', '疫苗', '发烧', '感冒', '体检', '复查', '量了', '称了', '身高', '体重',
    '喂了', '浇了', '浇水', '施肥', '换盆', '修剪',
    '提醒我', '帮我提醒', '提醒', '待办', '别忘了', '记得去',
    '买了', '到货', '过期', '库存',
    # ★ #1432（2026-11-04）存入类动词：带图说「存入面板…」此前不认 → 默录链跳过、
    #   被看图直答截胡（笔记本实锤）。补存放入动词（「存入银行」会带「银行/元」走记账域，不冲突）
    '存入', '存进', '存到', '入库', '放进', '收好',
]

# 纯提问特征（命中则不入档，只回答）
QUESTION_HINTS = ['怎么', '为什么', '如何', '是什么', '吗？', '吗?', '？', '?', '能不能', '可不可以', '介绍', '讲解']

# 第二层：域关键词路由（面板 → 关键词，命中计分）
DOMAIN_KEYWORDS = {
    'accounting_daily_1': ['花费', '花了', '块钱', '元', '记账', '账', '买菜', '缴费', '充值', '工资', '红包', '学费', '水电', '外卖'],
    'car_maintenance_1': ['车', '加油', '保养', '机油', '轮胎', '洗车', '年检', '车险', '油耗'],
    'daily_todo_1': ['提醒我', '提醒', '待办', '要做', '任务', '别忘了', '记得去', '开会'],
    # ★ 2026-09-19 #791 食物存放重做：补采购/临期/补货词
    'fridge_stock_1': ['冰箱', '食材', '菜买了', '牛奶', '鸡蛋', '过期', '库存', '剩菜', '冷藏', '冷冻', '零食', '调料',
                       '补货', '采购', '临期', '快过期', '放冰箱', '冰箱里', '冻饺子', '酸奶', '剩饭'],
    # ★ 2026-10-11 #1099 健康中枢四面板：关键词改指 health_file + 补健身/成长
    'health_file': ['健康档案', '体检', '血压', '血糖', '体检报告', '复查', '就医', '挂号', '疫苗', '指标'],
    'fitness_log': ['健身', '运动', '跑步', '锻炼', '体重', '睡眠', '千卡', '卡路里', '游泳'],
    'child_growth': ['身高', '视力', '接种', '成长曲线', '儿保'],
    'pet_care_1': ['宠物', '狗', '猫', '喂', '狗粮', '猫粮', '驱虫', '遛'],
    'plants_1': ['绿植', '花', '浇水', '施肥', '换盆', '修剪', '黄叶'],
    'smart_home_1': ['灯', '空调', '滤网', '传感器', '智能家居', '网关', '扫地机器人'],
    'kid_archive': ['孩子', '儿子', '女儿', '作业', '考试', '成绩', '分数', '考了', '错题', '数学', '语文', '英语',
                    '物理', '化学', '学校', '老师', '同学', '身高', '体重', '兴趣班', '特长班', '应用题',
                    '测验', '排名', '月考', '期中', '期末', '听写', '口算', '视力', '近视', '进度', '预习',
                    '零花钱', '压岁钱', '头围'],
    # ★ 2026-09-16 新增家庭模块
    'home_calendar': ['日程', '日历', '约会', '家长会', '聚会', '生日会', '出行'],
    'home_items': ['收纳', '收进', '放好了', '物品', '螺丝刀', '工具箱', '储物', '换季', '收起来',
                   '借给', '还了', '翻出来', '低库存', '放回',
                   # ★ #1432：物品存放域补「存储/存放」（「存入面板电脑存储 书房」实锤缺位）
                   '存储', '存放', '收纳盒', '收纳箱'],
    'elder_care': ['老人', '爷爷', '奶奶', '姥姥', '姥爷', '服药', '吃药', '降压药', '血糖药'],
    # ★ 2026-10-12 #1103 老家庭药箱退役：药箱词条改指新版 med_cabinet（字段全+效期提醒+AI 管家）
    'med_cabinet': ['家庭药箱', '药箱', '药品', '吃药', '服药', '感冒药', '消炎药', '退烧药', '创可贴', '保健品', '处方药'],
    'home_docs': ['证件', '保单', '护照', '身份证', '户口本', '驾驶证', '房产证'],
    'home_utilities': ['水费', '电费', '燃气费', '物业费', '宽带费', '暖气费'],
    'family_todo': ['全家', '分工', '家务', '大扫除'],
    'home_favors': ['人情', '随礼', '份子钱', '礼金', '探望'],
    'home_repair': ['维修', '报修', '漏水', '坏了', '修一下', '水管', '电路'],
}

# kid_archive 的域关键词（供 LLM 选域参考，也用于关键词加分）
# ★ 2026-09-18 学习域扩容：作业/考试/进度/视力独立成域，learning 保留做泛化兜底
KID_DOMAIN_HINTS = {
    'exam': ['考试', '测验', '小测', '月考', '期中', '期末', '排名', '考了', '成绩', '分数', '及格', '满分'],
    # ★ 2026-09-19 #792 习惯打卡上线：打卡/练字从作业分家给 habits，作业留 作业/听写/背诵/口算 等
    'homework': ['作业', '练习题', '口算', '听写', '背诵', '写字', '口算题'],
    # ★ 2026-09-19 #792 reading 补 hints（此前缺位，"阅读打卡"全被 homework 抢走）
    'reading': ['阅读打卡', '阅读', '读书', '绘本', '看书'],
    # ★ 2026-09-19 #792 学习P1：习惯打卡域（连续天数自动算）
    'habits': ['习惯打卡', '打卡', '坚持', '连续第', '跳绳', '背单词', '练字', '晨读', '仰卧起坐', '口算打卡', '练字打卡', '没打卡'],
    'progress': ['进度', '年级', '学到', '预习', '第几课', '第几单元', '自学', '同步'],
    'vision': ['视力', '近视', '远视', '散光', '眼镜', '验光', '查眼睛'],
    'mistakes': ['错题', '错了', '不会'],
    'learning': ['学习', '上课', '听课', '复习', '功课', '晚自习'],
    'health': ['疫苗', '生病', '发烧', '吃药', '体检', '感冒'],
    'growth': ['身高', '体重', '长了', '头围', '量身高', '称体重', '长个', '厘米', '公斤'],
    'money': ['零花钱', '压岁钱', '零用钱', '存钱'],
    'traits': ['喜欢', '讨厌', '害怕', '希望', '兴趣', '性格'],
    'relations': ['同学', '老师', '朋友', '吵架'],
    'clothing': ['衣服', '鞋', '码', '尺码', '穿'],
    'items': ['玩具', '书包', '水壶', '物品', '丢了', '放在'],
    'games': ['游戏', '玩手机', '平板'],
    'interests': ['兴趣班', '美术', '钢琴', '游泳', '跆拳道', '书法', '舞蹈'],
    'diet': ['吃了', '挑食', '过敏', '忌口', '爱吃', '不爱吃'],
}

# ══════════════════════════ 三级抽取引擎 ★ 2026-09-18 ══════════════════════════
# ① 快车道（词典/关键词，0 token）→ ② 当前阵营模型
# ★ #891 杀回落（2026-09-27 已定案）：外脑/本地按 current_model 选谁给谁，
#   失败如实报错，严禁跨阵营切换（旧版"cloud挂了落本地/local挂了落外脑"已根除）

_EXTERNAL_PREF = ['deepseek', 'doubao', 'qwen', 'zhipu', 'glm', 'kimi', 'moonshot',
                  'hunyuan', 'baichuan', 'siliconflow', 'openai', 'gemini', 'groq', 'openrouter']


def _external_providers():
    """读 multi_model_manager 配置，返回已配 key 的外脑列表（按优先序）。
    每项 (provider_id, base_url, api_key, model_name)。不动 mm 的 current_model 状态。"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager, PROVIDERS
    except Exception:
        return []
    try:
        mm = get_multi_model_manager()
        provs = mm._get_all_providers()
        models_cfg = (getattr(mm, 'config', None) or {}).get('models', {}) or {}
        keys_cfg = (getattr(mm, 'config', None) or {}).get('model_keys', {}) or {}
        cur = (getattr(mm, 'config', None) or {}).get('current_model', '') or ''
        ids = []
        if cur and cur != 'ollama':
            ids.append(cur)
        ids += [p for p in _EXTERNAL_PREF if p != cur]
        ids += [p for p in provs if p not in ids and p != 'ollama' and p != cur]
        out = []
        for pid in ids:
            if pid not in provs and pid not in PROVIDERS:
                continue
            entry = models_cfg.get(pid) or {}
            key = entry.get('api_key') or keys_cfg.get(pid) or ''
            if not key:
                continue
            prov = provs.get(pid) or PROVIDERS.get(pid) or {}
            base = entry.get('base_url') or prov.get('base_url') or ''
            model_name = entry.get('model_name') or (prov.get('models') or [''])[0]
            if base and model_name:
                out.append((pid, str(base).rstrip('/'), key, model_name))
        return out
    except Exception as e:
        logger.debug(f"[PanelAssist] 读外脑配置失败: {e}")
        return []


def _active_camp():
    """★ #891 杀回落（已定案）：按 current_model 判定当前激活阵营，选谁给谁不乱窜。
    返回 (camp, current_model)：current_model 非空且非 ollama = ('cloud', cur)；否则 ('local', cur)。
    ★ #1380（2026-11-02）：harveneye 是本地混合模型引擎（#1243 看图链已认，文本链补齐同口径）——
    cur='harveneye' 判本地阵营，不再误撞「外脑没配好」（生成今日食谱误报实锤）。"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        cur = str(((getattr(mm, 'config', None) or {}).get('current_model', '') or '')).strip().lower()
    except Exception:
        cur = ''
    if cur == 'harveneye':
        return ('local', cur)
    return ('cloud', cur) if cur and cur != 'ollama' else ('local', cur or 'ollama')


_ASSIST_GATE_LABEL = {
    'extract_allow_cloud': '录入抽取允许外脑',
    'answer_allow_cloud': '档案问答允许外脑',
}


def _llm_call(system, user, max_tokens=400, temperature=0.1, prefer=None, gate='extract_allow_cloud'):
    """★ #891 杀回落：按阵营直连，严禁跨阵营切换。返回 (text, model_label, err)。
    阵营判定：config.yaml current_model 非空且非 ollama = 外脑阵营；否则本地阵营。
      - 外脑失败 → 只报外脑错误（HTTP 码/配置问题），绝不静默落本地
      - 本地失败 → 只报本地错误，绝不静默走外脑
    prefer=None 跟随当前阵营；'cloud'/'local'=显式指定阵营（如蒸馏门2用户自选），同样不回落。
    gate：外脑阵营下检查的设置开关（extract_allow_cloud / answer_allow_cloud），关了如实报错不切本地。
    ★ 省 token 三板斧：短提示词 + max_tokens 上限 + 历史裁剪（调用方负责）"""
    if prefer == 'cloud':
        camp, cur = 'cloud', _active_camp()[1]
    elif prefer == 'local':
        camp, cur = 'local', 'ollama'
    else:
        camp, cur = _active_camp()

    if camp == 'cloud':
        if get_assist_settings().get(gate, 'true') != 'true':
            gname = _ASSIST_GATE_LABEL.get(gate, gate)
            return '', '', (f'当前是外脑模式（{cur}），但设置里「{gname}」开关关着，这个操作走不了。'
                            '到「设置 → 面板助手」打开开关，或把模型切回本地。')
        provs = _external_providers()
        target = next((p for p in provs if p[0] == cur), None)
        # ★ #1380（2026-11-02）：harveneye 已在 _active_camp 判本地阵营（对齐 #1243 看图口径），
        #   #1241 那段「不能在外脑模式使用」报错永远到不了，撤下——本地引擎走本地分支
        if target is None:
            if prefer == 'cloud' and provs:
                target = provs[0]  # 显式指定外脑（蒸馏门2）且当前模型是本地：用第一个可用外脑（外脑内部选择，不算跨阵营）
            else:
                return '', '', f'外脑 {cur} 没配好（key 或模型名缺失）。到「设置 → 模型」检查后重试。'
        pid, base, key, model_name = target
        try:
            import requests as _rq
            from src.core.multi_model_manager import get_multi_model_manager, bypass_independence
            mm = get_multi_model_manager()
            prov = mm._get_all_providers().get(pid) or {}
            headers = mm._build_auth_headers(pid, key, prov)
            payload = {'model': model_name, 'stream': False, 'temperature': temperature,
                       'max_tokens': max_tokens,
                       'messages': [{'role': 'system', 'content': system},
                                    {'role': 'user', 'content': user}]}
            if pid == 'qwen':
                payload['enable_thinking'] = False
            # ★ 2026-09-27 修空回复 v2：放宽重试条件——除「思考吃光额度(finish=length+有
            # reasoning)」外，还有「思考完正文仍空但 finish=stop」漏网（主聊天流式版同病根）。
            # 首战空回复一律加大 max_tokens 重试（上限 8192），不再挑 finish 原因，仍空才报错
            _retry_left = 2 if pid == 'zhipu' else 0
            while True:
                with bypass_independence():
                    r = _rq.post(f"{base}/chat/completions", headers=headers,
                                 json=payload, timeout=60)
                if r.status_code != 200:
                    last_err = f'HTTP {r.status_code}'
                    logger.debug(f"[PanelAssist] 外脑 {pid} 调用失败: {last_err}")
                    break
                _msg = (r.json().get('choices') or [{}])[0].get('message', {})
                _fin = (r.json().get('choices') or [{}])[0].get('finish_reason', '')
                txt = (_msg.get('content') or '').strip()
                if txt:
                    return txt, f'{pid}:{model_name}', ''
                if _retry_left > 0:
                    _retry_left -= 1
                    payload['max_tokens'] = min(int(payload['max_tokens'] * 3), 8192)
                    logger.info(f"[PanelAssist] {pid} 空回复(finish={_fin or '无'})，加大到 {payload['max_tokens']} 重试")
                    continue
                last_err = ('空回复（推理模型的思考过程太长，把回答额度吃光了）'
                            if _fin == 'length' and (_msg.get('reasoning_content') or '').strip()
                            else '空回复')
                break
        except Exception as e:
            last_err = str(e)
            logger.debug(f"[PanelAssist] 外脑 {pid} 调用失败: {e}")
        return '', '', (f'外脑 {pid}:{model_name} 调用失败（{last_err}）。不会偷偷换本地模型答；'
                        '请检查网络和外脑配置后重试。')

    # 本地阵营：只走本地，失败如实报错
    from src.core.kid_archive import call_local_llm
    txt, model, err = call_local_llm(user, system, timeout=300, temperature=temperature)
    if not err:
        return txt, model, ''
    return '', '', f'本地模型没响应（{err}）。不会偷偷换外脑答；请检查本地模型是否在运行。'


# ────────── 个人词典（确认卡「记住分法」沉淀，快车道 0 token 命中） ──────────

def list_lexicon(panel_id=''):
    c = _conn()
    try:
        if panel_id:
            rows = c.execute("SELECT * FROM assist_lexicon WHERE panel_id=? ORDER BY hits DESC, id DESC",
                             (panel_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM assist_lexicon ORDER BY hits DESC, id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ★ #995 词典闭环（2026-10-05 已批准）：确认卡确认后自动沉淀叫法
#   词典定位（定版拍板）：问法→面板映射记忆器，不存答案；命中=跳模型直进面板快车道
_LEXICON_SKIP_KEYS = {
    '日期', '时间', '备注', 'notes', 'note', 'date', 'time', '金额', '分数', 'score',
    'minutes', '身高', '体重', 'height', 'weight', 'value', '数量', 'count', 'qty',
    'member', '成员', 'kind', '来源', '方向', '周期', '状态',
    '优先级', '重复', '前置提醒', '分类', 'category', '数量单位', '单位', 'unit',
    '负责人', '经手人', 'person', 'who', '经手', '买家', '店员',
}
# 属性枚举值/过泛分类词不记（记了会误路由：用户说「买点日用品」不该被「日用」劫走）
_LEXICON_SKIP_VALUES = {
    '支出', '收入', '待办', '进行中', '已完成', '已付', '跳过', '高', '中', '低',
    '一次性', '每周', '每月', '每季度', '每年', '不提醒', '不重复', '在存', '吃完了',
    '过期扔了', '要补货', '餐饮', '交通', '车辆', '日用', '孩子', '医疗', '人情',
    '缴费', '娱乐', '宠物', '绿植', '维修', '其他', '冷藏', '冷冻', '门格',
}


def _pick_lexicon_words(records):
    """从确认入库的记录里挑可记的「叫法」（指向性强的名词：商家/物品/项目名等）。
    规则：跳过数字/过长/字段黑名单/属性枚举值，去重，最多 3 条（交 add_lexicon 再净化）。"""
    import re as _re
    words = []
    for rec in (records or []):
        if not isinstance(rec, dict):
            continue
        for k, v in rec.items():
            v = str(v or '').strip()
            if not v or _re.search(r'\d', v) or len(v) > 12:
                continue
            if k in _LEXICON_SKIP_KEYS or str(k).lower() in _LEXICON_SKIP_KEYS:
                continue
            if v in _LEXICON_SKIP_VALUES:
                continue
            if v not in words:
                words.append(v)
    return words[:3]


def add_lexicon(patterns, panel_id, domain, source='confirm'):
    """沉淀叫法映射。patterns: list[str]，去重截断，最多存 3 条。"""
    import re as _re
    clean = []
    for p in (patterns or []):
        p = str(p or '').strip()
        if not p or _re.search(r'\d', p):
            continue
        if 1 <= len(p) <= 12 and p not in clean:
            clean.append(p)
        if len(clean) >= 3:
            break
    if not clean or not panel_id:
        return 0
    n = 0
    with _lock:
        c = _conn()
        try:
            for p in clean:
                cur = c.execute("SELECT id FROM assist_lexicon WHERE pattern=?", (p,)).fetchone()
                if cur:
                    c.execute("UPDATE assist_lexicon SET panel_id=?, domain=? WHERE id=?",
                              (panel_id, domain or '', cur['id']))
                else:
                    c.execute("INSERT INTO assist_lexicon(pattern, panel_id, domain, source) VALUES(?,?,?,?)",
                              (p, panel_id, domain or '', source))
                n += 1
            c.commit()
        finally:
            c.close()
    return n


def delete_lexicon(lid):
    with _lock:
        c = _conn()
        try:
            c.execute("DELETE FROM assist_lexicon WHERE id=?", (lid,))
            c.commit()
        finally:
            c.close()
    return True


def _lexicon_pick(text, panel_id=''):
    """词典快车道：pattern 在话里命中即返回 (panel_id, domain, pattern)。pattern 越长越优先。"""
    if not text:
        return None
    c = _conn()
    try:
        if panel_id:
            rows = c.execute("SELECT * FROM assist_lexicon WHERE panel_id=? ORDER BY LENGTH(pattern) DESC",
                             (panel_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM assist_lexicon ORDER BY LENGTH(pattern) DESC").fetchall()
    finally:
        c.close()
    for r in rows:
        if r['pattern'] and r['pattern'] in text:
            try:
                with _lock:
                    cc = _conn()
                    cc.execute("UPDATE assist_lexicon SET hits=hits+1 WHERE id=?", (r['id'],))
                    cc.commit()
                    cc.close()
            except Exception:
                pass
            return r['panel_id'], r['domain'], r['pattern']
    return None


def _kid_fastpick(text):
    """kid_archive 域关键词快车道（0 token）。命中返回 (domain, 'keyword')，否则 ('', '')。"""
    if not text:
        return '', ''
    best, best_n = '', 0
    for dom, kws in KID_DOMAIN_HINTS.items():
        n = sum(1 for k in kws if k in text)
        if n > best_n:
            best, best_n = dom, n
    return (best, 'keyword') if best else ('', '')


def _pick_system_lean(today):
    """★ 省 token 选域 prompt：只发「键=名」菜单（~80 token），不发字段表。"""
    from src.core.kid_archive import DOMAINS
    menu = '、'.join(f'{k}={v[2]}' for k, v in DOMAINS.items() if k not in ('game_logs', 'interest_logs'))
    return ("从用户这句话里选出最贴切的一个档案域。可选域：" + menu + "。"
            "只输出JSON：{\"domain\":\"键名\"}。今天是" + today + "。只输出JSON，不要解释。")


def _parse_records_json(raw):
    """★ 2026-09-27 解析加固：GLM 抽取有时不按规矩输出（对象包 records/前后带解释话/
    markdown 围栏），旧版只 find('[')..rfind(']') 一级抠取，失败即报「模型输出解析失败」
    且原始输出不留痕、无从诊断（09-27 凌晨浮窗带图记账实录：同一句话一次成功一次失败）。
    多级抠取：整段 JSON → 抠数组 → 抠对象（records/data/items/list 字段或单条记录整体兜底）。
    返回 list[dict]（可为空数组=模型说没内容）；None=彻底解析不了（调用方报错）。
    下游调用方都按 valid 字段键过滤，脏键自动丢弃，这里放宽兜底是安全的。"""
    import re as _re
    t = (raw or '').strip()
    t = _re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=_re.S).strip()
    if not t:
        return None

    def _load(cand):
        try:
            return json.loads(cand)
        except Exception:
            return None

    obj = _load(t)
    if obj is None and '[' in t and ']' in t:
        obj = _load(t[t.find('['):t.rfind(']') + 1])
    if obj is None and '{' in t and '}' in t:
        obj = _load(t[t.find('{'):t.rfind('}') + 1])

    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for k in ('records', 'data', 'items', 'list'):
            v = obj.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        return [obj]  # 单条记录对象——下游 valid 过滤会把不相干键丢掉
    logger.warning(f"[PanelAssist] 抽取输出解析失败，原始输出前240字: {t[:240]!r}")
    return None


def _extract_with_fields(text, domain, fields, history=None, image_b64='', today=None):
    """★ 省 token 抽取：只带命中域的字段 schema，历史裁到 2 轮，max_tokens=400。
    返回 (records, model, err)。"""
    from src.core.kid_archive import DOMAINS
    label = DOMAINS.get(domain, (0, 0, domain))[2] if domain else ''
    schema = {'label': label or domain, 'fields': fields}
    system = _extract_system(schema, today or datetime.now().strftime('%Y-%m-%d'))
    hist_txt = ''
    for h in (history or [])[-2:]:
        role = '用户' if h.get('role') == 'user' else '你'
        hist_txt += f"{role}: {str(h.get('text', ''))[:120]}\n"
    prompt = ((f"历史：\n{hist_txt}" if hist_txt else '') + f"用户消息：{text}{image_b64}")
    raw, model, err = _llm_call(system, prompt, max_tokens=400, temperature=0.1)
    if err:
        return [], '', err
    arr = _parse_records_json(raw)
    if arr is None:
        return [], '', '模型这次没按格式回话（内容没解析开），再发一次通常就好；还不行请截图到「设置 → 关于」反馈'
    valid = {f['key'] for f in fields}
    recs = []
    for r in arr:
        clean = {k: str(v)[:500] for k, v in r.items() if k in valid}
        if clean and any(str(v).strip() for v in clean.values()):
            recs.append(clean)
    return recs, model, ''


# ══════════════════════════ 设置 ══════════════════════════

_ASSIST_DEFAULTS = {
    'home_record_enabled': 'true',    # 通道① 主聊天默录总开关（默认开，面板可关）
    'auto_insert': 'false',           # true=AI抽取直接入库；false=确认卡模式（用户拍板）
    'extract_allow_cloud': 'true',    # ★ 录入抽取允许走外脑（省token两段式）；false=纯本地（隐私严格档）
    # ★ D1（2026-10-11 定版拍板）：默认改 true。档案问答跟随用户当前模型阵营——选了外脑就用外脑，
    #   互不代答；旧默认 false 是历史省钱设计，会莫名拦住 AI分析/浮窗问答/食谱/手机问答/定时汇总，
    #   存量 false 由 get_assist_settings() 一次性迁移改 true（标记键防重复触发）
    'answer_allow_cloud': 'true',
    # ★ 2026-09-27 定版拍板：定时汇总三件套各配开关，默认全关（用户想自动跑自己打开）。
    # ★ 开发者纠正：汇总跟随用户当前模型阵营（外脑/本地均可——用户有权选任何模式，只建议不干预；
    # 隐私板块 UI 文案建议本地），外脑阵营受 answer_allow_cloud 开关管，失败报本阵营错不回落
    'summary_dream_weekly': 'false',      # 梦境周总结（每周一 09:30）
    'summary_accounting_monthly': 'false',  # 记账月报（每月1日 09:40）
    'summary_kid_monthly': 'false',       # 孩子成长月报（每月1日 09:50）
    'summary_fridge_expiry': 'false',     # ★ 2026-10-05 #1011 冰箱临期每日提醒（每天 09:20，默认关）
    'bills_auto_scan': 'true',            # ★ #1377（2026-11-02 定版拍板）账单相册每日自动分析（默认开）：
                                          #   心跳每日块扫未标记账单照→抽取→待确认卡，防重靠照片级标记
    'summary_meal_weekly': 'false',       # ★ 2026-10-10 #1063 饮食周报（每周一 09:45，默认关）
    'summary_health_weekly': 'false',     # ★ 2026-10-11 #1073 健康周报（每周一 09:55，默认关）
    # ★ #1155（2026-10-16 开发者两轮修正定案）：看图用谁——auto=跟随当前模式（默认）/
    #   local=固定本地 / cloud=固定外脑（用当前外脑）。选谁给谁、不行如实报、不兜底不代选
    'vision_routing': 'auto',
}


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── ★ 2026-09-18 物品分类存放：快答（"充电宝在哪"零 token 直查） + 低库存/借出提醒 ──

# 触发词：命中且能抠出物品名才走快答（不抢正常提问/记录）
_STORAGE_QK = ('在哪里', '在哪个', '在哪', '放在哪', '放哪了', '放哪里', '放到哪',
               '找一下', '帮我找', '找找', '找到')
_STORAGE_FILL = ('我的', '家里的', '家里', '请问', '一下', '的', '了', '吗', '呢', '啊', '呀',
                 '？', '?', '。', '，', '！', '!', ' ')
# 位置后缀词（"充电宝在哪个抽屉"→剥成"充电宝"再查）
_STORAGE_PLACE = ('抽屉', '柜子', '柜', '盒子', '箱子里', '箱子', '收纳箱', '架上', '架子', '层')

# 建库/查库的列名（物品存放面板字段固定为这些中文名）
_STORAGE_COLS = ('物品名称', '分类', '位置·房间', '位置·柜子家具', '位置·盒层',
                 '存放位置', '数量', '状态', '借给谁', '借出日期', '备注')


def _storage_tables():
    """返回物品存放类面板的数据表名（★ 2026-09-18 home_items 就地升级为台账，两种前缀都认）"""
    try:
        c = _conn()
        try:
            return [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "(name LIKE 'panel_item_storage%' OR name LIKE 'panel_home_items%')")]
        finally:
            c.close()
    except Exception:
        return []


def _storage_pick_kw(t):
    """从'我的充电宝在哪'抠出'充电宝'；抠不出来返回空串"""
    kw = t
    for k in _STORAGE_QK:
        kw = kw.replace(k, '')
    for ch in _STORAGE_FILL:
        kw = kw.replace(ch, '')
    return kw.strip()


def _storage_search(kw):
    """直查物品存放表。返回命中记录列表（跨多份面板合并，按id倒序）"""
    hits = []
    like = f'%{kw}%'
    for tb in _storage_tables():
        try:
            c = _conn()
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                usable = [col for col in _STORAGE_COLS if col in cols]
                conds = ' OR '.join(f'"{col}" LIKE ?' for col in ('物品名称', '分类', '备注') if col in cols)
                if not conds:
                    continue
                sql = f'SELECT * FROM "{tb}" WHERE ({conds}) ORDER BY id DESC LIMIT 5'
                for r in c.execute(sql, (like,) * sql.count('?')):
                    hits.append((tb, {k: r[k] for k in r.keys() if r[k] not in (None, '')}))
            finally:
                c.close()
        except Exception as e:
            logger.debug(f'[storage] 查询 {tb} 失败: {e}')
    return hits


def _storage_where_fast(t, panel_id=''):
    """物品存放快答：'充电宝在哪' → 直查位置字段，0 token 不走模型。
    返回 (answer, model)；返回 ('','') 表示不拦截、走后续流程。
    ★ 拦截策略：在物品存放面板对话框里问 → 必答（查不到也如实说没记录）；
      在其他地方问 → 查到了才答（"公园在哪"这类非物品问题不会被拦）。"""
    if not _storage_tables():
        return '', ''
    # ★ #1057 五类升级先拦：位置反查/季节/借出状态（零 token）
    v2 = _storage_where_fast_v2_gate(t, panel_id)
    if v2:
        return v2
    kw = _storage_pick_kw(t)
    if not kw or len(kw) > 20:
        return '', ''
    hits = _storage_search(kw)
    if not hits:
        # ★ #1057 别名归一兜底：'衣橱' → 档案规范名'大衣柜' 再查一次
        try:
            from src.core.storage_places import normalize_alias
            kw_n = normalize_alias(kw)
            if kw_n and kw_n != kw:
                hits = _storage_search(kw_n)
                if hits:
                    kw = kw_n
        except Exception:
            pass
    if not hits:
        kw2 = kw
        for w in _STORAGE_PLACE:
            kw2 = kw2.replace(w, '')
        kw2 = kw2.strip()
        if kw2 and kw2 != kw:
            hits = _storage_search(kw2)
            if hits:
                kw = kw2
    in_panel = (panel_id or '').startswith('item_storage') or (panel_id or '').startswith('home_items')
    if not hits:
        if not in_panel:
            return '', ''  # 不是在物品面板里问的，放行给正常问答流程
        return (f'📦 档案里没找到「{kw}」的存放记录。'
                f'找到后到「物品存放台账」记一条，下次一问就知道了。'), '⚡ 快车道'
    lines = ['📦 找到了：']
    for _tb, r in hits[:5]:
        pos = ' → '.join(str(r.get(k, '')) for k in ('位置·房间', '位置·柜子家具', '位置·盒层') if r.get(k)) \
            or str(r.get('存放位置') or '') or '（没填位置）'
        extra = []
        if r.get('数量'):
            try:
                _q = float(r['数量'])
                _q = int(_q) if _q == int(_q) else _q
            except (TypeError, ValueError):
                _q = r['数量']
            extra.append(f'数量{_q}')
        if r.get('状态') and r['状态'] != '在用':
            extra.append(str(r['状态']))
        if r.get('状态') == '借出' and r.get('借给谁'):
            extra.append(f'借给{r["借给谁"]}' + (f'（{r["借出日期"]}借出）' if r.get('借出日期') else ''))
        lines.append(f'• {r.get("物品名称", kw)} —— {pos}' + (f'（{"，".join(extra)}）' if extra else ''))
    return '\n'.join(lines), '⚡ 快车道'


# ── ★ #1057 查询链升级：位置反查/季节查询/借出状态/别名归一/跨家 ──
#   零 token 词典快答，全部在 _storage_where_fast 入口处拦截。

_STORAGE_WHAT = ('有什么', '放了啥', '放的啥', '放的什么', '装了啥', '装了什么',
                 '都有啥', '都有什么', '放的东西', '收纳了啥', '收纳了什么')
_STORAGE_SEASONS = ('冬天', '冬季', '夏天', '夏季', '春秋', '秋季', '秋天')
_STORAGE_BORROW_KW = ('借给谁', '谁借', '借走了', '借出去了', '还在借', '借出')

_CN_DIG = {'零': '0', '一': '1', '二': '2', '两': '2', '三': '3', '四': '4',
           '五': '5', '六': '6', '七': '7', '八': '8', '九': '9'}


def _cn_num_norm(s):
    """中文数字归一：'左门第二层'→'左门第2层'，'第10层'↔'第十层'互通。"""
    res = []
    for ch in s:
        if ch in _CN_DIG:
            res.append(_CN_DIG[ch])
        elif ch == '十':
            if res and res[-1].isdigit():
                # 二十→2,0 拼 20；十五→十前无数字才进这分支，这里处理 X十
                res.append('0' if len(res[-1]) == 1 else '')
            else:
                res.append('10')
        else:
            res.append(ch)
    # '10' 后跟数字（十五→105）修正为 1X
    out = []
    i = 0
    while i < len(res):
        if res[i] == '10' and i + 1 < len(res) and res[i + 1].isdigit():
            out.append('1')
            out.append(res[i + 1])
            i += 2
        else:
            out.append(res[i])
            i += 1
    return ''.join(out)


def _storage_pos_str(r):
    """行 → 位置路径串（房间 → 容器 → 格位），兼容旧'存放位置'列。"""
    return ' → '.join(str(r.get(k, '')) for k in ('位置·房间', '位置·柜子家具', '位置·盒层') if r.get(k)) \
        or str(r.get('存放位置') or '') or '（没填位置）'


def _storage_place_query(t):
    """位置反查：'衣柜左门第二层有什么' → 档案树逐级匹配（含别名/中文数字归一）→ 查物品位置列。
    返回 (answer, model) 或 None（不是位置反查/没匹配到位置）。"""
    if not any(w in t for w in _STORAGE_WHAT):
        return None
    try:
        from src.core import storage_places as sp
    except Exception:
        return None
    tree = sp.get_tree()
    if not tree:
        return None
    q = _cn_num_norm(t)
    # 逐级匹配：名字或别名出现在问句里 → 记录 (深度, 匹配节点)
    # ★ 修复：容器/格位始终遍历（'大衣柜左门第2层'不用提房间），房间仅在明确提到时设条件
    best = {'room': None, 'cont': None, 'slot': None, 'prop': None}
    for p in tree:
        if _cn_num_norm(p['name']) in q:
            best['prop'] = p['name']
        for room in p['rooms']:
            for cont in room['containers']:
                cnames = [cont['name']] + cont.get('aliases', [])
                if any(_cn_num_norm(n) in q for n in cnames):
                    best['cont'] = cont['name']
                    best['room'] = room['name']   # 容器命中顺带定房间（档案树归属）
                for slot in cont.get('slots', []):
                    snames = [slot['name']] + slot.get('aliases', [])
                    if any(_cn_num_norm(n) in q for n in snames):
                        best['slot'] = slot['name']
                        best['cont'] = cont['name']
                        best['room'] = room['name']
            for slot in room.get('loose_slots', []):
                snames = [slot['name']] + slot.get('aliases', [])
                if any(_cn_num_norm(n) in q for n in snames):
                    best['slot'] = slot['name']
                    best['room'] = best['room'] or room['name']
            # 裸房间名匹配（仅当容器还没定房间时——「卧室有什么」）
            if not best['room'] and _cn_num_norm(room['name']) in q:
                best['room'] = room['name']
    if not (best['room'] or best['cont'] or best['slot']):
        return None
    # 组装查询条件（房间必须，容器/格位有才加）
    hits = []
    for tb in _storage_tables():
        try:
            c = _conn()
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                conds, args = [], []
                if best['room'] and '位置·房间' in cols:
                    conds.append('"位置·房间" LIKE ?')
                    args.append(f'%{best["room"]}%')
                if best['cont'] and '位置·柜子家具' in cols:
                    conds.append('"位置·柜子家具" LIKE ?')
                    args.append(f'%{best["cont"]}%')
                if best['slot'] and '位置·盒层' in cols:
                    conds.append('"位置·盒层" LIKE ?')
                    args.append(f'%{best["slot"]}%')
                if not conds:
                    continue
                sql = f'SELECT * FROM "{tb}" WHERE {" AND ".join(conds)} ORDER BY id DESC LIMIT 15'
                for r in c.execute(sql, args):
                    hits.append({k: r[k] for k in r.keys() if r[k] not in (None, '')})
            finally:
                c.close()
        except Exception as e:
            logger.debug(f'[storage] 位置反查 {tb} 失败: {e}')
    path = ' → '.join(x for x in (best['prop'], best['room'], best['cont'], best['slot']) if x)
    if not hits:
        return (f'📦 「{path}」还没放东西（或物品没填这个位置）。\n'
                f'放进去后到「物品存放台账」记一条，选上位置就能这样反查了。'), '⚡ 快车道'
    lines = [f'📦 {path} 里有 {len(hits)} 样：']
    for r in hits[:15]:
        lines.append(f'• {r.get("物品名称", "?")}' + (f'（{r["数量"]}件）' if r.get('数量') else ''))
    return '\n'.join(lines), '⚡ 快车道'


def _storage_season_query(t):
    """季节查询：'冬天的衣服收在哪' → 查「适用季节」列。"""
    season = next((s for s in _STORAGE_SEASONS if s in t), '')
    if not season:
        return None
    key = season[0]  # 冬/夏/春/秋
    hits = []
    for tb in _storage_tables():
        try:
            c = _conn()
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                if '适用季节' not in cols:
                    continue
                sql = (f'SELECT * FROM "{tb}" WHERE "适用季节" LIKE ? '
                       f'ORDER BY id DESC LIMIT 15')
                for r in c.execute(sql, (f'%{key}%',)):
                    hits.append({k: r[k] for k in r.keys() if r[k] not in (None, '')})
            finally:
                c.close()
        except Exception as e:
            logger.debug(f'[storage] 季节查询 {tb} 失败: {e}')
    if not hits:
        return (f'📦 档案里没有标「{season}」适用的物品。\n'
                f'录入时选上适用季节，换季一问就知道收在哪。'), '⚡ 快车道'
    lines = [f'📦 标了「{season}」的物品（{len(hits)} 件）：']
    for r in hits[:15]:
        lines.append(f'• {r.get("物品名称", "?")} —— {_storage_pos_str(r)}')
    return '\n'.join(lines), '⚡ 快车道'


def _storage_borrow_query(t):
    """借出状态查询：'梯子借给谁了' / '谁借走了' → 查「借给谁/状态」列。"""
    if not any(w in t for w in _STORAGE_BORROW_KW):
        return None
    # ★ 修复：先剥全部借出词再抠物品名；剩泛指词/过短 → 视为泛查（列全部借出）
    kw = t
    for w in _STORAGE_BORROW_KW:
        kw = kw.replace(w, '')
    kw = _storage_pick_kw(kw)
    generic = ('东西', '物品', '玩意', '什么', '家里')
    named = bool(kw and len(kw) >= 2 and not any(g in kw for g in generic))
    hits = []
    for tb in _storage_tables():
        try:
            c = _conn()
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                if '借给谁' not in cols:
                    continue
                conds, args = ['("借给谁" != "" AND "借给谁" IS NOT NULL)'], []
                if named:
                    name_col = '物品名称' if '物品名称' in cols else None
                    if name_col:
                        conds.append(f'"{name_col}" LIKE ?')
                        args.append(f'%{kw}%')
                sql = f'SELECT * FROM "{tb}" WHERE {" AND ".join(conds)} ORDER BY id DESC LIMIT 10'
                for r in c.execute(sql, args):
                    hits.append({k: r[k] for k in r.keys() if r[k] not in (None, '')})
            finally:
                c.close()
        except Exception as e:
            logger.debug(f'[storage] 借出查询 {tb} 失败: {e}')
    if not hits:
        if named:
            return (f'📦 没查到「{kw}」的借出记录——要么没借出去，要么借的时候没记。\n'
                    f'下次借出去记得在台账里把状态改成「借出」、填上借给谁。'), '⚡ 快车道'
        return '📦 目前没有登记借出的物品。', '⚡ 快车道'
    lines = [f'📦 借出记录（{len(hits)} 件）：']
    for r in hits[:10]:
        when = f'（{r["借出日期"]}借出）' if r.get('借出日期') else ''
        lines.append(f'• {r.get("物品名称", "?")} —— 借给 {r.get("借给谁", "?")}{when}')
    return '\n'.join(lines), '⚡ 快车道'


def _storage_where_fast_v2_gate(t, panel_id=''):
    """★ #1057 五类升级入口：位置反查 → 季节 → 借出状态，命中即答；未命中走原物品名逻辑。"""
    for fn in (_storage_place_query, _storage_season_query, _storage_borrow_query):
        try:
            r = fn(t)
        except Exception as e:
            logger.debug(f'[storage] v2 查询 {fn.__name__} 异常: {e}')
            r = None
        if r:
            return r
    return None


# ── ★ 2026-09-26 甲乙（已批准）：全域档案检索 ──
#   甲=首页找东西预查注入（engine 调 archive_quick_context）
#   乙=FC query_panel 工具（builtin_tools 调 archive_search_records）
#   病根：外脑找档案只有文件类工具 → 爬目录搜图片注定落空（面板照片存 sqlite base64，不在文件系统）
_ARCHIVE_INTENT = ('帮我找', '找出', '找一下', '帮我查', '查一下', '查查', '找到', '在哪',
                   '放在哪', '有没有', '记录过', '搜一下', '帮我搜', '存到哪', '放哪里')
_ARCHIVE_EXCLUDE = ('天气', '新闻', '股票', '基金', '汇率', '外卖',        # 外部信息类，档案里没有
                    '存档', '记一下', '记录一下', '帮我记', '帮我存')      # 录入意图，交给正常流程
_ARCHIVE_STOP = ('帮我', '找出', '找一下', '找到', '面板', '档案', '一下', '照片', '图片',
                 '记录', '存档', '哪里', '在哪', '放在哪', '放到哪', '什么', '看看', '一下',
                 '的', '了', '吗', '呢', '吧', '啊', '我', '你', '他', '她', '它', '请',
                 '给', '是', '有', '在', '去', '把', '和', '跟', '与', '或', '这个', '那个', '一个')


def _archive_pick_kws(text):
    """从找东西的话里抠检索词：停用词切开 → 取 >=2 字词块（最多 4 个）"""
    t = text.strip()
    for w in _ARCHIVE_STOP:
        t = t.replace(w, '|')
    kws = []
    for seg in t.split('|'):
        seg = seg.strip()
        if len(seg) >= 2 and seg not in kws and not seg.isdigit():
            kws.append(seg[:12])
    return kws[:4]


def archive_search_records(keywords, panel_id='', max_hits=10):
    """在所有面板表里 LIKE 检索关键词（跳过图片 base64 长值）。
    Returns: [(panel_label, panel_id, slim_dict), ...]"""
    if not keywords:
        return []
    try:
        from src.core.panel_manager import get_panel_manager
        pm = get_panel_manager()
    except Exception:
        return []
    out, seen = [], set()
    try:
        domains = list(list_domains())
    except Exception:
        domains = []
    for item in domains:
        pid = item.get('panel_id', '') if isinstance(item, dict) else ''
        if panel_id and pid != panel_id:
            continue
        panel = pm.get_panel(pid)
        if not panel:
            continue
        try:
            rows = pm.query_panel_data(pid, limit=300)
        except Exception:
            continue
        img_keys = {f.get('name') for f in (panel.fields or []) if f.get('type') == 'image'}
        keys = [f.get('name') for f in (panel.fields or [])]
        for r in rows:
            slim, hit = {}, False
            for k in keys:
                v = r.get(k)
                if not v or k in img_keys:
                    continue
                sv = str(v)
                if len(sv) > 200:      # base64/超长文本跳过
                    continue
                if any(kw in sv for kw in keywords):
                    hit = True
                slim[k] = sv[:60]
            if hit and slim:
                sig = (pid, str(slim)[:120])
                if sig in seen:
                    continue
                seen.add(sig)
                out.append((panel.name, pid, slim))
                if len(out) >= max_hits:
                    return out
    return out


def archive_quick_context(text):
    """首页找东西预查（甲）：命中/未命中都返回注入文本（未命中=告诉外脑别爬目录），不适用返回 ('', False)。
    Returns: (context_str or '', hit: bool)"""
    t = (text or '').strip()
    if not t or len(t) > 60:
        return '', False
    if any(w in t for w in _ARCHIVE_EXCLUDE):
        return '', False
    if not any(w in t for w in _ARCHIVE_INTENT):
        return '', False
    if any(w in t for w in ('花了', '花费', '买了', '记一笔', '账')):   # 记账意图交给智能拆分
        return '', False
    kws = _archive_pick_kws(t)
    if not kws:
        return '', False
    hits = archive_search_records(kws)
    if hits:
        lines = [f'用户正在找：「{t}」。以下是本机面板档案中预查到的相关记录（已自动查好，直接引用回答；'
                 '不要再调用工具去磁盘搜图片——面板照片存在数据库里，文件系统里没有）：']
        for label, pid, slim in hits[:8]:
            body = '｜'.join(f'{k}={v}' for k, v in slim.items())
            lines.append(f'- 【{label}】{body}')
        return '\n'.join(lines), True
    return (f'用户正在找：「{t}」。已预查本机全部面板档案（关键词：{"、".join(kws)}），没有找到相关家庭档案记录。'
            '请如实告诉用户面板档案里还没有这条记录。注意：面板数据和照片都存在数据库里、不在文件系统，'
            '不要用工具去磁盘爬目录找面板记录；但如果用户找的可能是电脑上的文件（下载/文档等），可正常使用文件工具查找。'), False


def _is_storage_query(t):
    """★ 2026-09-18 'X在哪'类找东西问句（装了物品存放面板才生效）。
    命中但查无记录时，走问句检索如实答"没记录"，绝不当成要录入。"""
    if not _storage_tables():
        return False
    if not any(k in t for k in _STORAGE_QK):
        return False
    return bool(_storage_pick_kw(t))


def storage_alerts():
    """★ 2026-09-18 物品存放面板提醒：低库存 + 借出超过7天未还。
    返回 alert item 列表（与 /api/assist/alerts 的 reminders 同构，带 link）。"""
    out = []
    today = datetime.now().date()
    for tb in _storage_tables():
        try:
            c = _conn()
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                need = ('物品名称', '数量', '低库存提醒少于')
                if not all(col in cols for col in need):
                    continue
                for r in c.execute(f'SELECT * FROM "{tb}"'):
                    d = {k: r[k] for k in r.keys()}
                    name = str(d.get('物品名称') or '').strip()
                    if not name:
                        continue
                    # 低库存：填了阈值且数量 ≤ 阈值（排除已淘汰）
                    try:
                        qty = float(d.get('数量') or 0)
                        low = float(d.get('低库存提醒少于') or 0)
                    except (TypeError, ValueError):
                        qty, low = 0.0, 0.0
                    if low > 0 and 0 < qty <= low and (d.get('状态') or '') != '淘汰':
                        out.append({'type': 'storage', 'id': f'{tb}-{r["id"]}-low',
                                    'title': f'📦 {name} 只剩 {int(qty) if qty == int(qty) else qty} 个（低库存，该补货了）',
                                    'module': tb.replace('panel_', '', 1), 'link': '/panel/' + tb.replace('panel_', '', 1)})
                    # 借出超7天未还
                    if (d.get('状态') or '') == '借出' and d.get('借出日期'):
                        try:
                            lend = datetime.strptime(str(d['借出日期'])[:10], '%Y-%m-%d').date()
                            days = (today - lend).days
                            if days > 7:
                                who = str(d.get('借给谁') or '').strip() or '别人'
                                out.append({'type': 'storage', 'id': f'{tb}-{r["id"]}-lend',
                                            'title': f'📦 {name} 借给{who}已经 {days} 天了，该提醒还回来了',
                                            'module': tb.replace('panel_', '', 1), 'link': '/panel/' + tb.replace('panel_', '', 1)})
                        except ValueError:
                            pass
            finally:
                c.close()
        except Exception as e:
            logger.debug(f'[storage] 提醒扫描 {tb} 失败: {e}')
    return out[:10]


# ── ★ 2026-09-19 #791 食物存放快答：「牛奶在哪/鸡蛋还有吗/该买啥」零 token 直查 ──

# 问位置触发词（与物品存放同风格；'哪里'兜更口语的'放冰箱哪里了'）
_FRIDGE_WHERE = ('在哪里', '在哪个', '在哪', '放在哪', '放哪了', '放哪里', '放到哪', '哪里', '找一下', '找找')
# 问库存触发词
_FRIDGE_STOCK = ('还有没有', '还有吗', '还剩多少', '还有多少', '还剩', '剩多少', '剩了',
                 '吃完没', '吃完了没', '过期没', '过期了没', '有没有')
# 采购清单触发词（命中直接列「要补货 + 低库存」）
_FRIDGE_BUY = ('该买啥', '买点啥', '要买啥', '买什么', '买啥', '采购清单', '购物清单', '买菜清单',
               '该补什么', '该买什么', '缺什么')
_FRIDGE_FILL = ('我的', '家里的', '家里', '冰箱', '冰箱里', '请问', '一下', '还有', '还', '帮', '把', '给',
                '我', '放', '拿', '的', '了', '吗', '呢', '啊', '呀', '么', '？', '?', '。', '，', '！', '!', ' ')


def _fridge_tables():
    """食物存放类面板的数据表名"""
    try:
        c = _conn()
        try:
            return [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'panel_fridge_stock%'")]
        finally:
            c.close()
    except Exception:
        return []


def _fridge_pick_kw(t, triggers):
    """从「牛奶还有吗」抠出「牛奶」；抠不出来返回空串"""
    kw = t
    for k in triggers:
        kw = kw.replace(k, '')
    for ch in _FRIDGE_FILL:
        kw = kw.replace(ch, '')
    return kw.strip()


def _fridge_search(kw):
    """按名称模糊查食物记录（排除已吃完/过期扔了）"""
    hits = []
    like = f'%{kw}%'
    for tb in _fridge_tables():
        try:
            c = _conn()
            try:
                sql = (f'SELECT * FROM "{tb}" WHERE "名称" LIKE ? '
                       f"AND (\"状态\" IS NULL OR \"状态\" NOT IN ('吃完了','过期扔了')) "
                       f'ORDER BY id DESC LIMIT 5')
                for r in c.execute(sql, (like,)):
                    hits.append((tb, {k: r[k] for k in r.keys() if r[k] not in (None, '')}))
            finally:
                c.close()
        except Exception as e:
            logger.debug(f'[fridge] 查询 {tb} 失败: {e}')
    return hits


def _fridge_fmt_qty(d):
    """数量+单位拼出来，没数量返回空"""
    q = d.get('数量')
    if q in (None, ''):
        return ''
    try:
        qf = float(q)
        q = int(qf) if qf == int(qf) else qf
    except (TypeError, ValueError):
        pass
    return f'{q}{str(d.get("数量单位") or "")}'


def _fridge_fast(t, panel_id=''):
    """食物存放快答：位置 / 库存 / 采购清单 三种问法，0 token。
    返回 (answer, model)；('', '') 表示不拦截。
    ★ 拦截策略同物品存放：在冰箱面板对话框里问 → 必答（查不到也如实说）；
      其他地方 → 查到了才答（不抢正常问答）。"""
    if not _fridge_tables():
        return '', ''
    in_panel = (panel_id or '').startswith('fridge_stock')

    # 采购清单：不用抠关键词，直接列
    if any(k in t for k in _FRIDGE_BUY):
        out = []
        for tb in _fridge_tables():
            try:
                c = _conn()
                try:
                    cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                    rows = [dict(r) for r in c.execute(f'SELECT * FROM "{tb}"')]
                finally:
                    c.close()
            except Exception:
                continue
            if '名称' not in cols:
                continue
            for d in rows:
                name = str(d.get('名称') or '').strip()
                if not name:
                    continue
                st = str(d.get('状态') or '').strip()
                if st == '要补货':
                    out.append(f'• {name}（你标记了「要补货」）')
                    continue
                if st in ('吃完了', '过期扔了'):
                    continue
                try:
                    qty = float(d.get('数量') or 0)
                    low = float(d.get('低库存提醒少于') or 0)
                except (TypeError, ValueError):
                    qty, low = 0.0, 0.0
                if low > 0 and 0 < qty <= low:
                    out.append(f'• {name}（{_fridge_fmt_qty(d)}，低于低库存线 {int(low)}）')
        if out:
            return '🛒 该补货的清单：\n' + '\n'.join(out[:8]) + \
                   '\n买回来后到「食物存放台账」记一笔就更新了。', '⚡ 快车道'
        if in_panel:
            return '🛒 现在没有要补货的东西：没标记「要补货」，也没有低于低库存线的。', '⚡ 快车道'
        return '', ''

    # 位置问法 → 答分区+层格
    if any(k in t for k in _FRIDGE_WHERE):
        kw = _fridge_pick_kw(t, _FRIDGE_WHERE)
        if not kw or len(kw) > 20:
            return '', ''
        hits = _fridge_search(kw)
        if hits:
            lines = ['🧊 找到了：']
            for _tb, r in hits[:5]:
                pos = ' → '.join(str(r.get(k, '')) for k in ('分区', '位置·层格') if r.get(k)) or '（没填位置）'
                extra = []
                q = _fridge_fmt_qty(r)
                if q:
                    extra.append(q)
                if r.get('到期日'):
                    extra.append(f'{r["到期日"]}到期')
                lines.append(f'• {r.get("名称", kw)} —— {pos}' + (f'（{"，".join(str(x) for x in extra)}）' if extra else ''))
            return '\n'.join(lines), '⚡ 快车道'
        if in_panel:
            return (f'🧊 档案里没找到「{kw}」的存放记录。'
                    f'放进冰箱后到「食物存放台账」记一条，下次一问就知道了。'), '⚡ 快车道'
        return '', ''

    # 库存问法 → 答数量+到期日
    if any(k in t for k in _FRIDGE_STOCK):
        kw = _fridge_pick_kw(t, _FRIDGE_STOCK)
        if not kw or len(kw) > 20:
            return '', ''
        hits = _fridge_search(kw)
        if hits:
            lines = ['🧊 查了一下：']
            for _tb, r in hits[:5]:
                pos = '/'.join(str(r.get(k, '')) for k in ('分区', '位置·层格') if r.get(k))
                parts = [f'{r.get("名称", kw)}（{pos}）' if pos else str(r.get("名称", kw))]
                q = _fridge_fmt_qty(r)
                parts.append(f'还剩 {q}' if q else '数量没记')
                if r.get('到期日'):
                    try:
                        exp = datetime.strptime(str(r['到期日'])[:10], '%Y-%m-%d').date()
                        days = (exp - datetime.now().date()).days
                        if days < 0:
                            parts.append('⚠️ 已过期')
                        elif days <= 3:
                            parts.append(f'⚠️ {days} 天后到期')
                        else:
                            parts.append(f'{days} 天后到期')
                    except ValueError:
                        pass
                lines.append('• ' + '，'.join(str(p) for p in parts))
            return '\n'.join(lines), '⚡ 快车道'
        if in_panel:
            return f'🧊 档案里没有「{kw}」的库存记录（或已经吃完了）。', '⚡ 快车道'
        return '', ''

    return '', ''


def fridge_alerts():
    """★ 2026-09-19 #791 食物存放提醒：已过期滞留 + 低库存 + 要补货。
    ★ 分工不重复：临期（3天内）由 features.remind 挂的提醒负责，这里只报 remind 管不到的。
    返回 alert item 列表（与 /api/assist/alerts 的 reminders 同构，带 link）。"""
    out = []
    today = datetime.now().date()
    for tb in _fridge_tables():
        try:
            c = _conn()
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                if '名称' not in cols:
                    continue
                link = '/panel/' + tb.replace('panel_', '', 1)
                for r in c.execute(f'SELECT * FROM "{tb}"'):
                    d = {k: r[k] for k in r.keys()}
                    name = str(d.get('名称') or '').strip()
                    if not name:
                        continue
                    st = str(d.get('状态') or '').strip()
                    if st in ('吃完了', '过期扔了'):
                        continue
                    # ① 已过期还占着地方（remind 只管"快过期"，过期滞留归这里）
                    if d.get('到期日'):
                        try:
                            exp = datetime.strptime(str(d['到期日'])[:10], '%Y-%m-%d').date()
                            days = (today - exp).days
                            if days > 0:
                                out.append({'type': 'fridge', 'id': f'{tb}-{r["id"]}-expired',
                                            'title': f'🧊 {name} 已经过期 {days} 天了，看看还能不能吃，不行就处理掉',
                                            'module': tb.replace('panel_', '', 1), 'link': link})
                                continue  # 过期了就不用再报低库存
                        except ValueError:
                            pass
                    # ② 低库存
                    try:
                        qty = float(d.get('数量') or 0)
                        low = float(d.get('低库存提醒少于') or 0)
                    except (TypeError, ValueError):
                        qty, low = 0.0, 0.0
                    if low > 0 and 0 < qty <= low:
                        out.append({'type': 'fridge', 'id': f'{tb}-{r["id"]}-low',
                                    'title': f'🧊 {name} 只剩 {_fridge_fmt_qty(d) or "一点点"}（低库存，该补货了）',
                                    'module': tb.replace('panel_', '', 1), 'link': link})
                    # ③ 标记了要补货
                    if st == '要补货':
                        out.append({'type': 'fridge', 'id': f'{tb}-{r["id"]}-restock',
                                    'title': f'🛒 {name} 标记了「要补货」，采购时记得带上',
                                    'module': tb.replace('panel_', '', 1), 'link': link})
            finally:
                c.close()
        except Exception as e:
            logger.debug(f'[fridge] 提醒扫描 {tb} 失败: {e}')
    return out[:10]


def habit_alerts():
    """★ 2026-09-19 #792 习惯打卡漏卡提醒：近7天打过≥2天的习惯，今天还没打 → 提醒。
    表不存在（老库没升级/没打过卡）静默跳过零打扰。"""
    out = []
    today = datetime.now().date()
    try:
        c = _conn()
        try:
            rows = [dict(r) for r in c.execute('SELECT child_id, habit, date, done FROM kid_habits')]
            children = {r['id']: r['name'] for r in c.execute('SELECT id, name FROM children')}
        finally:
            c.close()
    except Exception:
        return out
    by = {}  # (child_id, habit) -> set(dates)
    for d in rows:
        if str(d.get('done') or '') == '没做':
            continue
        _dt = str(d.get('date') or '')[:10]
        _h = str(d.get('habit') or '').strip()
        if not _dt or not _h:
            continue
        by.setdefault((d.get('child_id'), _h), set()).add(_dt)
    for (cid, habit), dts in by.items():
        if today.isoformat() in dts:
            continue  # 今天已经打了
        recent = set()
        for s in dts:
            try:
                gap = (today - datetime.strptime(s, '%Y-%m-%d').date()).days
            except ValueError:
                continue
            if 0 < gap <= 7:
                recent.add(s)
        if len(recent) < 2:
            continue  # 还没形成习惯，不打扰
        who = children.get(cid, '')
        out.append({'type': 'habit', 'id': f'habit-{cid}-{habit}',
                    'title': f"🔥 {''.join([who, '的']) if who else ''}「{habit}」今天还没打卡（近7天坚持了{len(recent)}天）",
                    'module': 'kid_archive', 'link': '/panel/kid_archive'})
    return out[:10]


def mistake_alerts():
    """★ 2026-09-19 错题本提醒（学习方案C）：下次重做日期到了/过了 且 还没掌握 → 提醒重做。
    表不存在（没装错题本）静默跳过零打扰。"""
    out = []
    today = datetime.now().date()
    try:
        c = _conn()
        try:
            tbs = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'panel_mistake_notebook%'")]
            for tb in tbs:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{tb}")')]
                if '下次重做' not in cols or '掌握了吗' not in cols:
                    continue
                for r in c.execute(f'SELECT * FROM "{tb}"'):
                    d = {k: r[k] for k in r.keys()}
                    if (d.get('掌握了吗') or '') in ('还没', ''):
                        redo = str(d.get('下次重做') or '')[:10]
                        if not redo:
                            continue
                        try:
                            rd = datetime.strptime(redo, '%Y-%m-%d').date()
                        except ValueError:
                            continue
                        if rd <= today:
                            subj = str(d.get('科目') or '').strip() or '错题'
                            overdue = (today - rd).days
                            tip = f'已超 {overdue} 天' if overdue > 0 else '就是今天'
                            out.append({'type': 'mistake', 'id': f'{tb}-{r["id"]}-redo',
                                        'title': f'📕 {subj}错题该重做了（{tip}），重做对了就标「重做对了」',
                                        'module': tb.replace('panel_', '', 1),
                                        'link': '/panel/' + tb.replace('panel_', '', 1)})
        finally:
            c.close()
    except Exception as e:
        logger.debug(f'[PanelAssist] 错题提醒查询失败: {e}')
    return out[:8]


def elder_alerts():
    """★ 2026-09-19 老人关怀哨兵（方案A拍板）：替不在身边的儿女盯爸妈的每日状态。
    数据源 panel_elder_checkin_1（每日打卡）/ panel_elder_contact_1（联系与代办），
    表不存在或缺列一律静默跳过（没装老人模块的用户零打扰）。
    规则：
    1. 血压 ≥150/95        → 电话问候 + 必要时就医
    2. 连续2条打卡「不舒服」 → 建议关注或约号
    3. 连续3条心情「低落」   → 多聊聊
    4. 当天/昨天「没吃」饭   → 打电话问问
    5. 当天/昨天「漏了一顿/没吃」药 → 问漏了哪种、还要不要补（★ 2026-09-19 老人半合并：用药并进打卡）
    6. 最近打卡不适主诉关键词出现≥2次（头晕/胸闷/疼/麻/喘）→ 别拖，去查查
    7. 距上次电话/视频/探望 超7天 → 该联系了
    """
    out = []
    today = datetime.now().date()

    def _parse_date(v):
        try:
            return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return None

    c = _conn()
    try:
        # —— 每日打卡哨兵 ——
        cols = [r[1] for r in c.execute('PRAGMA table_info(panel_elder_checkin_1)')]
        if all(col in cols for col in ('日期', '状态', '吃饭')):
            rows = c.execute('SELECT * FROM panel_elder_checkin_1 ORDER BY id DESC LIMIT 10').fetchall()
            recs = [{k: r[k] for k in r.keys()} for r in rows]
            if recs:
                latest = recs[0]
                who = str(latest.get('老人姓名') or '').strip() or '老人'
                link = {'type': 'elder', 'module': 'elder_checkin_1', 'link': '/panel/elder_checkin_1'}
                # 1) 血压
                try:
                    hi = float(latest.get('血压高压') or 0)
                    lo = float(latest.get('血压低压') or 0)
                except (TypeError, ValueError):
                    hi = lo = 0.0
                if hi >= 150 or lo >= 95:
                    out.append(dict(link, id='elder-bp',
                                    title=f'🚨 {who} 最近一次血压 {int(hi) if hi == int(hi) else hi}/{int(lo) if lo == int(lo) else lo}，偏高了——打个电话问问，持续偏高建议就医'))
                # 2) 连续不舒服
                if len(recs) >= 2 and all(str(r.get('状态') or '') == '不舒服' for r in recs[:2]):
                    out.append(dict(link, id='elder-unwell',
                                    title=f'🚨 {who} 连续 2 天打卡「不舒服」，别拖，建议问清楚症状或约个号'))
                # 3) 连续低落
                if all(col in cols for col in ('心情',)) and len(recs) >= 3 \
                        and all(str(r.get('心情') or '') == '低落' for r in recs[:3]):
                    out.append(dict(link, id='elder-mood',
                                    title=f'💙 {who} 连续 3 天心情低落，这周多打几个电话、多聊聊'))
                # 4) 没吃饭
                d0 = _parse_date(latest.get('日期'))
                if str(latest.get('吃饭') or '') == '没吃' and d0 and (today - d0).days <= 1:
                    out.append(dict(link, id='elder-meal',
                                    title=f'🍚 {who} 记了「没吃」饭，打电话问问是不是不舒服'))
                # 4.5) ★ 2026-09-19 老人半合并：漏服提醒（列是新增的，老库没这列时静默跳过）
                if '服药情况' in cols and d0 and (today - d0).days <= 1:
                    med = str(latest.get('服药情况') or '')
                    if med in ('漏了一顿', '没吃'):
                        missed = str(latest.get('漏了哪种') or '').strip()
                        tail = f'（漏了：{missed}）' if missed else ''
                        out.append(dict(link, id='elder-med',
                                        title=f'💊 {who} 今天记了「{med}」药{tail}，问问漏了哪种、要不要补'))
                # 5) 不适主诉关键词
                if all(col in cols for col in ('不适主诉',)):
                    kw = ('头晕', '胸闷', '疼', '麻', '喘')
                    hits = sum(1 for r in recs if any(k in str(r.get('不适主诉') or '') for k in kw))
                    if hits >= 2:
                        out.append(dict(link, id='elder-symptom',
                                        title=f'🩺 {who} 最近 {len(recs)} 次打卡里有 {hits} 次提到头晕/疼/胸闷这类不舒服，建议带去查查'))
    except Exception as e:
        logger.debug(f'[elder] 打卡哨兵跳过: {e}')
    finally:
        c.close()

    # —— 联系频率哨兵 ——
    try:
        c = _conn()
        cols2 = [r[1] for r in c.execute('PRAGMA table_info(panel_elder_contact_1)')]
        if all(col in cols2 for col in ('日期', '方式')):
            r = c.execute("SELECT * FROM panel_elder_contact_1 "
                          "WHERE 方式 IN ('电话','视频','探望') ORDER BY id DESC LIMIT 1").fetchone()
            if r:
                d = {k: r[k] for k in r.keys()}
                last = _parse_date(d.get('日期'))
                if last:
                    days = (today - last).days
                    if days > 7:
                        out.append({'type': 'elder', 'id': 'elder-contact',
                                    'title': f'📞 已经 {days} 天没给爸妈打电话了（上次联系 {last.strftime("%m-%d")}），今晚打个？',
                                    'module': 'elder_contact_1', 'link': '/panel/elder_contact_1'})
    except Exception as e:
        logger.debug(f'[elder] 联系哨兵跳过: {e}')
    finally:
        c.close()

    return out[:8]


def init_db():
    with _lock:
        c = _conn()
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS assist_settings (
                        key TEXT PRIMARY KEY, value TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS assist_pending (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        panel_id TEXT NOT NULL,
                        domain TEXT DEFAULT '',
                        child_id INTEGER,
                        records_json TEXT NOT NULL,
                        source_text TEXT DEFAULT '',
                        created_at TEXT DEFAULT (datetime('now','localtime')))""")
            c.execute("""CREATE TABLE IF NOT EXISTS assist_lexicon (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        pattern TEXT NOT NULL UNIQUE,
                        panel_id TEXT NOT NULL DEFAULT '',
                        domain TEXT DEFAULT '',
                        hits INTEGER DEFAULT 0,
                        source TEXT DEFAULT 'confirm',
                        created_at TEXT DEFAULT (datetime('now','localtime')))""")
            # ★ #1379（2026-11-02 定版拍板）误触发词级黑名单：用户明说「以后别识别 X」才进，
            #   「别记/这是对话」只作废当次绝不动关键词（主动权在用户手里）
            c.execute("""CREATE TABLE IF NOT EXISTS receipt_blocklist (
                        word TEXT PRIMARY KEY,
                        created_at TEXT DEFAULT (datetime('now','localtime')))""")
            # ★ 2026-09-19 #768 面板 AI 分析历史（前端回看上次分析）
            c.execute("""CREATE TABLE IF NOT EXISTS panel_ai_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        panel_id TEXT NOT NULL,
                        question TEXT DEFAULT '',
                        text TEXT NOT NULL,
                        model TEXT DEFAULT '',
                        created_at TEXT DEFAULT (datetime('now','localtime')))""")
            c.commit()
        finally:
            c.close()


def get_assist_settings():
    s = dict(_ASSIST_DEFAULTS)
    c = _conn()
    try:
        for r in c.execute("SELECT key, value FROM assist_settings").fetchall():
            s[r['key']] = r['value']
        # ★ D1 存量迁移（2026-10-11）：answer_allow_cloud 旧默认 false 会拦 5 类功能，
        #   升级新版后一次性改成 true；写标记键保证只跑一次——之后用户主动关就尊重用户选择
        try:
            mig = c.execute("SELECT value FROM assist_settings WHERE key='answer_gate_migrated_v1'").fetchone()
            if not mig:
                c.execute("INSERT OR REPLACE INTO assist_settings(key, value) VALUES('answer_allow_cloud','true')")
                c.execute("INSERT OR REPLACE INTO assist_settings(key, value) VALUES('answer_gate_migrated_v1','true')")
                c.commit()
                s['answer_allow_cloud'] = 'true'
        except Exception:
            pass  # 迁移失败不阻断读取（下次调用再试）
    finally:
        c.close()
    return s


def save_assist_settings(d):
    with _lock:
        c = _conn()
        try:
            for k, v in (d or {}).items():
                if k in _ASSIST_DEFAULTS:
                    c.execute("INSERT OR REPLACE INTO assist_settings(key, value) VALUES(?,?)", (k, str(v).lower()))
            c.commit()
        finally:
            c.close()
    return True

# ══════════════════════════ #1379 误触发豁免（2026-11-02 定版拍板） ══════════════════════════


def looks_like_question(t):
    """#1379-A 提问豁免判定（与 classify_home 同口径）：句首疑问词 / 全句以？结尾 /
    句首「能不能、可不可以」类。命中 = 用户在提问不是要录入，凭证识别/默录都该让路。
    注意问号在句尾才算——「吃了 30 元？」这种带金额的存疑句不该一刀切放走，
    所以这里只看句首 12 字与句尾问号，与 classify_home 完全一致。"""
    t = (t or '').strip()
    if not t:
        return False
    return any(h in t[:12] for h in QUESTION_HINTS) or t.rstrip().endswith(('？', '?'))


def get_receipt_blocklist():
    """#1379-B2 词级黑名单（凭证识别触发前检查；词是用户明说「以后别识别 X」才进来的）"""
    try:
        c = _conn()
        try:
            return [r['word'] for r in c.execute("SELECT word FROM receipt_blocklist ORDER BY created_at").fetchall()]
        finally:
            c.close()
    except Exception:
        return []


def add_receipt_blocklist(words):
    """词进黑名单（幂等）。words: str 或 list。返回本次新增的词列表"""
    if isinstance(words, str):
        words = [words]
    added = []
    with _lock:
        c = _conn()
        try:
            for w in words:
                w = (w or '').strip()
                # 单字不收（拉黑「账」这种字误杀面太大）、超 20 字不是词
                if not w or len(w) < 2 or len(w) > 20:
                    continue
                cur = c.execute("INSERT OR IGNORE INTO receipt_blocklist(word) VALUES(?)", (w,))
                if cur.rowcount:
                    added.append(w)
            c.commit()
        finally:
            c.close()
    if added:
        logger.info(f"[PanelAssist] #1379 黑名单新增: {added}")
    return added


def remove_receipt_blocklist(word):
    with _lock:
        c = _conn()
        try:
            n = c.execute("DELETE FROM receipt_blocklist WHERE word=?", ((word or '').strip(),)).rowcount
            c.commit()
        finally:
            c.close()
    return n > 0


# 撤销最近一次确认入账（#1379-D）：进程内记账本，重启丢失即不可撤（如实回执）
_last_posted = {'panel_id': '', 'domain': '', 'ids': [], 'summary': '', 'ts': 0.0}


def undo_last_posted(max_age=600):
    """撤销最近一次「确认入账」落库的台账行。max_age 秒内才可撤（默认 10 分钟，
    防翻旧账误删）。返回 (ok, 消息)"""
    import time as _t
    from src.core.panel_manager import get_panel_manager
    if not _last_posted['ids']:
        return False, '没有可撤销的入账（重启后之前的也撤不了）'
    if _t.time() - _last_posted['ts'] > max_age:
        return False, f'最近一次入账已超过 {max_age // 60} 分钟，为防误删旧账不支持撤销，可去面板手动删'
    pm = get_panel_manager()
    table = f"panel_{_last_posted['panel_id']}"
    c = pm._conn() if hasattr(pm, '_conn') else None
    if c is None:
        from src.models.database import get_connection
        from src.core.panel_manager import DB_PATH
        c = get_connection(str(DB_PATH))
    try:
        marks = ','.join('?' * len(_last_posted['ids']))
        c.execute(f'DELETE FROM "{table}" WHERE id IN ({marks})', _last_posted['ids'])
        c.commit()
    finally:
        c.close()
    out = _last_posted['summary'] or f"{len(_last_posted['ids'])} 条"
    _last_posted.update({'ids': [], 'summary': '', 'ts': 0.0})
    try:
        from src.core.sync_bus import bump
        bump(f"panel:{_last_posted['panel_id']}")
    except Exception:
        pass
    logger.info(f"[PanelAssist] #1379 入账已撤销: {table} {out}")
    return True, f'↩️ 已撤销刚才的入账：{out}（台账恢复原样）'

# ══════════════════════════ 域注册表 ══════════════════════════


def _panel_json(panel_id):
    p = PANELS_DIR / panel_id / 'panel.json'
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _kid_domain_fields(domain):
    """kid DOMAINS: key -> (table, category, label, [field keys])，返回 fields [{key,label,type}]"""
    from src.core.kid_archive import DOMAINS
    if domain not in DOMAINS:
        return None
    _table, _cat, label, keys = DOMAINS[domain]
    # 键名→中文标签映射（仅提示用，抽取输出仍用键名）
    zh = {
        'date': '日期', 'subject': '科目', 'topic': '知识点', 'score': '分数', 'weakness': '薄弱点',
        'action': '措施', 'status': '状态', 'photo': '照片', 'notes': '备注', 'question': '题目',
        'wrong_reason': '错因', 'correct': '正确解法', 'mastered': '已掌握', 'htype': '类型',
        'item': '项目', 'value': '结果', 'place': '地点', 'height': '身高cm', 'weight': '体重kg',
        'tcategory': '类别', 'content': '内容', 'note': '备注', 'rtype': '关系类型', 'name': '名字',
        'relation_desc': '情况描述', 'ctype': '类型', 'size': '尺码', 'brand': '品牌',
        'category': '类别', 'location': '摆放位置', 'platform': '平台', 'age_rating': '适龄',
        'daily_limit': '每日限玩分钟', 'time_window': '时段', 'game_id': '游戏', 'minutes': '分钟数',
        'over_limit': '超时', 'icategory': '类别', 'teacher': '老师', 'schedule': '上课时间',
        'start_date': '开始日期', 'cost': '费用', 'interest_id': '课程', 'mood': '态度',
        'progress': '进展', 'dcategory': '饮食类别', 'food': '食物', 'reason': '原因/身体情况',
        # ★ 2026-09-18 学习域扩容 + 视力
        'content': '作业内容', 'duration': '用时(分钟)', 'quality': '完成质量', 'exam_name': '考试名称',
        'full_score': '满分', 'rank': '名次', 'grade_level': '年级/教材', 'chapter': '章节/单元',
        'progress_pct': '进度(%)', 'mastery': '掌握程度', 'vtype': '类型', 'left_eye': '左眼',
        'right_eye': '右眼', 'need_glasses': '是否需要配镜',
        # ★ 2026-09-18 成长档案加强（选填字段）
        'hw_type': '作业类型', 'independent': '独立完成', 'wrong_count': '错题数',
        'class_avg': '班级均分', 'vs_last': '对比上次', 'parent_note': '家长沟通',
        'expect_date': '预计完成', 'teacher_progress': '老师教到哪了',
        'degree': '度数', 'lens_type': '镜片类型', 'outdoor_min': '户外时长(分钟)',
        'screen_min': '屏幕时间(分钟)', 'recheck_date': '复查日期', 'head_circ': '头围cm',
        'temp': '体温', 'medicine': '用药', 'next_visit': '复诊日期',
        'reaction': '过敏反应', 'substitute': '替代食物',
        'scene': '发生场景', 'response': '家长应对', 'followup': '后续变化',
        'closeness': '亲密程度', 'attention': '需要关注',
        'season': '适用季节', 'buy_date': '购买日期', 'price': '价格', 'next_size': '预计换码',
        'dispose': '淘汰去向',
        'total_lessons': '总课时', 'used_lessons': '已用课时', 'expire_date': '课包有效期',
        'absence': '请假次数',
        'mtype': '类型', 'amount': '金额(元)', 'source': '给钱人/收款方', 'balance': '结余(元)',
        # ★ 2026-09-19 #792 习惯打卡
        'habit': '习惯', 'done': '完成情况', 'streak': '连续天数',
    }
    # 同名键在不同域含义不同 → 域内覆盖（如 mood：作业=情绪，兴趣反馈=态度；item：健康=项目，零花钱=用途）
    _override = {
        'homework': {'mood': '情绪状态'},
        'money': {'item': '用途/来源', 'mtype': '收支类型'},
    }.get(domain, {})
    return {'key': domain, 'label': label,
            'fields': [{'key': k, 'label': _override.get(k) or zh.get(k, k), 'type': 'text'} for k in keys
                       if k not in ('photo', 'game_id', 'interest_id')]}


def get_domain_schema(panel_id, domain=''):
    """返回抽取用 schema。表面板 fields=[{key,label,type,options}]；kid 域 fields 同构。"""
    if panel_id == 'kid_archive':
        from src.core.kid_archive import DOMAINS
        if domain:
            f = _kid_domain_fields(domain)
            return {'panel_id': panel_id, 'domain': domain, 'label': f['label'] if f else domain,
                    'fields': f['fields'] if f else [], 'kind': 'kid'} if f else None
        return {'panel_id': panel_id, 'domain': '', 'label': '孩子成长档案',
                'domains': [{'key': k, 'label': v[2]} for k, v in DOMAINS.items()
                            if k not in ('game_logs', 'interest_logs')],
                'kind': 'kid'}
    pj = _panel_json(panel_id)
    if not pj:
        return None
    fields = []
    for f in (pj.get('fields') or []):
        fields.append({'key': f.get('name', ''), 'label': f.get('label', f.get('name', '')),
                       'type': f.get('type', 'text'), 'options': f.get('options', [])})
    return {'panel_id': panel_id, 'domain': '', 'label': pj.get('name', panel_id),
            'fields': fields, 'kind': 'table'}


def list_domains():
    """全部可录入域（前端下拉/提示用）"""
    out = []
    if PANELS_DIR.is_dir():
        for d in sorted(PANELS_DIR.iterdir()):
            pj = _panel_json(d.name)
            if not pj:
                continue
            if d.name == 'kid_archive':
                out.append({'panel_id': d.name, 'name': pj.get('name'), 'icon': pj.get('icon', ''),
                            'domains': [{'key': k, 'label': v[2]} for k, v in
                                        __import__('src.core.kid_archive', fromlist=['DOMAINS']).DOMAINS.items()
                                        if k not in ('game_logs', 'interest_logs')]})
            elif pj.get('fields'):
                out.append({'panel_id': d.name, 'name': pj.get('name'), 'icon': pj.get('icon', ''),
                            'domains': [{'key': '', 'label': pj.get('name')}]})
    return out

# ══════════════════════════ 本地模型调用 ══════════════════════════


def _resolve_vision_model():
    """★ #1204（2026-10-25 开发者死命令）：本地看图 = 用户选的本地主模型，严禁扫描代选。
    返回当前主模型名（config.yaml current_model 剥 ollama: 前缀）；没选/是外脑 → ''。
    能不能看图由调用方校验（model_supports_vision），不能看图就如实报错提醒用户自己换。"""
    try:
        import yaml as _yaml
        cfgp = Path(__file__).resolve().parent.parent.parent / 'config.yaml'
        _cm = str(((_yaml.safe_load(cfgp.read_text(encoding='utf-8')) or {}).get('current_model')) or '').strip()
        if _cm.startswith('ollama:'):
            return _cm[len('ollama:'):]
        return ''
    except Exception:
        return ''


def _normalize_b64_for_vision(image_b64, max_side=1280, max_bytes=1024 * 1024):
    """★ 2026-09-23 实测实锤：超大原图（约4MB+）Ollama 静默丢图，minicpm-v 答「无法提供」。
    看图前归一化：超 max_bytes 或最长边超 max_side → 缩边 + JPEG 重编码。
    输入/输出均为纯 base64（不带 data: 前缀）；失败原样返回。"""
    try:
        import base64 as _b64
        import io as _io
        if not image_b64 or len(image_b64) <= max_bytes * 4 // 3:
            return image_b64
        from PIL import Image
        img = Image.open(_io.BytesIO(_b64.b64decode(image_b64)))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        w, h = img.size
        scale = max(w, h) / float(max_side)
        if scale > 1:
            img = img.resize((max(1, int(w / scale)), max(1, int(h / scale))), Image.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, 'JPEG', quality=85)
        logger.info(f"[PanelAssist] 大图归一化 {len(image_b64)//1024}KB -> {len(buf.getvalue())//1024}KB")
        return _b64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"[PanelAssist] 图片归一化失败，用原图: {e}")
        return image_b64


def call_local_vision(prompt, image_b64, timeout=300):
    """本地看图（★ #1204 开发者死命令：只用用户选的本地主模型，不能看图如实报错提醒换模型，绝不代选）。
    image_b64 为纯 base64（不带 data: 前缀）"""
    # ★ #1267（2026-10-29 实测实锤）：主模型=HarvenEye → 走 Eye 视觉通道（底座 qwen3-vl
    #   就是通用视觉模型，选谁给谁）。必须放在 _resolve_vision_model 之前——
    #   resolve 只认 ollama: 前缀，harveneye 恒返空 → 旧版提前报「没有选定模型」
    from src.core import eye_client as _eyecli
    if _eyecli.is_selected():
        image_b64 = _normalize_b64_for_vision(image_b64)
        ok, ans = _eyecli.vision_chat(prompt, [image_b64], timeout=timeout)
        return (ans.strip(), 'harveneye', '') if ok else (None, 'harveneye', f'HarvenEye 看图失败: {ans}')
    model = _resolve_vision_model()
    if not model:
        return None, '', '本地模式没有选定模型。到「设置 → 模型中心」自己选一个能看图的本地模型，或切到外脑模式'
    try:
        from src.core.image_processor import model_supports_vision, installed_vision_models
        if not model_supports_vision(model):
            hint = ' / '.join(installed_vision_models()[:4]) or '还没装能看图的模型'
            return None, model, (f'当前本地模型「{model}」不支持看图。'
                                 f'请到「设置 → 模型中心」自己换一个能看图的本地模型（已装可选：{hint}），或切到外脑模式')
    except Exception:
        pass
    try:
        import requests
        # ★ 2026-09-23 大图归一化：超大原图 Ollama 静默丢图
        image_b64 = _normalize_b64_for_vision(image_b64)
        payload = {'model': model, 'stream': False,
                   'messages': [{'role': 'user', 'content': prompt, 'images': [image_b64]}],
                   'options': {'temperature': 0.3}}
        r = requests.post('http://127.0.0.1:11434/api/chat', json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get('message', {}).get('content', '').strip(), model, ''
    except Exception as e:
        return None, '', f'视觉模型调用失败: {e}'


def call_cloud_vision(prompt, image_b64, timeout=60, model_id=None):
    """★ #1155（2026-10-16 开发者两轮修正定案）外脑看图：用用户当前外脑——选谁给谁。
    不写死任何视觉模型（旧版焊死 glm-4v-flash 已废）；当前外脑看不了/没配 → 如实报错，
    不兜底不代选，由用户自己决定切谁。image_b64 为纯 base64。
    ★ A2+B（2026-11-12）：新增 model_id 参数——仅 _camp_vision_bg 退避补漏用，
    指定已配置的外脑代跑一次（后台数据提取链专用）；不传保持原铁律行为。"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager, PROVIDERS
        import requests
        mm = get_multi_model_manager()
        if model_id:
            cur = str(model_id).strip()   # 退避补漏：指定通道，跳过当前外脑检查
        else:
            cur = str(getattr(mm, 'current_model', '') or (mm.config.get('current_model') or '') or '').strip()
            if not cur or cur == 'ollama':
                return None, '', '当前是本地模式，这条通道只给外脑用（请走本地看图或切到外脑模式）'
            # ★ #1241（2026-10-27）：HarvenEye 是本地混合模型引擎不是外脑——如实跳过不起名
            #   （不兜底不代选；调用方 PhotoNamer 本就「起名没成（如实不兜底）」）
            if cur == 'harveneye':
                return None, '', 'HarvenEye 是本地混合模型引擎，不是外脑，这条起名通道用不了（不影响看图，照片功能照常）'
        entry = (mm.config.get('models', {}) or {}).get(cur) or {}
        prov = (mm._get_all_providers().get(cur)) or PROVIDERS.get(cur) or {}
        key = entry.get('api_key') or prov.get('api_key') or ''
        base = str(entry.get('base_url') or prov.get('base_url') or '').rstrip('/')
        mname = entry.get('model_name') or prov.get('model_name') or cur
        if not (key and base):
            return None, '', f'外脑「{mname}」没配好（缺 Key 或地址），到「设置 → 模型」补上，或换个外脑/模式再试'
        # 大图归一化（与本地看图同一套，防超大原图识别失败）
        image_b64 = _normalize_b64_for_vision(image_b64)
        _mime = 'image/png' if str(image_b64).startswith('iVBOR') else 'image/jpeg'
        payload = {'model': mname, 'stream': False, 'temperature': 0.3,
                   'messages': [{'role': 'user', 'content': [
                       {'type': 'image_url', 'image_url': {'url': f'data:{_mime};base64,{image_b64}'}},
                       {'type': 'text', 'text': prompt}]}]}
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        r = requests.post(f'{base}/chat/completions', headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            txt = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '').strip()
            if txt:
                return txt, f'{cur}:{mname}', ''
            return None, '', f'外脑「{mname}」返回空回复'
        # ★ 如实报：可能不支持看图，绝不偷偷换 glm-4v-flash 兕底
        return None, '', (f'外脑「{mname}」看图没成功（HTTP {r.status_code}）。'
                          f'它可能不支持图片；你可以换个支持看图的外脑，或切到本地模式用本地模型看。')
    except Exception as e:
        return None, '', f'外脑看图失败: {e}'


def _vision_routing_pref():
    """★ #1155 看图路由偏好：auto=跟随当前模式（默认）/ local=固定本地 / cloud=固定外脑。"""
    try:
        v = str(get_assist_settings().get('vision_routing', 'auto') or 'auto').strip().lower()
        return v if v in ('auto', 'local', 'cloud') else 'auto'
    except Exception:
        return 'auto'


def _try_harveneye(prompt, image_b64, timeout=120):
    """★ 2026-10-20 第四批4.2: HarvenEye 可选视觉增强包挂点。
    探测 data/harveneye/installed.json（由 HarvenEye 包安装器写入），
    存在且 vision_url 可用 → 走本地三件套（YOLO+CLIP+Qwen3-VL）HTTP 服务；
    没装/服务不通 → 返回 None（调用方落到下一层），**缺哪层跳哪层，哈文主体零依赖**。"""
    try:
        import json as _json, urllib.request as _ur
        cfg_p = Path(__file__).parent.parent.parent / 'data' / 'harveneye' / 'installed.json'
        if not cfg_p.exists():
            return None
        cfg = _json.loads(cfg_p.read_text(encoding='utf-8'))
        url = cfg.get('vision_url', '')
        if not url:
            return None
        req = _ur.Request(url, data=_json.dumps({
            'prompt': str(prompt)[:800], 'image': image_b64[:200000] if image_b64 else '',
        }).encode('utf-8'), headers={'Content-Type': 'application/json'})
        with _ur.urlopen(req, timeout=timeout) as resp:
            out = _json.loads(resp.read().decode('utf-8'))
        if out.get('ok'):
            return out.get('text', ''), out.get('model', 'harveneye'), ''
        return None
    except Exception:
        return None


def _eye_vision(prompt, image_b64, timeout=120):
    """★ #1243（2026-10-27）：HarvenEye 看图通道——b64 落临时文件 → eye_client /chat 带图
    （Eye 同机读图零传输）。返回 (text, model, err) 三元组，与 _camp_vision 各分支同构。"""
    import base64 as _b64
    import os as _os
    import tempfile as _tmp
    from src.core import eye_client
    path = ''
    try:
        raw = _b64.b64decode(_strip_b64_local(image_b64) or '')
        fd, path = _tmp.mkstemp(suffix='.jpg', prefix='hv_eye_vision_')
        with _os.fdopen(fd, 'wb') as f:
            f.write(raw)
        ok, ans = eye_client.chat(
            [{'role': 'user', 'content': prompt}], timeout=timeout, image_path=path)
        if not ok:
            return '', 'HarvenEye', ans
        return ans, 'HarvenEye（qwen3-vl）', ''
    except Exception as e:
        return '', 'HarvenEye', f'HarvenEye 看图失败: {e}'
    finally:
        if path:
            try:
                _os.remove(path)
            except OSError:
                pass


def _strip_b64_local(s):
    """剥 dataURL 前缀（_eye_vision 内部用）。"""
    if isinstance(s, str) and s.startswith('data:'):
        return s.split(',', 1)[-1]
    return s


def _current_model_is_eye():
    """★ #1243：用户当前选的主模型是否 HarvenEye（读 config current_model）。"""
    try:
        from src.utils.config import get_config
        return ((get_config() or {}).get('current_model', '') or '').strip() == 'harveneye'
    except Exception:
        return False


def _camp_vision(prompt, image_b64, timeout=120):
    """★ #1155 看图路由统一入口（开发者两轮修正定案）：选谁给谁、不行如实报、不兜底不代选。
    vision_routing 偏好：auto → 外脑阵营=当前外脑 / 本地阵营=自动探测的本地视觉模型；
    local/cloud → 用户固定的选择。失败如实报错，绝不静默换模型。
    ★ 2026-10-18 修：补 timeout 参数——img_store.retag/describe、photo_center 三个调用点
    都在传 timeout=120/180，旧签名没这个参数 → 全部 instant 报错（后台补标空转一整夜实锤）
    ★ 2026-10-20 第四批4.2: auto 模式优先探测 HarvenEye 包（缺哪层跳哪层；
    降级链 HarvenEye→本地minicpm-v→外脑glm-4v→纯文字，及格线=纯外脑+纯文字）
    ★ #1243（2026-10-27）：用户当前主模型=HarvenEye → 看图一律走 Eye（选谁用谁铁律）——
    起名/补标/描述/手机看图等所有走本入口的链路统一生效，不再撞「外脑没配好」"""
    # ★ #1243：主模型=HarvenEye → Eye 看图通道（放在偏好判定前：选谁给谁）
    if _current_model_is_eye():
        return _eye_vision(prompt, image_b64, timeout=timeout)
    pref = _vision_routing_pref()
    if pref == 'auto':
        _he = _try_harveneye(prompt, image_b64, timeout=timeout)
        if _he is not None:
            return _he
    if pref == 'local':
        return call_local_vision(prompt, image_b64, timeout=timeout)
    if pref == 'cloud':
        return call_cloud_vision(prompt, image_b64, timeout=timeout)
    camp, _cur = _active_camp()
    if camp == 'cloud':
        return call_cloud_vision(prompt, image_b64, timeout=timeout)
    return call_local_vision(prompt, image_b64, timeout=timeout)


def _camp_vision_bg(prompt, image_b64, timeout=120):
    """★ A2+B（2026-11-12）后台视觉链统一入口——数据提取任务专用（打标/描述/图片转述/
    账单扫描/识菜入库/深识/看图填表），聊天等交互直答仍走 _camp_vision 铁律不退避。
    实锤根因：_camp_vision 铁律「选谁给谁、不行如实报」对交互链是对的，但后台打标
    跟着陪葬——昨晚本地模式没选模型，33 张照片批量「标签失败」一整夜。
    退避顺序（每步如实标注·退避，绝不冒充原模型）：
      ① _camp_vision 原路（尊重用户偏好/选谁给谁）
      ② Eye 通道（本地毫秒级免费；原路没走过 Eye 时）
      ③ 另一阵营（本地↔外脑）；本地模式没选模型时，从已配 Key 的外脑里
        找第一个支持 vision 的代跑一次（用户自己的 Key，只为把标签打出来）
    返回 (text, model, err)，与 _camp_vision 同构。"""
    text, model, err = _camp_vision(prompt, image_b64, timeout=timeout)
    if text:
        return text, model, err
    # 退避②：Eye 通道
    try:
        from src.core import eye_client as _ec
        if not _current_model_is_eye() and getattr(_ec, 'models_ready', lambda: False)():
            t2, m2, _e2 = _eye_vision(prompt, image_b64, timeout=timeout)
            if t2:
                return t2, f'{m2}·退避', ''
    except Exception:
        pass
    # 退避③：另一阵营
    camp, _cur = _active_camp()
    if camp == 'cloud':
        t3, m3, e3 = call_local_vision(prompt, image_b64, timeout=timeout)
    else:
        t3, m3, e3 = call_cloud_vision(prompt, image_b64, timeout=timeout)
        if not t3:
            # 本地模式没选模型/模型不能看图 → 已配 Key 的视觉外脑补漏
            try:
                from src.core.multi_model_manager import get_multi_model_manager, PROVIDERS
                from src.core.vision_link import _provider_has_vision
                mm = get_multi_model_manager()
                for cid, entry in (mm.config.get('models', {}) or {}).items():
                    if str(cid) in ('ollama', 'harveneye'):
                        continue
                    if not (entry.get('api_key') or (PROVIDERS.get(cid) or {}).get('api_key') or ''):
                        continue
                    if not _provider_has_vision(cid):
                        continue
                    t3, m3, e3 = call_cloud_vision(prompt, image_b64,
                                                   timeout=timeout, model_id=cid)
                    if t3:
                        break
            except Exception as _ce:
                e3 = f'{e3 or ""}; 外脑补漏失败: {_ce}'
    if t3:
        return t3, f'{m3}·退避', ''
    return None, model, f'{err}；退避也失败: {e3 or "无可用通道"}'


def _norm_images(image_b64):
    """★ A1 照片链多图化（2026-10-11）：image_b64 兼容单图 str / 多图 list。
    返回有效图列表（纯 base64 或 dataURL，长度过滤垃圾值）。
    ★ #1407-C（2026-11-03）：上限 4 → 9——实测实锤场景：重要物品一物多照
    （柜子 1 张 + 抽屉 1 张 + 物品多视角），4 张不够用。单图行为完全向后兼容。"""
    if not image_b64:
        return []
    if isinstance(image_b64, str):
        return [image_b64] if len(image_b64) > 100 else []
    try:
        return [i for i in (image_b64 or []) if isinstance(i, str) and len(i) > 100][:9]
    except Exception:
        return []

# ══════════════════════════ 结构化抽取 ══════════════════════════


def _extract_system(schema, today):
    fields_desc = '\n'.join(
        f'- {f["label"]}（键名 {f["key"]}，类型 {f.get("type", "text")}'
        + (f'，可选值: {"/".join(f.get("options", []))}' if f.get('options') else '') + '）'
        for f in schema['fields'])
    return (
        "你是家庭档案录入助手，把用户的一句话拆解成结构化记录。\n"
        f"【目标档案】{schema['label']}\n【字段】\n{fields_desc}\n"
        "【规则】\n"
        "1. 只输出 JSON 数组，每条记录一个对象，键必须用括号里的键名，缺失的字段填 \"\"（绝对不要编造）。"
        "★绝不允许出现【字段】清单以外的任何键（用户提到但清单里没有的信息，浓缩到语义最贴切的现有字段或备注，绝不自创字段）；回复中也不要建议用户去用手动表单/手动录入（表单入口由面板页自身提供）。\n"
        "2. 一句话包含多件事时拆成多条记录。\n"
        "3. 今天是 " + today + "；'今天/昨天/上周'等换算成 YYYY-MM-DD。\n"
        "4. 数字一律用阿拉伯数字（八十五→85，金额不带单位）。\n"
        "5. 选择类字段从可选值里选最贴近的，实在没有就填最简短的归纳词。\n"
        "6. 用户消息可能包含对之前记录的更正（如'改成86'），按最新意思输出。\n"
        "7. 如果内容与该档案完全无关，输出 []。\n"
        "8. 每条记录必须包含【字段】里列出的全部键名（没提到的填 \"\"）；用户明确说到的每个信息点都必须落到语义最贴切的字段，不要丢弃。"
        "例：'应用题审题又马虎了，每天练两道应用题' → 薄弱点=应用题审题马虎，解决办法=每天练两道应用题；'类似薄弱点/问题/原因'类信息绝不能省略。\n"
        "9. ★方向判定（记账）：收到钱/进钱=收入（工资、红包、退款、入账、进账、到账、回款、卖货收款、奖金、中奖等），花钱/付钱=支出。"
        "'入账10万'是收入不是支出；'发红包'是支出，'收红包'是收入。\n"
        "只输出 JSON 数组，不要解释。")


def _extract_system_pick_domain(today):
    from src.core.kid_archive import DOMAINS
    menu = '\n'.join(
        f'- {k}（{v[2]}）字段: {", ".join(v[3])}'
        for k, v in DOMAINS.items() if k not in ('game_logs', 'interest_logs'))
    return (
        "你是孩子成长档案录入助手。先判断这句话属于哪个档案域，再拆解成记录。\n"
        f"【可选域及字段】\n{menu}\n"
        "【规则】只输出一个 JSON 对象：{\"domain\": \"域键名\", \"records\": [记录数组]}；"
        f"每条记录必须包含该域列出的全部字段键（没提到的填 \"\"），用户说到的每个信息点（如薄弱点、办法、备注）都要落到语义最贴切的字段，不要丢弃；"
        f"多件事拆多条；今天是 {today}；数字用阿拉伯数字。只输出 JSON。")


def extract_records(text, panel_id, domain='', history=None, image_b64='', child_id=None):
    """结构化抽取。返回 (records:list[dict], domain:str, model:str, err:str)
    - 表面板: records 键=字段中文名
    - kid: domain 为空时由模型选域；records 键=字段键名
    - panel_id 为空: 家庭空间跨面板模式，返回 groups=[{panel_id,domain,label,icon,fields,records}]，domain='home'
    """
    today = datetime.now().strftime('%Y-%m-%d')

    if not panel_id:
        return _extract_home(text, history, image_b64, today)

    schema = get_domain_schema(panel_id, domain)
    if not schema:
        return [], '', '', f'未知档案: {panel_id}/{domain}'

    # 图片：先走 #1155 看图路由转述（跟随用户当前外脑/本地偏好），再并入文本抽取
    # ★ A1 照片链（2026-10-11）：多图逐张转述（上限4），单图行为与旧版完全一致
    imgs = _norm_images(image_b64)
    img_desc = ''
    if imgs:
        descs = []
        for _i, _im in enumerate(imgs, 1):
            desc, vmodel, err = _camp_vision_bg(
                '请详细描述这张图片的内容。如果是作业/试卷，抄出题目和答案；如果是单据，抄出项目与金额；'
                '如果是体检/疫苗本，抄出项目与数值。只描述事实。', _im)
            if err:
                return [], domain, '', err
            descs.append(f"【图片{_i}】{desc[:800]}" if len(imgs) > 1 else desc[:800])
        img_desc = "\n【图片内容】" + '\n'.join(descs)

    hist_txt = ''
    for h in (history or [])[-6:]:
        role = '用户' if h.get('role') == 'user' else '你'
        hist_txt += f"{role}: {str(h.get('text', ''))[:200]}\n"

    if schema['kind'] == 'kid' and not domain:
        # ★ 2026-09-18 三级引擎改造：
        # ① 快车道（词典→域关键词，0 token）→ ② 两段式（先选域后抽取，省 token）→ ③ 本地兜底
        # ★ 空/无效域绝不再带着 records 返回（根治"确认入库 404 无法入库"）
        lex = _lexicon_pick(text, 'kid_archive')
        if lex and lex[1]:
            recs, model, err = _extract_with_fields(text, lex[1], _kid_domain_fields(lex[1])['fields'],
                                                    history, img_desc, today)
            return recs, lex[1], model, err
        hit, _src = _kid_fastpick(text)
        if hit:
            logger.info(f"[PanelAssist] 快车道选域 {hit}（关键词，0 token）")
            recs, model, err = _extract_with_fields(text, hit, _kid_domain_fields(hit)['fields'],
                                                    history, img_desc, today)
            return recs, hit, model, err

        # ② 第一段：小 prompt 选域（只发域名菜单，max_tokens=40）
        system = _pick_system_lean(today)
        prompt = (f"历史对话：\n{hist_txt}\n" if hist_txt else '') + f"用户消息：{text}{img_desc}"
        raw, model, err = _llm_call(system, prompt, max_tokens=40, temperature=0.1)
        if err:
            return [], '', '', err
        dom = ''
        try:
            obj = json.loads(raw[raw.find('{'):raw.rfind('}') + 1])
            dom = str(obj.get('domain', '')).strip()
        except Exception:
            pass
        f = _kid_domain_fields(dom)
        if not f:
            # ★ 兜底链：模型没选出来 → 关键词再试一次 → 仍不行就明确报错（绝不空域+records）
            hit, _s2 = _kid_fastpick(text)
            if hit:
                dom = hit
                f = _kid_domain_fields(dom)
            else:
                logger.warning(f"[PanelAssist] 选域失败（模型输出={raw[:60]!r}），拒绝空域入库")
                return [], '', model, ('没认出这句话该记进哪类档案。试试带上类目再说，'
                                       '比如「记一笔学习：…」「记一笔健康：…」「记一笔饮食：…」')
        # ③ 第二段：只带该域字段抽取（省 token）
        recs, model2, err2 = _extract_with_fields(text, dom, f['fields'], history, img_desc, today)
        return recs, dom, (model2 or model), err2

    if schema['kind'] == 'kid' and domain:
        # 指定了域（页签挂载）：直接按该域字段抽取，省 token
        f = _kid_domain_fields(domain)
        if not f:
            return [], domain, '', f'未知孩子档案域: {domain}'
        recs, model, err = _extract_with_fields(text, domain, f['fields'], history, img_desc, today)
        return recs, domain, model, err

    system = _extract_system(schema, today)
    prompt = (f"历史对话：\n{hist_txt}\n" if hist_txt else '') + f"用户消息：{text}{img_desc}"
    raw, model, err = _llm_call(system, prompt, max_tokens=400, temperature=0.1)
    if err:
        return [], domain, '', err
    arr = _parse_records_json(raw)
    if arr is None:
        return [], domain, '', '模型这次没按格式回话（内容没解析开），再发一次通常就好；还不行请截图到「设置 → 关于」反馈'
    valid = {f['key'] for f in schema['fields']}
    recs = []
    for r in arr:
        clean = {k: str(v)[:500] for k, v in r.items() if k in valid}
        if clean and any(str(v).strip() for v in clean.values()):
            recs.append(clean)
    # ★ #1057 位置归一：物品存放类面板，位置三列过别名归一+中文数字归一（'衣橱'→'大衣柜'、'左门第二层'→'左门第2层'）
    if (panel_id or '').startswith(('home_items', 'item_storage')) or panel_id in _storage_tables():
        try:
            from src.core.storage_places import resolve_place_input
            for rec in recs:
                room = rec.get('位置·房间', '') or ''
                cont = rec.get('位置·柜子家具', '') or ''
                slot = rec.get('位置·盒层', '') or ''
                if room or cont or slot:
                    n_room, n_cont, n_slot = resolve_place_input(_cn_num_norm(room), _cn_num_norm(cont), _cn_num_norm(slot))
                    if room:
                        rec['位置·房间'] = n_room
                    if cont:
                        rec['位置·柜子家具'] = n_cont
                    if slot:
                        rec['位置·盒层'] = n_slot
        except Exception as e:
            logger.debug(f'[storage] 录入位置归一失败（不影响入库）: {e}')
    return recs, domain, model, ''

# ══════════════════════════ 通道① 分类与默录 ══════════════════════════


def _extract_home(text, history, image_b64, today):
    """家庭空间（hub页）跨面板抽取：一句话自动分类到任意面板域。
    返回 (groups, 'home', model, err)，groups=[{panel_id,domain,label,icon,fields,records}]
    ★ #891 杀回落（2026-09-27）：抽取改走 _llm_call 跟随当前阵营（原写死本地，外脑模式下属于乱窜）。"""
    # 菜单：全部表面板 + kid 全部域
    menu_items = []   # (panel_id, domain, label, fields)
    for item in list_domains():
        pid = item['panel_id']
        if pid == 'kid_archive':
            for d in item['domains']:
                f = _kid_domain_fields(d['key'])
                if f:
                    menu_items.append((pid, d['key'], f"孩子成长档案·{d['label']}", f['fields']))
        else:
            sc = get_domain_schema(pid, '')
            if sc and sc.get('fields'):
                menu_items.append((pid, '', sc['label'], sc['fields']))
    if not menu_items:
        return [], 'home', '', '没有可录入的档案面板'
    menu = '\n'.join(
        f'- panel_id={pid} domain={dom or "-"}｜{label}｜字段键: {", ".join(f["key"] for f in fields)}'
        for pid, dom, label, fields in menu_items)

    hist_txt = ''
    for h in (history or [])[-6:]:
        role = '用户' if h.get('role') == 'user' else '你'
        hist_txt += f"{role}: {str(h.get('text', ''))[:200]}\n"

    # ★ A1 照片链（2026-10-11）：多图逐张转述（上限4），单图行为与旧版一致
    imgs = _norm_images(image_b64)
    img_desc = ''
    if imgs:
        descs = []
        for _i, _im in enumerate(imgs, 1):
            desc, _, err = _camp_vision_bg(
                '请详细描述这张图片的内容。如果是单据，抄出项目与金额；如果是记录本，抄出条目与数值。只描述事实。', _im)
            if err:
                return [], 'home', '', err
            descs.append(f"【图片{_i}】{desc[:800]}" if len(imgs) > 1 else desc[:800])
        img_desc = "\n【图片内容】" + '\n'.join(descs)

    # ★ #995 词典快车道（2026-10-05）：用户历史确认过的叫法直接定面板，跳过大模型选面板。
    #   词典只管「路由」，字段抽取照走原管线；抽不出内容/任何异常都落回大模型主链，绝不挡路。
    try:
        _lex = _lexicon_pick(text)
        if _lex and _lex[0]:
            _pid, _dom, _pat = _lex
            logger.info(f"[PanelAssist] #995 词典快车道命中「{_pat}」→ {_pid}/{_dom or '-'}")
            if _pid == 'kid_archive':
                if _dom:
                    _f = _kid_domain_fields(_dom)
                    if _f:
                        recs2, model2, err2 = _extract_with_fields(
                            text, _dom, _f['fields'], history, img_desc, today)
                        if not err2 and recs2:
                            return recs2, _dom, model2, err2
            else:
                sc2 = get_domain_schema(_pid, '')
                if sc2 and sc2.get('fields'):
                    recs2, model2, err2 = extract_records(
                        text, _pid, '', history=history, image_b64=image_b64)
                    if not err2 and recs2:
                        groups2 = [{'panel_id': _pid, 'domain': '', 'label': sc2['label'],
                                    'fields': sc2['fields'], 'records': recs2}]
                        return groups2, 'home', model2, ''
    except Exception as _le:
        logger.debug(f"[PanelAssist] #995 词典快车道跳过: {_le}")

    system = (
        "你是家庭生活空间录入助手。把用户的一句话分类到合适的家庭档案并拆解成记录。\n"
        f"【可选档案及字段键】\n{menu}\n"
        "【规则】\n"
        '1. 只输出一个 JSON 数组，每个元素形如 {"panel_id": "...", "domain": "...或空字符串", "records": [{...}]}。\n'
        "2. records 的键必须用该档案列出的字段键名（没提到的填 \"\"），用户说到的每个信息点都要落到语义最贴切的字段，不要丢弃。\n"
        "3. 一句话包含多件事且属于不同档案时，拆成多个元素。\n"
        f"4. 今天是 {today}；'今天/昨天'等换算成 YYYY-MM-DD；数字用阿拉伯数字。\n"
        "5. 纯提问/聊天/与所有档案无关时输出 []。\n"
        "只输出 JSON 数组，不要解释。")
    prompt = (f"历史对话：\n{hist_txt}\n" if hist_txt else '') + f"用户消息：{text}{img_desc}"
    raw, model, err = _llm_call(system, prompt, max_tokens=500, temperature=0.1)
    if err:
        return [], 'home', '', err
    arr = _parse_records_json(raw)
    if arr is None:
        return [], 'home', '', '模型这次没按格式回话（内容没解析开），再发一次通常就好；还不行请截图到「设置 → 关于」反馈'
    fields_map = {(pid, dom): fields for pid, dom, _, fields in menu_items}
    label_map = {(pid, dom): (label) for pid, dom, label, _ in menu_items}
    groups = []
    for g in arr:
        pid = str(g.get('panel_id', '')).strip()
        dom = str(g.get('domain', '')).strip()
        if (pid, dom) not in fields_map and (pid, '') in fields_map:
            dom = ''
        fields = fields_map.get((pid, dom))
        if not fields:
            continue
        valid = {f['key'] for f in fields}
        recs = []
        for r in (g.get('records') or []):
            clean = {k: str(v)[:500] for k, v in r.items() if k in valid}
            if clean and any(str(v).strip() for v in clean.values()):
                recs.append(clean)
        if not recs:
            continue
        groups.append({'panel_id': pid, 'domain': dom, 'label': label_map[(pid, dom)],
                       'fields': fields, 'records': recs})
    if not groups:
        return [], 'home', '', '没听出可以记的内容，试试带上具体信息。想去某个页面可以说"打开家庭生活"，或切到聊天模式深入聊'
    return groups, 'home', model, ''


def classify_home(text):
    """通道① 分类：动词触发 + 域关键词计分。返回 {trigger, panel_id, confidence}"""
    t = (text or '').strip()
    if not t:
        return {'trigger': False, 'panel_id': '', 'confidence': 'none'}
    # 纯提问过滤
    if any(h in t[:12] for h in QUESTION_HINTS) or t.rstrip().endswith(('？', '?')):
        return {'trigger': False, 'panel_id': '', 'confidence': 'none'}
    if not any(w in t for w in RECORD_TRIGGERS):
        return {'trigger': False, 'panel_id': '', 'confidence': 'none'}
    scores = {}
    for pid, kws in DOMAIN_KEYWORDS.items():
        s = sum(1 for k in kws if k in t)
        if s:
            scores[pid] = s
    if not scores:
        return {'trigger': True, 'panel_id': '', 'confidence': 'low'}
    best = max(scores, key=scores.get)
    others = [p for p, s in scores.items() if p != best and s == scores[best]]
    conf = 'high' if not others and scores[best] >= 2 else ('medium' if not others else 'low')
    return {'trigger': True, 'panel_id': best, 'confidence': conf}


def maybe_home_record(conv_id, user_message, assistant_reply, images=None):
    """通道① 主聊天默录钩子（engine.send 调用）。异步、默认开、失败静默。
    内部先走孩子档案归档（其自带默认关开关），再做家庭域默录。
    ★ 2026-09-27 修谎报记账：接收 images（base64 列表），首图传给抽取链——
    否则「发图+说记账」时视觉转述在聊天里成功了、录入抽取却拿不到图静默失败。"""
    try:
        init_db()
        # 孩子对话归档（独立开关，默认关）
        try:
            from src.core.kid_archive import maybe_archive_chat
            maybe_archive_chat(conv_id, user_message, assistant_reply)
        except Exception:
            pass
        s = get_assist_settings()
        if s.get('home_record_enabled', 'true') != 'true':
            return
        if conv_id in _home_archiving:
            return
        _home_archiving.add(conv_id)
        # ★ A1 照片链（2026-10-11）：多图全量传给默录链（上限4），不再只取首图
        try:
            imgs = [i for i in (images or []) if isinstance(i, str) and len(i) > 100][:4]
        except Exception:
            imgs = []
        th = threading.Thread(target=_home_worker, args=(conv_id, user_message, imgs), daemon=True)
        th.start()
    except Exception as e:
        logger.debug(f"[PanelAssist] 默录钩子跳过: {e}")


def _home_worker(conv_id, user_message, image_b64=''):
    try:
        # ★ #1143（2026-10-14）分析请求防误捕：问哈文升级后发出的「面板数据+请分析」消息
        #   长得像报账（有金额有日期），会被记账抽取误判 → 生成鬼影 pending 卡（点入库=记假账）。
        #   问哈文的消息统一带【仅分析请勿录入】前缀，这里硬拦截（本地判断，0 token）。
        if str(user_message or '').lstrip().startswith('【仅分析请勿录入】'):
            logger.info(f"[PanelAssist] conv={conv_id} 分析请求带勿录标记，跳过抽取")
            return
        cls = classify_home(user_message)
        if not cls.get('trigger'):
            return
        pid = cls['panel_id']
        conf = cls['confidence']
        if not pid:
            # 无域线索 → 不打扰用户，不猜
            logger.info(f"[PanelAssist] conv={conv_id} 有记录意图但无法定位档案，跳过")
            return
        domain = ''
        if pid == 'kid_archive':
            # 用域关键词缩小范围，猜不准就交给模型选域
            hits = {d: sum(1 for k in kws if k in user_message) for d, kws in KID_DOMAIN_HINTS.items()}
            domain = max(hits, key=hits.get) if max(hits.values() or [0]) > 0 else ''
        records, rdomain, model, err = extract_records(user_message, pid, domain, image_b64=image_b64)
        if err or not records:
            if err:
                logger.info(f"[PanelAssist] conv={conv_id} 默录抽取未成功: {err[:80]}")
            return
        final_domain = rdomain or domain
        if conf == 'high' or get_assist_settings().get('auto_insert') == 'true':
            ok, ins_err, ins_note = insert_records(pid, final_domain, records)
            logger.info(f"[PanelAssist] conv={conv_id} 默录入档 {pid}/{final_domain} "
                        f"{len(records) if ok else 0}条 {ins_err or ''} {ins_note or ''}")
            # ★ P0-A 发图默录：入库成功后把图片本体存进附件库并关联最新记录
            #   （09-27 实锤痛点：识别完图片用完即弃，事后查图必然落空）
            # ★ A1 多图化（2026-10-11）：多张图循环存，全部挂最新记录
            if ok and image_b64:
                try:
                    from src.core import panel_images
                    from src.core.panel_manager import DB_PATH as _PM_DB
                    from src.models.database import get_connection
                    c = get_connection(str(_PM_DB))
                    row = c.execute(f'SELECT MAX(id) FROM "panel_{pid}"').fetchone()
                    c.close()
                    for _im in _norm_images(image_b64):
                        panel_images.save_image(pid, _im, record_id=row[0] if row else None,
                                                note=f'主聊天默录 · {user_message[:60]}')
                except Exception as _img_e:
                    logger.debug(f"[PanelAssist] 默录存图跳过: {_img_e}")
        else:
            add_pending(pid, final_domain, records, user_message)
            # ★ P0-A 待确认场景也先存图（挂面板，确认后可补关联）；★ A1 多图循环存
            if image_b64:
                try:
                    from src.core import panel_images
                    for _im in _norm_images(image_b64):
                        panel_images.save_image(pid, _im, note=f'待确认记录配图 · {user_message[:60]}')
                except Exception:
                    pass
            logger.info(f"[PanelAssist] conv={conv_id} 低置信度 → 待确认队列 {pid}/{final_domain}")
    except Exception as e:
        logger.debug(f"[PanelAssist] 默录worker失败: {e}")
    finally:
        _home_archiving.discard(conv_id)

# ══════════════════════════ 入库 ══════════════════════════


def _bump_if_written(panel_id, n):
    """★ #1220-A：面板数据写入成功 → 数据版本 +1（手机端感知刷新）"""
    if n and n > 0:
        try:
            from src.core.sync_bus import bump
            bump(f'panel:{panel_id}')
        except Exception:
            pass


def insert_records(panel_id, domain, records, child_id=None, image_b64='', image_quality='', image_notes=None):
    """把抽取结果写入对应档案。返回 (成功条数, 错误信息, 附加说明)
    ★ #913 查重：同日同金额近似名的记录自动跳过（京东 6 连重实锤后的缝）。
    ★ #911 带图确认：image_b64 非空且入库成功 → 存进附件库挂到最新一条记录。
    ★ A1/A5（2026-10-11 照片链）：image_b64 支持多图列表；image_quality 画质档
    （hd/compressed）透传进附件库，原图补传替换链靠它识别旧图。
    ★ #1407-A/C（2026-11-03）：image_notes 与多图一一对应（确认卡补拍的角色标注：
    柜子/抽屉/物品特写…），逐图写进附件 note——一物多照不都是同一句备注。"""
    if not records:
        return 0, '', ''
    try:
        if panel_id == 'kid_archive':
            from src.core.kid_archive import DOMAINS, get_children, insert_domain
            if domain not in DOMAINS:
                return 0, f'未知孩子档案域: {domain}', ''
            if not child_id:
                children = get_children()
                if not children:
                    return 0, '尚未建立孩子档案，请先在成长档案→设置里建档', ''
                child_id = next((c['id'] for c in children if c.get('is_default')), children[0]['id'])
            n = 0
            for rec in records:
                try:
                    insert_domain(child_id, domain, rec)
                    n += 1
                except Exception:
                    pass
            # ★ #1220-A：孩子档案写入 → 数据版本 +1
            if n > 0:
                try:
                    from src.core.sync_bus import bump
                    bump(f'panel:{panel_id}')
                except Exception:
                    pass
            return n, '', ''
        from src.core.panel_manager import get_panel_manager
        schema = get_domain_schema(panel_id, '')
        valid = {f['key'] for f in (schema or {}).get('fields', [])}
        pm = get_panel_manager()
        panel = pm.get_panel(panel_id)
        features = getattr(panel, 'features', {}) or {}
        n = 0
        dup_n = 0
        last_err = ''
        last_id = None
        _acc_rules = None  # ★ 2026-09-17 方案A 自定义分类规则（懒加载一次）
        _link_notes = []   # ★ 2026-10-10 #1061 外卖记账联动提示（每条入库收集）
        for rec in records:
            data = {k: v for k, v in rec.items() if k in valid and str(v).strip()}
            if not data:
                continue
            # ★ 2026-10-10 #1062 B3 热量诚实化：AI 没填/填没认出的热量 → 本地词典按家常份量
            #   粗估回填（识图链已禁止 AI 猜热量，这里是录入链统一兜底，标注仅供参考）
            if panel_id == 'meal_diary':
                try:
                    from src.core.meal_insights import estimate_calories as _meal_cal
                    _foods = data.get('识别出的食物') or ''
                    _cal = data.get('估算热量')
                    if _foods and not (isinstance(_cal, (int, float)) and _cal > 0):
                        _est, _d, _u = _meal_cal(_foods)
                        if _est > 0 and '估算热量' in valid:
                            data['估算热量'] = _est
                except Exception:
                    pass
            # ★ #913 入库查重：同日+同金额+近似名 → 跳过（不再 6 连重）
            if pm.find_duplicate(panel_id, data):
                dup_n += 1
                logger.info(f"[PanelAssist] 查重命中跳过 {panel_id}: {data}")
                continue
            # ★ 2026-09-16 记账面板统一口径：小窗抽取的账目补 kind/方向/来源，
            #   分类用关键词兜底修正（实锤 AI 把"早餐"选成"买菜"）
            # ★ 2026-09-17 方案A：自定义分类关键词优先（每批读一次规则）
            if panel_id == 'accounting_daily_1' and '预算' not in data:
                from src.core.acc_categories import classify_ex as _acc_cls, load_custom_rules as _acc_rules_fn
                if _acc_rules is None:
                    _acc_rules = _acc_rules_fn()
                _item = data.get('项目') or data.get('名称') or ''
                _cat, _dir, _dir_hit = _acc_cls(_item, data.get('分类', ''), _acc_rules)
                data['分类'] = _cat
                # ★ #888 AI双判方向：关键词命中方向时以关键词为准（确定性规则覆盖 AI 误判，
                #   实锤 id=161「入账18万」AI 判支出）；关键词未命中保留 AI 判定
                if _dir_hit or not data.get('方向'):
                    data['方向'] = _dir
                if '状态' in data or '名称' in data:
                    data.setdefault('kind', 'expected')
                    data.setdefault('到期日', data.get('到期日') or data.get('日期') or '')
                elif '金额' in data:
                    data.setdefault('kind', 'flow')
                    data.setdefault('来源', '哈文助手')
            # ★ #1378（2026-11-02）冰箱录入兜底统一走公共函数 fridge_intake_guard
            #   （分区校验门+到期日词典+状态默认；与确认卡/FC/拍照识菜/L1 五入口同一落点）
            if panel_id.startswith('fridge_stock'):
                try:
                    from src.core.fridge_hub import fridge_intake_guard as _fig, _append_note as _apn
                    _gn = _fig(data)
                    _apn(data, _gn)
                except Exception:
                    pass
            try:
                saved = pm.insert_panel_data(panel_id, data)
                n += 1
                last_id = (saved or {}).get('id')
                # ★ 2026-09-16 记录→提醒联动（features.remind 面板自动挂提醒）
                try:
                    _sync_record_reminder(panel_id, features, data, last_id)
                except Exception:
                    pass
                # ★ 2026-10-10 #1061 外卖自动记账联动（餐饮→记账待确认卡，两段门）
                try:
                    from src.core.meal_insights import maybe_link_accounting as _mla
                    _link_notes.append(_mla(panel_id, data))
                except Exception:
                    pass
            except Exception as e:
                # ★ 2026-09-16 不再静默吞异常：入库失败必须让用户看到（实锤假成功事故）
                last_err = str(e)
                logger.error(f"[PanelAssist] insert_records 落库失败({panel_id}): {e}; data={data}")
        # ★ #911 带图确认：图本体进附件库、挂到本批最新一条记录
        # ★ A1/A5（2026-10-11）：多图循环存 + 画质档标注入库
        # ★ #1407-A/C（2026-11-03）：image_notes 逐图角色标注一一对应（短了用默认补齐）
        if n > 0 and image_b64 and last_id:
            try:
                from src.core import panel_images
                _ims = _norm_images(image_b64)
                _notes = list(image_notes or [])
                for _i, _im in enumerate(_ims):
                    _nt = str(_notes[_i]).strip() if _i < len(_notes) and _notes[_i] else ''
                    panel_images.save_image(panel_id, _im, record_id=last_id,
                                            note=_nt or '哈文助手确认入库配图', quality=image_quality)
            except Exception as _ie:
                logger.debug(f"[PanelAssist] 确认配图存图跳过: {_ie}")
        note = f'{dup_n} 条疑似重复已自动跳过' if dup_n else ''
        # ★ 2026-10-10 #1061 外卖记账联动提示拼进 note（前端确认完成后展示）
        _links = [x for x in _link_notes if x]
        if _links:
            note = (note + '；' if note else '') + '；'.join(_links)
        if n < len([r for r in records if any(k in r and str(r[k]).strip() for k in r)]) and last_err:
            _bump_if_written(panel_id, n)
            return n, f'有 {len(records) - n} 条入库失败: {last_err}', note
        # ★ 2026-10-22 记账钩子：入库后核查花销型目标，超支/接近当场反馈（拼进附加说明）
        if panel_id == 'accounting_daily_1':
            try:
                from src.core.coach_engine import check_spend_goals
                _alerts = check_spend_goals()
                if _alerts:
                    note = (note + '　' if note else '') + '　'.join(a['title'] for a in _alerts[:2])
            except Exception:
                pass
        _bump_if_written(panel_id, n)
        return n, '', note
    except Exception as e:
        return 0, str(e), ''

# ══════════════════════════ 待确认队列 ══════════════════════════

# ★ S1 三小活（2026-11-12 定版拍板「开发」）：
#   ① 老卡 7 天自动过期——忘在收纳盒里的卡不能堆积成垃圾场；
#   ② 重复发言去重——同一句「47.6 炸酱面」连说两遍只出一张卡（10 分钟窗口）。
_PENDING_EXPIRE_DAYS = 7
_PENDING_DEDUP_WINDOW_MIN = 10
_pending_cleanup_last = [0.0]      # 惰性清理节流（300s 内不重复跑）


def _pending_norm_key(records):
    """records 规范化指纹：同内容不同键序视为相同（去重比对用）"""
    try:
        return json.dumps(records, ensure_ascii=False, sort_keys=True)
    except Exception:
        return ''


def _cleanup_expired_pending():
    """老卡 7 天自动过期（直接删，不加状态列——过期卡=没人要的卡，留着只有害）。返回清理条数"""
    c = _conn()
    try:
        cur = c.execute("DELETE FROM assist_pending WHERE created_at < datetime('now', ?, 'localtime')",
                        (f'-{_PENDING_EXPIRE_DAYS} days',))
        c.commit()
        if cur.rowcount:
            logger.info(f"[PanelAssist] S1 待确认老卡过期清理: {cur.rowcount} 条（>{_PENDING_EXPIRE_DAYS}天）")
        return cur.rowcount
    finally:
        c.close()


def add_pending(panel_id, domain, records, source_text='', child_id=None):
    """★ S1 去重：同面板 + 相同 records + 10 分钟窗口内已有未确认卡 → 拒收（返回 False）。
    覆盖两种实锤场景：同一句话连发两遍（records 全同）；换个说法重说一遍（改口径但抽取结果同）。
    不同内容不受影响；超过窗口的视为有意再记一笔，照常放行（#913 入库查重仍是最后防线）。"""
    with _lock:
        c = _conn()
        try:
            # ── ② 重复发言去重 ──
            nk = _pending_norm_key(records)
            if nk:
                dup = c.execute(
                    "SELECT id FROM assist_pending WHERE panel_id=? AND records_json=? "
                    "AND created_at >= datetime('now', ?, 'localtime') LIMIT 1",
                    (panel_id, nk, f'-{_PENDING_DEDUP_WINDOW_MIN} minutes')).fetchone()
                if dup:
                    logger.info(f"[PanelAssist] S1 重复发言去重: 跳过 pending（panel={panel_id}, "
                                f"窗口{_PENDING_DEDUP_WINDOW_MIN}分钟内已有同内容卡 id={dup[0]}）")
                    return False
            # 写入也用规范化串（sort_keys）：去重比对才可靠（同内容不同键序=同指纹）
            c.execute("INSERT INTO assist_pending(panel_id, domain, child_id, records_json, source_text) VALUES(?,?,?,?,?)",
                      (panel_id, domain or '', child_id, nk, source_text[:300]))
            c.commit()
        finally:
            c.close()
        # ── ① 老卡过期：惰性触发（节流 300s，扫一张小表 <1ms）──
        try:
            import time as _t
            if _t.time() - _pending_cleanup_last[0] > 300:
                _pending_cleanup_last[0] = _t.time()
                _cleanup_expired_pending()
        except Exception:
            pass
    return True


def list_pending(panel_id=''):
    c = _conn()
    try:
        sql = "SELECT * FROM assist_pending ORDER BY id DESC LIMIT 100"
        rows = c.execute(sql).fetchall() if not panel_id else c.execute(
            "SELECT * FROM assist_pending WHERE panel_id=? ORDER BY id DESC LIMIT 100", (panel_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['records'] = json.loads(d.pop('records_json') or '[]')
            out.append(d)
        return out
    finally:
        c.close()


def confirm_pending(pid, records=None):
    """确认待确认条目入库（可带用户修改后的 records）"""
    c = _conn()
    try:
        row = c.execute("SELECT * FROM assist_pending WHERE id=?", (pid,)).fetchone()
    finally:
        c.close()
    if not row:
        return False, '条目不存在'
    recs = records if records is not None else json.loads(row['records_json'] or '[]')
    # ★ #1379-D（2026-11-02）撤销入账基建：落库前记 MAX(id)，落库后比差出本次插入的行 id
    _max_before = 0
    _posted_ids = []
    try:
        if str(row['panel_id']) != 'kid_archive':
            from src.models.database import get_connection as _gc
            from src.core.panel_manager import DB_PATH as _PM_DB
            _c0 = _gc(str(_PM_DB))
            _r0 = _c0.execute(f'SELECT MAX(id) FROM "panel_{row["panel_id"]}"').fetchone()
            _max_before = int(_r0[0] or 0)
            _c0.close()
    except Exception:
        pass
    n, err, ins_note = insert_records(row['panel_id'], row['domain'], recs, row['child_id'])
    if err:
        return False, err
    # 比差行 id → 存进撤销账本（供「撤销刚才入账」用）
    if _max_before:
        try:
            from src.models.database import get_connection as _gc
            from src.core.panel_manager import DB_PATH as _PM_DB
            import time as _tt
            _c1 = _gc(str(_PM_DB))
            _rows = _c1.execute(f'SELECT id FROM "panel_{row["panel_id"]}" WHERE id>?', (_max_before,)).fetchall()
            _c1.close()
            _sum = '；'.join(
                f"{r.get('项目') or r.get('名称') or '消费'} {r.get('金额', '')}".strip()
                + ('元' if str(r.get('金额', '')) else '') for r in (recs or [])[:3])
            _last_posted.update({'panel_id': row['panel_id'], 'domain': row['domain'] or '',
                                 'ids': [r[0] for r in _rows], 'summary': _sum, 'ts': _tt.time()})
        except Exception:
            pass
    delete_pending(pid)
    # ★ #995 词典闭环：确认卡=用户亲自确认过的「话→面板」映射，自动沉淀（下次秒路由）
    try:
        _words = _pick_lexicon_words(recs)
        if _words:
            add_lexicon(_words, row['panel_id'], row['domain'] or '', source='confirm')
            logger.info(f"[PanelAssist] #995 确认卡沉淀叫法 {_words} → {row['panel_id']}")
    except Exception as _le:
        logger.debug(f"[PanelAssist] 词典沉淀跳过: {_le}")
    if ins_note:
        return True, f'{n}条已入库（{ins_note}）'
    # ★ P0-A 图片补关联：待确认期间先存的图（record_id 空），确认入库后挂到最新记录
    try:
        from src.core import panel_images as _pimg
        from src.core.panel_manager import DB_PATH as _PM_DB
        from src.models.database import get_connection as _gc
        _c = _gc(str(_PM_DB))
        _row = _c.execute(f'SELECT MAX(id) FROM "panel_{row["panel_id"]}"').fetchone()
        _c.close()
        if _row and _row[0]:
            for img in _pimg.list_images(row['panel_id'], limit=20):
                if not img.get('record_id') and str(img.get('note', '')).startswith('待确认'):
                    _pimg.attach_to_record(img['filename'], _row[0])
    except Exception:
        pass
    return True, f'{n}条已入库'


def delete_pending(pid):
    with _lock:
        c = _conn()
        try:
            c.execute("DELETE FROM assist_pending WHERE id=?", (pid,))
            c.commit()
        finally:
            c.close()
    return True


def pending_count(panel_id=''):
    c = _conn()
    try:
        if panel_id:
            return c.execute("SELECT COUNT(*) FROM assist_pending WHERE panel_id=?", (panel_id,)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM assist_pending").fetchone()[0]
    finally:
        c.close()


def clear_pending(panel_id=''):
    """★ S1 一键清空：收纳盒全清（带 panel_id 则只清该面板）。只动 assist_pending，
    绝不碰已入库数据；前端按钮自带确认弹窗，这里不再二次设防。"""
    c = _conn()
    try:
        if panel_id:
            cur = c.execute("DELETE FROM assist_pending WHERE panel_id=?", (panel_id,))
        else:
            cur = c.execute("DELETE FROM assist_pending")
        c.commit()
        n = cur.rowcount
        if n:
            logger.info(f"[PanelAssist] S1 待确认一键清空: {n} 条（panel_id={panel_id or '全部'}）")
        return n
    finally:
        c.close()


# ══════════════════════════ 记录→提醒联动（features.remind） ══════════════════════════


def _sync_record_reminder(panel_id, features, rec, ref_id):
    """面板开了 features.remind 时，按配置从记录字段自动挂一条提醒。
    配置示例：
      {"title_field": "药品名称", "date_field": "有效期至", "advance_days": 14, "label": "药品过期"}
      {"title_fields": ["老人姓名","药物名称"], "time_field": "服药时间", "repeat_field": "重复", "default_repeat": "daily"}
      {"mode": "cycle", "title_field": "植物名", "date_field": "最近浇水", "cycle_field": "浇水周期天数"}
    """
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    from src.core import home_reminder as hr

    cfg = (features or {}).get('remind')
    if not cfg:
        return
    # 状态满足则不挂（如 已缴费/已完成）
    skip_field, skip_if = cfg.get('skip_field'), cfg.get('skip_if')
    if skip_field and skip_if and str(rec.get(skip_field, '') or '').strip() == skip_if:
        return
    date_v = ''
    if cfg.get('date_field'):
        date_v = str(rec.get(cfg['date_field'], '') or '').strip()
        if not date_v:
            return  # 配置了日期字段但记录没填 → 不挂提醒
        base = hr._parse_dt(date_v)
        if not base:
            return
        # ★ #1157 纯日期没填时间 → 默认 09:00 提醒（零点横幅没意义；_parse_dt 历史行为是 00:00）
        if not (cfg.get('time_field') and rec.get(cfg['time_field'])) \
                and base.hour == 0 and base.minute == 0:
            base = base.replace(hour=9, minute=0)
    else:
        base = _dt.now()  # 无日期字段（如老人每日服药）→ 从今天开始提醒

    if cfg.get('mode') == 'cycle':
        # 周期模式：基准日 + 周期天数，之后按间隔重复
        try:
            cycle = int(float(str(rec.get(cfg.get('cycle_field', ''), '') or 0)))
        except (TypeError, ValueError):
            cycle = 0
        if cycle <= 0:
            return
        remind_dts = [base + _td(days=cycle)]
        repeat, interval = 'interval_days', cycle
    else:
        # 普通模式：基准日（可选时间/提前量/重复）
        tf = cfg.get('time_field')
        if tf and rec.get(tf):
            m = _re.match(r'^(\d{1,2}):(\d{2})', str(rec[tf]).strip())
            if m:
                base = base.replace(hour=min(23, int(m.group(1))), minute=int(m.group(2)))
        # ★ #1157（2026-10-16）advance_days 支持多档列表（如 [3, 0] = 临期前3天 + 当天各挂一条）；
        #   advance_field（用户填的提前量）优先且仍单档
        af = cfg.get('advance_field')
        if af and str(rec.get(af, '') or '').strip() in hr.ADVANCE_ZH_MAP:
            adv_minutes_list = [hr.ADVANCE_ZH_MAP[str(rec[af]).strip()]]
        else:
            _adv = cfg.get('advance_days')
            _days = _adv if isinstance(_adv, list) else ([_adv] if _adv else [0])
            adv_minutes_list = []
            for _d in _days:
                try:
                    adv_minutes_list.append(int(float(_d)) * 1440)
                except (TypeError, ValueError):
                    pass
            if not adv_minutes_list:
                adv_minutes_list = [0]
        remind_dts = [base - _td(minutes=mv) for mv in adv_minutes_list]
        repeat = 'once'
        interval = 0
        rf = cfg.get('repeat_field')
        if rf and str(rec.get(rf, '') or '').strip() in hr.REPEAT_ZH_MAP:
            repeat = hr.REPEAT_ZH_MAP[str(rec[rf]).strip()]
        elif cfg.get('default_repeat'):
            repeat = cfg['default_repeat']

    # 标题
    if cfg.get('title_fields'):
        title = '·'.join(str(rec.get(k, '') or '').strip() for k in cfg['title_fields'] if rec.get(k))
    else:
        title = str(rec.get(cfg.get('title_field', ''), '') or '').strip()
    if not title:
        title = panel_id
    label = cfg.get('label')
    title = f"【{label}】{title}" if label else title

    rid_errs = []
    for rdt in remind_dts:
        _at = hr._fmt(rdt)
        # ★ #1157 查重：同模块+同标题+同时点已有未完成提醒 → 不重复挂
        #   （同名累加入库/重开确认卡都会再走这里，没查重会堆一栈同款提醒）
        try:
            _c = hr._conn()
            try:
                _dup = _c.execute(
                    "SELECT 1 FROM home_reminders WHERE module=? AND title=? AND remind_at=? "
                    "AND notified=0 LIMIT 1", (panel_id, title[:200], _at)).fetchone()
            finally:
                _c.close()
            if _dup:
                continue
        except Exception:
            pass
        rid, err = hr.add_reminder(module=panel_id, title=title[:200],
                                   remind_at=_at, repeat=repeat,
                                   interval_value=interval, advance=0,
                                   note='随档案记录自动创建', ref_id=ref_id, source='panel')
        if err:
            rid_errs.append(err)
    if rid_errs:
        logger.debug(f"[PanelAssist] 记录联动提醒失败: {rid_errs[0]}")


# ══════════════════════════ 对话框 2.0：问答双模统一入口 ══════════════════════════

REMINDER_INTENT_KW = ['提醒', '别忘了', '不要忘', '记得去', '定个闹钟', '设个提醒', '定个提醒']


def _is_reminder_intent(t):
    import re as _re
    if any(k in t for k in REMINDER_INTENT_KW):
        return True
    head = t[:6]
    return any(k in head for k in ('日程', '日历', '预约'))


def _is_question(t):
    t = (t or '').strip()
    if t.rstrip().endswith(('？', '?')):
        return True
    return any(h in t[:12] for h in QUESTION_HINTS)


def _collect_answer_context(panel_id='', domain='', limit_rows=40, question=''):
    """收集用于问答的本机档案数据（限额，防上下文爆炸）。全部只读本地。
    ★ P0-B 2026-09-27：带 question 时先走 FTS5 全文索引命中最相关记录（快 10 倍+省 token），
    命中不足再补最近记录；同时给 AI 报图片附件概况（图片不再不可见）。"""
    lines = []
    if panel_id == 'kid_archive':
        from src.core.kid_archive import get_children, query_domain, DOMAINS
        doms = [domain] if (domain and domain in DOMAINS) else \
            [k for k in DOMAINS if k not in ('game_logs', 'interest_logs')]
        for c in get_children()[:3]:
            lines.append(f"【孩子】{c['name']}")
            for d in doms[:12]:
                try:
                    rows = query_domain(c['id'], d, limit=20)
                except Exception:
                    continue
                if rows:
                    label = DOMAINS[d][2]
                    parts = []
                    for r in rows[:12]:
                        slim = {k: v for k, v in dict(r).items() if v and k not in ('id', 'child_id')}
                        if slim:
                            parts.append(json.dumps(slim, ensure_ascii=False)[:160])
                    lines.append(f"- {label}（最近{len(rows)}条）：{'；'.join(parts)[:1000]}")
    elif panel_id:
        from src.core.panel_manager import get_panel_manager
        pm = get_panel_manager()
        panel = pm.get_panel(panel_id)
        label = panel.name if panel else panel_id
        keys = [f.get('name') for f in (panel.fields if panel else [])]
        # ★ P0-B FTS 先命中：与问题最相关的记录排最前（命中即不用读全表）
        fts_rows = []
        if question:
            try:
                fts_rows = pm.fts_search(question, panel_id=panel_id, limit=10)
            except Exception:
                fts_rows = []
        if fts_rows:
            lines.append(f"【{label}】与你问题「{question[:40]}」最相关的记录：")
            try:
                from src.core import panel_images as _pimg_fts
            except Exception:
                _pimg_fts = None
            for hit in fts_rows[:10]:
                try:
                    rows = pm.query_panel_data(panel_id, filters={'id': hit['record_id']}, limit=1)
                except Exception:
                    rows = []
                if rows:
                    slim = {k: str(rows[0].get(k, ''))[:60] for k in keys if rows[0].get(k)}
                    # ★ 2026-09-27 逐条标注随记图数量（无图不加标注，有图明确说）
                    if _pimg_fts is not None:
                        try:
                            _n = len(_pimg_fts.list_images(panel_id, record_id=hit['record_id']))
                        except Exception:
                            _n = 0
                        if _n:
                            slim['随记图片'] = f'{_n}张（面板里点📷可看）'
                    if slim:
                        lines.append('- ' + json.dumps(slim, ensure_ascii=False)[:220])
        rows = pm.query_panel_data(panel_id, limit=limit_rows)
        lines.append(f"【{label}】共可读到最近 {len(rows)} 条：")
        for r in rows[:limit_rows]:
            slim = {k: str(r.get(k, ''))[:60] for k in keys if r.get(k)}
            if slim:
                lines.append('- ' + json.dumps(slim, ensure_ascii=False)[:200])
        # ★ P0-A 图片附件概况：让 AI 知道有图、图在哪查
        # ★ 2026-09-27 反幻觉：0 张时必须明说无图，否则模型会编造「图片存在附件库里点开就能看」
        try:
            from src.core import panel_images as _pimg
            imgs = _pimg.list_images(panel_id, limit=10)
            if imgs:
                # ★ 2026-10-25 #1191 反幻觉：limit=10 封顶，满 10 只能说「至少」，不能当精确数
                _n_txt = f'至少 {len(imgs)} 张' if len(imgs) >= 10 else f'{len(imgs)} 张'
                lines.append(f"【{label}】有 {_n_txt} 关联图片（存于图片附件库，"
                             f"note 描述：{'；'.join((i.get('note') or i['created_at']) for i in imgs[:5])}）。"
                             f"用户要看图请到面板对应记录点开，文件系统里搜不到。")
            elif question and any(w in question for w in ('图', '照片', '相片', '截图', 'pic', 'image')):
                # ★ 2026-09-27 反幻觉：仅用户问到图片时才说明现状（0 张明说，杜绝编造「图在库里」）
                lines.append(f"【{label}】图片附件库现状：0 张图片。"
                             f"此前聊天里发过的截图没有保存机制、已不可找回。"
                             f"用户问图时必须如实说没有图，绝不能说图片存在库里；"
                             f"可建议重新发图片并说「存入记账」即可自动存图关联记录。")
        except Exception:
            pass
    else:
        # 家庭空间（hub）：全部面板各取少量 + 近期提醒/日程
        # ★ P0-B 带问题时先跨面板 FTS 命中（不再全靠逐面板读 8 条）
        from src.core.panel_manager import get_panel_manager
        pm = get_panel_manager()
        if question:
            try:
                hits = pm.fts_search(question, limit=12)
            except Exception:
                hits = []
            if hits:
                lines.append(f'【全文检索「{question[:40]}」最相关记录】')
                for h in hits[:12]:
                    lines.append(f"- [{h['panel_id']}#{h['record_id']}] {h['text'][:180]}")
            # ★ 2026-09-27 反幻觉：用户问图时，报图库权威总数（0 张必须明说）
            # ★ 2026-10-25 #1191 根治假数：改用 img_store.count_all() 真 COUNT——
            #   旧法「每面板各取5张累加」10 面板封顶 50，出过「50 张」假数（开发者截图实锤）
            if question and any(w in question for w in ('图', '照片', '相片', '截图', 'pic', 'image')):
                try:
                    from src.core import img_store as _ig_hub
                    _st = _ig_hub.count_all()
                    _total, _albums = _st['total'], [a for a in _st['albums'] if a['count'] > 0]
                    if _total:
                        _detail = [f"{a['name']} {a['count']}张" for a in _albums[:10]]
                        lines.append(f'【图库实况】图库共 {_total} 张照片（真数，直接引用）：{"；".join(_detail)}。'
                                     f'找具体照片调 search_images 工具；用户也能在「图片库」页浏览全部相册。')
                    else:
                        lines.append('【图库实况】图库共 0 张照片。'
                                     '此前聊天里发过的截图没有保存机制、已不可找回。'
                                     '必须如实说没有图，绝不能编造「图片存在库里」；'
                                     '可建议用户重新发图片并说「存入记账」即可自动存图关联。')
                except Exception:
                    pass
        for item in list_domains():
            pid = item['panel_id']
            if pid == 'kid_archive':
                lines.append(f"- {item['name']}：孩子成长档案（学习/健康/兴趣等 12 类）")
                continue
            panel = pm.get_panel(pid)
            rows = pm.query_panel_data(pid, limit=8)
            keys = [f.get('name') for f in (panel.fields if panel else [])]
            parts = []
            for r in rows[:8]:
                slim = {k: str(r.get(k, ''))[:40] for k in keys if r.get(k)}
                if slim:
                    parts.append(json.dumps(slim, ensure_ascii=False)[:150])
            if parts:
                lines.append(f"- {item['name']}：{'；'.join(parts)[:700]}")
        try:
            from src.core import home_reminder as hr
            due = hr.due_reminders(5)
            if due:
                lines.append("【马上要提醒的】" + '；'.join(r['title'] for r in due))
            evs = hr.list_events(upcoming_days=7, limit=8)
            if evs:
                lines.append("【未来7天日程】" + '；'.join(
                    f"{e['event_date']} {e['event_time'] or ''} {e['title']}" for e in evs))
        except Exception:
            pass
    return '\n'.join(lines)[:4500]


def _trim_system_history(engine):
    """★ 2026-09-19 修B：__system__ 常驻会话历史裁剪（防上下文污染）。
    面板对话框复用引擎单例（conversation_id='__system__'），历史一直累积不清空，
    旧话题会串进新回答（实锤案例：问面板为什么没人用，模型答「校准后是100000」——
    那是之前聊过的里程表话题）。内存只留最近6轮；数据库超过40条裁到最近40条。
    __system__ 只是面板对话框的便签本，不需要长记忆。"""
    try:
        if getattr(engine, 'conversation_id', '') != '__system__':
            return
        if len(engine.history) > 12:
            engine.history = engine.history[-12:]
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute(
                "DELETE FROM messages WHERE conversation_id='__system__' AND id NOT IN ("
                "SELECT id FROM messages WHERE conversation_id='__system__' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 40)")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f'[PanelAssist] __system__ 历史裁剪失败（不影响对话）: {e}')


def _free_chat_send(t, panel_id, domain='', extra_context='', page_context='', image_b64=''):  
    """★ 2026-09-16 方案C：自由对话 → 完整引擎接管。
    复用引擎单例（conversation_id='__system__'，不污染主对话历史）；
    查询模式不传 = 沿用首页当前设置；把"此刻在哪"和页面上下文带给引擎。
    ★ 2026-09-23：支持带图（聊天模式发照片问内容 → engine 路由 minicpm-v 看图）。"""
    from src.core.engine import get_conversation_engine
    where = ''
    try:
        if panel_id:
            from src.core.panel_manager import get_panel_manager
            p = get_panel_manager().get_panel(panel_id)
            if p:
                where = f"（用户此刻在「{p.name}」面板的对话框里说话）"
        else:
            where = "（用户此刻在家庭空间的对话框里说话）"
    except Exception:
        pass
    msg = t
    ctx_bits = []
    if extra_context:
        ctx_bits.append(str(extra_context)[:1500])
    if page_context:
        ctx_bits.append(str(page_context)[:1500])
    if ctx_bits:
        msg = t + "\n\n【背景补充（供参考，不用复述）】\n" + "\n".join(ctx_bits)
    parts = []
    try:
        engine = get_conversation_engine()
        _trim_system_history(engine)  # ★ 2026-09-19 修B：发送前裁历史，防旧话题串台
        # ★ 2026-09-23：带图 → dataURL 交给 engine（自动路由本地看图模型）
        imgs = []
        if image_b64:
            _mime = 'image/png' if str(image_b64).startswith('iVBOR') else 'image/jpeg'
            imgs = [f'data:{_mime};base64,{image_b64}']
        for mtype, content in engine.send(((where + ' ') if where else '') + msg, stream=True, images=imgs,
                                          pure_chat=True):  # ★ 2026-09-26 模式分开（已定案A）：浮窗聊天=纯聊，跳过记事拦截；想记账走本模块确认卡流程
            if mtype == 'text':
                parts.append(content)
    except Exception as e:
        return 'answer', None, '', f'完整引擎调用失败：{e}'
    text_out = ''.join(parts).strip()
    if not text_out:
        return 'answer', None, '', '哈文这次没答上来，再问一次试试'
    return 'answer', {'text': text_out, 'free': True}, '完整引擎', ''


# ── ★ 2026-09-18 对话造分类：分类=面板定义（纯 JSON 无代码），建好立即可录、天然可上 Hub ──

CREATE_PANEL_KW = ['建个分类', '建分类', '新建分类', '创建分类', '加个分类', '建一个分类',
                   '建个面板', '新建面板', '创建面板', '加个面板', '我想建个', '帮我建个',
                   # ★ #1037（2026-10-06）开发者在主聊天说「帮我建一个模块」没触发——实锤触发词只认「分类/面板」。
                   #   补「模块/台账」说法（主聊天走 FC create_panel，这里是面板助手对话框的平行入口）
                   '建个模块', '建一个模块', '新建模块', '创建模块', '加个模块',
                   '建个台账', '建一个台账', '新建台账', '创建台账', '加个台账']


def _create_panel_from_text(t):
    """用户一句话建新分类：AI 设计字段 → create_panel_from_template 落地。
    返回 (mode, payload, model, err)"""
    from src.core.panel_manager import get_panel_manager
    system = ("你是家庭面板设计师。用户想新增一个记录分类，把他的描述设计成面板字段。"
              "只输出JSON：{\"name\":\"分类名(2-6字中文)\", \"icon\":\"一个emoji\", "
              "\"fields\":[{\"name\":\"字段键名(中文,如 名称/日期/金额)\", \"label\":\"同name\", "
              "\"type\":\"text或number或date或select\", \"options\":[仅select给,最多6个]}]}。"
              "字段2-6个：第一字段是名称/项目类，有日期就带日期，金额类给number。只输出JSON，不要解释。")
    raw, model, err = _llm_call(system, t, max_tokens=350, temperature=0.2)
    if err:
        return '', None, '', '生成分类定义失败：' + err
    try:
        obj = json.loads(raw[raw.find('{'):raw.rfind('}') + 1])
    except Exception:
        return '', None, '', '生成的分类定义没解析出来，换个说法再试一次'
    name = str(obj.get('name') or '').strip()[:12]
    if not name:
        return '', None, '', '没起出分类名，描述再具体一点'
    # 重名检查：已有同名分类 → 提示直接用（省一次创建）
    try:
        for it in list_domains():
            if (it.get('name') or '').strip() == name:
                return ('answer', {'text': f'已经有「{name}」这个分类了，直接跟我说就能记，'
                                           f'比如「记一笔{name}：…」'}, model, '')
    except Exception:
        pass
    fs = []
    for f in (obj.get('fields') or [])[:6]:
        fn = str(f.get('name') or f.get('label') or '').strip()[:12]
        if not fn:
            continue
        ftype = str(f.get('type') or 'text').lower()
        if ftype not in ('text', 'number', 'date', 'select'):
            ftype = 'text'
        fs.append({'name': fn, 'label': str(f.get('label') or fn), 'type': ftype,
                   'options': [str(o)[:20] for o in (f.get('options') or [])[:8]]})
    if not fs:
        return '', None, '', '没设计出字段，说说你想记哪些信息（比如 书名、日期、感想）'
    pid = 'user_' + datetime.now().strftime('%m%d%H%M%S')
    icon = str(obj.get('icon') or 'note')[:8]
    try:
        result = get_panel_manager().create_panel_from_template(
            name, pid, fs, '对话创建 · ' + t[:80], icon, with_chart=False, with_search=True)
    except Exception as e:
        return '', None, '', f'创建失败：{e}'
    if not (result or {}).get('success'):
        return '', None, '', (result or {}).get('message') or '创建失败'
    logger.info(f"[PanelAssist] 对话造分类成功: {pid} {name} 字段={[f['name'] for f in fs]}")
    return ('panel_created',
            {'panel_id': pid, 'name': name, 'fields': [f['name'] for f in fs],
             'note': f'以后直接说「记一笔{name}：…」就能记了，说错字段还能改'},
            model, '')


# ── ★ 2026-09-19 修C：已知故障求助快车道 ──
# 用户把报错原话（截图打字/粘贴）发给助手时，直接匹配已知故障给修复步骤——
# 0 token 秒回，也不会出现「问面板为什么没人用，答校准后是100000」式的答非所问。
_KNOWN_FAULTS = [
    (r'缺少\s*panel_?id|panel_?id.{0,8}(为空|缺失|缺少)|没拿到模块标识|分析功能出了个小故障',
     '这是「✨ AI 分析」按钮的一个已知小故障（按钮把模块标识传错了，9月19日这版已经修好）。\n\n'
     '【三步修好】\n'
     '1️⃣ 完全关闭哈文窗口\n'
     '2️⃣ 双击桌面「Harven AI」重新打开（重启时会自动把面板页面刷新成修好的版本）\n'
     '3️⃣ 再点「✨ AI 分析」试一次\n\n'
     '要是重启后还不行，直接在这里告诉我「重启后还是报错」，我帮你查下一步。'),
    (r'AI\s*分析.{0,12}(没成功|失败|没反应|不好用)|分析失败|请求失败',
     'AI 分析没成功，常见就两种情况：\n\n'
     '① **模型在忙**——等几秒再点一次「✨ AI 分析」；\n'
     '② **本地模型没就绪**——去「设置 → 模型」确认选好了本地模型。\n\n'
     '如果报错里有「panel_id」或「模块标识」字样，那是已知小故障，重启一下哈文就好。'
     '把具体报错发给我，我帮你判断是哪种。'),
]


def _known_fault_answer(t):
    """匹配已知故障文案 → 返回人话修复步骤；不命中返回空"""
    import re
    for pat, ans in _KNOWN_FAULTS:
        if re.search(pat, t):
            return ans
    return ''


# ★ 2026-09-26 修C/D（已批准）：记账意图判定——聊天模式不再吞记账、问号不再吞账
_RECORD_INTENT_HINTS = ['记一笔', '帮我记', '记一下', '记上', '记个', '记账', '花了', '花费', '买单', '付款',
                        '充值', '缴费', '买了', '加油', '发了工资', '收了红包', '工资', '红包']
# 查询守卫：「这个月花了多少」是查账不是记账，命中则不转记录
# ★ 2026-09-27 #884 补词：「昨天买了什么」「买过哪些」是回看查询，也不转记录
_QUERY_GUARD = ['多少', '几个', '几盒', '几瓶', '哪家', '哪种', '怎么花', '为什么花', '对账', '账单',
                '买了什么', '买了啥', '买过', '花在哪', '花过', '查查', '查一下', '找找']


def _has_record_intent(t):
    """强记账信号判定：命中记账词 + 不含查询疑问词"""
    t = (t or '').strip()
    if not t:
        return False
    if any(q in t for q in _QUERY_GUARD):
        return False
    return any(k in t for k in _RECORD_INTENT_HINTS)


def assist_chat(text, panel_id='', domain='', history=None, extra_context='', image_b64='',
                page_context='', free_chat=False):
    """对话框 2.0 统一入口（前端不做分流）。
    返回 (mode, payload, model, resolved_domain, err) ★ 2026-09-18 带回解析后的域：
      mode='reminder' → payload={reminder_id, info, describe}（提醒/日程已设好）
      mode='answer'   → payload={text}（检索档案回答，不编造；free_chat=True 时走完整引擎）
      mode='records'  → payload=records 或 groups（同 extract_records，确认卡数据源）
      mode='panel_created' → payload={panel_id,name,fields,note}（★ 对话造分类成功）

    ★ 2026-09-16 方案C（已定案）：自由对话开关——打开后所有消息转发完整引擎
      （知识库/长期记忆/画像/FC 全能力，查询模式沿用首页当前设置），零额外配置。
    """
    t = (text or '').strip()
    if not t and not image_b64:
        return '', None, '', '', '请说点什么'

    # ⓪⁻ ★ 2026-09-19 修C：已知故障求助快车道——报错原话贴过来直接给步骤，
    # 0 token 秒回；自由对话模式也先过这道（谁问故障都该得到标准答案）
    # ★ 元组必须是5元 (mode,payload,model,resolved_domain,err)——初版漏了域导致
    #   "not enough values to unpack" 500（全面检测#789 实测抓出）
    fault_ans = _known_fault_answer(t)
    if fault_ans:
        return 'answer', {'text': fault_ans, 'free': True}, '本地快车道', '', ''

    # ⓪⁼ ★ 2026-09-26 修C（已批准）：聊天模式记账信号分流——纯文本带强记账信号时
    #   先转记录抽取出确认卡，不再被聊天引擎吞掉（此前 hintChat 承诺"帮我记一笔照样能记"未兑现）
    if free_chat and not image_b64 and _has_record_intent(t):
        records, dom, model, err = extract_records(t, panel_id, domain, history=history)
        return 'records', records, model, dom, err

    # ⓪⁽ ★ 2026-09-27 #884（已批准）：浮窗助手接档案中枢——查账类问题本地毫秒级直答，0 token
    #   命中（查账意图+时间范围）→ 人话秒答；没有记录就如实说没有；
    #   强记账信号已在上面转确认卡，这里只会接到查询；
    #   只有意图没有明确时间 → 不拦，放行走完整引擎（引擎预查已注入中枢数据）
    if free_chat and not image_b64:
        try:
            from src.core.archive_hub import hub_direct_answer
            _hub_ans, _hub_res = hub_direct_answer(t)
            if _hub_ans:
                return 'answer', {'text': _hub_ans, 'free': True}, '档案中枢', '', ''
        except Exception:
            pass  # 中枢异常不挡正常对话

    # ⓪ 自由对话模式 → 完整引擎接管（带面板/页面上下文，让哈文知道"我在哪"）
    # ★ 2026-09-23：带图也走这里（聊天模式发照片问内容，引擎路由 minicpm-v 看图）
    if free_chat:
        mode, payload, model, err = _free_chat_send(t, panel_id, domain, extra_context, page_context,
                                                    image_b64=image_b64)
        return mode, payload, model, '', err

    # 带图片 → 直接走记录抽取（视觉模型转述 + 结构化）
    if image_b64:
        records, dom, model, err = extract_records(t, panel_id, domain, history=history, image_b64=image_b64)
        return 'records', records, model, dom, err

    # ① 提醒/日程意图 → 一句话设提醒（本地模型解析时间）
    if _is_reminder_intent(t):
        from src.core import home_reminder as hr
        rid, info, err = hr.create_from_text(t, module=panel_id or '', source='chat')
        if err:
            return 'reminder', None, '', '', err
        return 'reminder', {'reminder_id': rid, 'info': info,
                            'describe': hr.describe_info(info)}, '', '', ''

    # ①′ 物品存放快答：'XX在哪/找一下XX' → 直查物品存放面板位置，0 token（★ 2026-09-18）
    #   - 面板已装且能抠出物品名 → 必走快答（查不到也如实答"没记录"，绝不当成要录入）
    #   - 面板没装 → 返回 ('','')，消息照常走后面的流程
    _ans, _model = _storage_where_fast(t, panel_id)
    if _ans:
        return 'answer', {'text': _ans}, _model, '', ''

    # ①″ 食物存放快答：'牛奶在哪/鸡蛋还有吗/该买啥' → 直查冰箱台账，0 token（★ 2026-09-19 #791）
    _ans, _model = _fridge_fast(t, panel_id)
    if _ans:
        return 'answer', {'text': _ans}, _model, '', ''

    # ② 问句 → 检索本机档案回答（数据不够就说不够，绝不编造）
    #   ★ 2026-09-18 'X在哪'找东西问句也算问句（快答没拦住时，兜底走检索，不落录入）
    #   ★ 2026-09-26 修D（已批准）：带强记账信号的优先记账（"买菜花了50？"不再被问号吞掉）
    if (_is_question(t) or _is_storage_query(t)) and not _has_record_intent(t):
        ctx = _collect_answer_context(panel_id, domain, question=t)
        # ★ #1107（2026-10-11）健康问句交织点评：健康四主角面板的问答带配角近况，
        #   引用标注「面板名·摘要」；纯 SQL 取数，非健康面板零开销
        try:
            from src.core.link_rules import get_context_for_ai
            _cross = get_context_for_ai(panel_id)
            if _cross:
                ctx += ("\n\n【关联面板近况】\n" + _cross +
                        "\n（引用上面配角数据时用「面板名·摘要」小标注，没列的不编）")
        except Exception:
            pass  # 联动上下文属锦上添花，异常不挡问答
        if extra_context:
            ctx += f"\n\n【补充材料】\n{str(extra_context)[:3000]}"
        if page_context:
            # ★ 2026-09-16 面板页助手：当前页面信息（模板目录等），支撑"XX模板是干嘛的"这类问题
            ctx += f"\n\n【当前页面信息】\n{str(page_context)[:3000]}"
        if not ctx.strip():
            return 'answer', None, '', '', '这个面板还没有记录，先记几条我才能答得准'
        system = ("你是家庭档案助手。只根据下面给出的本机档案数据和页面信息回答问题；"
                  "数据里没有的信息就直说「档案里没有记录」，绝对不要编造。回答简洁、说人话。")
        prompt = f"【本机档案数据】\n{ctx}\n\n【问题】{t}"
        # ★ #891 杀回落（2026-09-27）：问答跟随当前阵营（外脑模式走外脑/本地模式走本地，不乱窜）；
        #   外脑阵营受「档案问答允许外脑」开关管，关了如实报错
        ans, model, err = _llm_call(system, prompt, max_tokens=600, temperature=0.3, gate='answer_allow_cloud')
        if err:
            return 'answer', None, '', '', err
        return 'answer', {'text': ans}, model, '', ''

    # ②′ 对话造分类：想记但没有分类 → AI 现场建一个（分类=面板定义，纯 JSON 无代码）
    if any(k in t for k in CREATE_PANEL_KW):
        mode, payload, model, err = _create_panel_from_text(t)
        return mode, payload, model, '', err

    # ③ 默认：记录抽取（确认卡流程）★ 带回解析后的域（前端入库 URL 依赖它）
    records, dom, model, err = extract_records(t, panel_id, domain, history=history)
    return 'records', records, model, dom, err


# ★ 2026-09-19 学科 AI 顾问（学习方案A）：同一份学习数据，不同学科切换不同老师视角。
# 触发条件：kid_archive 或带"科目/学科"字段的面板，且记录里出现对应学科词。
_SUBJECT_ROLES = {
    '语文': '涉及【语文】内容时以资深语文老师视角：作文点评看立意/结构/好句并指出一处最该改进的地方；阅读看答题思路是否踩到采分点。',
    '数学': '涉及【数学】内容时以数学老师视角：错题讲思路、不直接报答案；点出薄弱知识点（如分数运算/行程问题）并给针对性练习建议。',
    '英语': '涉及【英语】内容时以英语老师视角：找词汇/语法弱点，给记忆方法（词根词缀/场景造句），听说读写均衡安排。',
    '物理': '涉及【物理】内容时以物理老师视角：概念是否真懂、公式适用条件、实验与建模思维。',
    '化学': '涉及【化学】内容时以化学老师视角：方程式书写规范、实验现象记忆、推断题思路。',
    '生物': '涉及【生物】内容时以生物老师视角：概念图梳理、易混知识点对比。',
    '历史': '涉及【历史】内容时以历史老师视角：时间轴记忆、因果链梳理、材料题答法。',
    '地理': '涉及【地理】内容时以地理老师视角：地图记忆、原理结合生活实例。',
    '道法': '涉及【道法/政治】内容时以道法老师视角：答题模板、时政结合、材料分析步骤。',
    '音乐': '涉及【音乐】内容时以音乐老师视角：练习安排、乐理巩固、保护和提升兴趣。',
    '美术': '涉及【美术】内容时以美术老师视角：作品进步点、练习方向、兴趣保护。',
    '体育': '涉及【体育】内容时以体育老师视角：体能循序渐进、避免受伤。',
}


def _subject_advisor_hint(ctx):
    """ctx 里出现学科词 → 返回对应学科角色分工提示（没有就不加，不浪费 token）"""
    found = [s for s in _SUBJECT_ROLES if s in ctx]
    if not found:
        return ''
    lines = ['★ 学科顾问分工：学习记录请按学科分别归纳到【数据概览】里，分析时切换对应学科老师视角：']
    for s in found[:6]:
        lines.append(f'- {_SUBJECT_ROLES[s]}')
    return '\n'.join(lines)


def panel_ai(panel_id, question=''):
    """features.ai 按钮：面板数据 AI 分析。返回 (text, model, err)
    ★ 2026-09-19 ai_role 角色卡：panel.json 顶层 ai_role 有值时按模块角色分析（十角色矩阵），
    并把每次分析结果存 panel_ai_history（#768 做实：前端能回看上次分析）
    ★ 2026-09-27 开发者指令：废除「分析=写死本地」，跟随用户当前模型阵营（选外脑走外脑，
    没有本地模型的用户也能用分析）；外脑阵营下受「问答允许外脑」开关管，关了如实报错"""
    ctx = _collect_answer_context(panel_id)
    if len(ctx.strip()) < 30:
        return None, '', '这个面板的记录还很少，先记几条再来分析更有依据'
    pj = _panel_json(panel_id) or {}
    role = (pj.get('ai_role') or '').strip()
    if role:
        system = (f"{role}\n"
                  "根据给出的本机面板记录做分析：输出【数据概览】【发现/规律】【建议】三节，"
                  "每节2-4条，具体、可操作、不空话。记录太少就直说样本少，不要编造。")
    else:
        system = ("你是家庭数据分析师。根据给出的本机面板记录做分析："
                  "输出【数据概览】【发现/规律】【建议】三节，每节2-4条，具体、可操作、不空话。"
                  "记录太少就直说样本少，不要编造。")
    # ★ 2026-09-19 学科 AI 顾问（学习方案A）：学习类数据按学科切换老师视角
    is_learning = (panel_id == 'kid_archive') or any(
        f.get('name') in ('科目', '学科') for f in (pj.get('fields') or []))
    if is_learning:
        subj = _subject_advisor_hint(ctx)
        if subj:
            system += '\n' + subj
    # ★ 2026-10-10 #1062 B2 饮食分析定制：先本地算好确定性统计注入（AI 以此为准不许自己估）
    if panel_id == 'meal_diary':
        try:
            from src.core.meal_insights import meal_stats as _mstats
            _st = _mstats(30)
            _tk, _sp, _cal = _st['takeout'], _st['spicy'], _st['calories']
            _shops = '、'.join(f"{s['shop']}（{s['count']}次¥{s['amount']}）" for s in _tk['shops'][:3]) or '无'
            _words = '、'.join(w['word'] for w in _sp['words'][:5]) or '无'
            system += (
                f"\n这是餐饮记录分析，下面是本地已算好的确定性统计（以此为准，禁止自己再估算数字）："
                f"近30天共{_st['records']}条记录；外卖{_tk['count']}次共¥{_tk['amount']}，常点店：{_shops}；"
                f"吃辣{_sp['count']}次（命中词：{_words}）；"
                f"热量：表内估算合计{_cal['diary_sum']}千卡，另有{_cal['estimated']}条按家常份量词典粗估{_cal['dict_est']}千卡（粗估仅供参考）。"
                "\n分析重点：①钱花超没（外卖花销）②外卖频率高不高③吃辣频率④给具体可执行的饮食建议；"
                "热量只说参考量级，不做精确营养断言。")
        except Exception as _me:
            logger.debug(f"[PanelAssist] 饮食统计注入跳过: {_me}")
    prompt = f"【面板记录】\n{ctx}\n\n" + (f"用户关注：{question}" if question else "请做整体分析。")
    # ★ 2026-09-20 #801 健康板块联动：餐饮面板分析时自动带上健康体检数据 + 每日摄入汇总
    health_ctx = _meal_health_link(panel_id)
    if health_ctx:
        prompt += "\n" + health_ctx
    # ★ 2026-10-11 #1107 AI 交织点评：联动规则表取配角近况注入
    #   （链①吃→动→身体 / 链②吃→长，纯 SQL 取数零额外成本）
    try:
        from src.core.link_rules import get_context_for_ai
        _cross = get_context_for_ai(panel_id)
        if _cross:
            prompt += ("\n【关联面板近况（交织点评用）】\n" + _cross +
                       "\n点评引用上述配角数据时，在句中带「面板名·摘要」小标注"
                       "（例：餐饮·近30天外卖12次）；上面没列的内容一律不引用不编造。")
    except Exception as e:
        logger.debug(f"[PanelAssist] 交织点评注入跳过: {e}")
    # ★ 2026-09-27：跟随阵营直连（#891 原则），分析属问答类 → gate=answer_allow_cloud
    text, model, err = _llm_call(system, prompt, max_tokens=900, temperature=0.4, gate='answer_allow_cloud')
    if not err and text:
        try:
            _save_ai_result(panel_id, question, text, model)
        except Exception as e:
            logger.debug(f"[PanelAssist] 分析结果存档失败: {e}")
    return text, model, err


def _meal_health_link(panel_id):
    """★ 2026-09-20 #801 健康板块联动：餐饮类面板做 AI 分析时，
    附上近几日摄入汇总 + 健康体检面板的最近记录（体重/血压等），让点评有依据。
    非餐饮面板返回空串，零开销。"""
    try:
        from src.core.panel_manager import get_panel_manager
        pm = get_panel_manager()
        panel = pm.get_panel(panel_id)
        if not panel:
            return ''
        names = [f.get('name', '') for f in (panel.fields or [])]
        kcal_field = next((n for n in names if ('热量' in n or '千卡' in n)), '')
        is_meal = panel_id.startswith('meal') or ('餐' in (panel.name or '')) \
            or any('餐' in n or n == kcal_field for n in names)
        if not is_meal:
            return ''
        lines = ['【健康板块联动数据】']
        # 近 7 天摄入汇总（有热量字段才算）
        if kcal_field:
            try:
                rows = pm.query_panel_data(panel_id, limit=400)
                daily = {}
                for r in rows:
                    d = str(r.get('日期', '') or '')[:10]
                    if not d:
                        continue
                    try:
                        kcal = float(str(r.get(kcal_field, '') or 0).replace('千卡', '').strip() or 0)
                    except (TypeError, ValueError):
                        continue
                    daily[d] = daily.get(d, 0.0) + kcal
                if daily:
                    recent = sorted(daily.items(), reverse=True)[:7]
                    lines.append('近几日摄入汇总：' + '；'.join(f"{d}约{int(v)}千卡" for d, v in recent))
            except Exception as e:
                logger.debug(f"[PanelAssist] 摄入汇总失败: {e}")
        # 健康类面板（体检/血压/体重）最近记录
        for pid, p in list(pm._panels.items()):
            if pid == panel_id:
                continue
            pname = p.name or ''
            if not (pid.startswith('health') or '健康' in pname or '体检' in pname):
                continue
            try:
                hrows = pm.query_panel_data(pid, limit=10)
            except Exception:
                continue
            parts = []
            for r in hrows[:10]:
                slim = {k: str(r.get(k, ''))[:30] for k in ('日期', '成员', '项目', '数值') if r.get(k)}
                if slim:
                    parts.append(json.dumps(slim, ensure_ascii=False))
            if parts:
                lines.append(f"【{pname}】最近记录：{'；'.join(parts)[:600]}")
        if len(lines) == 1:
            return ''
        lines.append('点评请结合以上健康数据（如体重/血压变化趋势）给建议，只做生活方式参考，不做医疗诊断。')
        return '\n'.join(lines)
    except Exception as e:
        logger.debug(f"[PanelAssist] 健康联动上下文失败: {e}")
        return ''


def _save_ai_result(panel_id, question, text, model):
    """★ 2026-09-19 #768 分析结果落库（回看上次分析用）"""
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT INTO panel_ai_history (panel_id, question, text, model) "
                      "VALUES (?, ?, ?, ?)", (panel_id, question or '', text or '', model or ''))
            c.commit()
        finally:
            c.close()


def last_ai_result(panel_id):
    """★ 2026-09-19 #768 取某面板最近一次分析结果；无则返回 None"""
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT question, text, model, created_at FROM panel_ai_history "
                            "WHERE panel_id = ? ORDER BY id DESC LIMIT 1", (panel_id,)).fetchone()
            if not row:
                return None
            return {'question': row[0], 'text': row[1], 'model': row[2], 'created_at': row[3]}
        finally:
            c.close()


# 启动建表
try:
    init_db()
except Exception as _e:
    logger.warning(f"[PanelAssist] 初始化建表延迟: {_e}")
