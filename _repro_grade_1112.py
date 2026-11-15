# -*- coding: utf-8 -*-
"""复现今晚批改超时：真作业图 + 真 t2 prompt 调 zhipu glm-5.3-Flash，量真实耗时。"""
import sys, io, json, time, base64, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import yaml

# 1) 图：今晚失败的那张 id=215（4457KB 手机原图）
src = r'H:\HarvenAI\data\media\img_store\202611\e6d0f8e47be44b20afc0a37dbc6b9161.jpg'
from PIL import Image
im = Image.open(src).convert('RGB')
w, h = im.size
print(f'原图: {w}x{h} {len(open(src,"rb").read())//1024}KB')
if max(w, h) > 2048:
    sc = 2048 / float(max(w, h))
    im = im.resize((max(1, int(w*sc)), max(1, int(h*sc))), Image.LANCZOS)
buf = __import__('io').BytesIO()
im.save(buf, 'JPEG', quality=90)
b64 = base64.b64encode(buf.getvalue()).decode()
print(f'归一化后: {im.size[0]}x{im.size[1]} b64={len(b64)//1024}KB')

# 2) 真 t2 prompt（study_coach.py:365-370 原文，单图无标准答案）
t2 = ("这是学生的作业页。学科：自动。\n"
      '标准答案：（无标准答案图，请按题目常识判断，并把不确定的项 uncertain 设为 true）\n'
      "逐题对照批改，只输出 JSON 数组，格式："
      '[{"no":"题号","question":"题目摘要(20字内)","student_answer":"学生答案",'
      '"correct_answer":"正确答案","wrong":true/false,"kp":"考察知识点","uncertain":true/false}]。'
      "字迹看不清的题 uncertain=true 且不要判错。不要其他文字。"
      "\n\n【学科判断】上面写的学科是「自动」——请根据作业内容判断真实学科（语文/数学/英语/物理/化学等），"
      "按上述格式输出批改 JSON 数组后，另起一行单独输出：@@科目:学科名（如 @@科目:语文）")

cfg = yaml.safe_load(open(r'H:\HarvenAI\config.yaml', encoding='utf-8')) or {}
key = (cfg.get('model_keys') or {}).get('zhipu', '')

for model in ['glm-5.3-Flash', 'glm-4v-flash']:
    body = {'model': model, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': t2},
        {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}]}], 'stream': False}
    req = urllib.request.Request('https://open.bigmodel.cn/api/paas/v4/chat/completions',
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.loads(r.read())
        dt = time.time() - t0
        content = out['choices'][0]['message']['content']
        n_items = content.count('"no"')
        print(f'[{model}] OK {dt:.1f}s 批出约{n_items}题, 回复{len(content)}字 -> {content[:80]}...')
    except Exception as e:
        print(f'[{model}] FAIL {time.time()-t0:.1f}s -> {type(e).__name__}: {str(e)[:100]}')
