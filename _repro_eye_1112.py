# -*- coding: utf-8 -*-
"""实测 Eye 本地通道批改耗时：同一张作业图 + t2 prompt 走 eye_client.chat（模拟 _eye_vision_call_raw）。"""
import sys, io, time, base64
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'H:\HarvenAI')

from PIL import Image
src = r'H:\HarvenAI\data\media\img_store\202611\e6d0f8e47be44b20afc0a37dbc6b9161.jpg'
im = Image.open(src).convert('RGB')
w, h = im.size
if max(w, h) > 2048:
    sc = 2048 / float(max(w, h))
    im = im.resize((max(1, int(w*sc)), max(1, int(h*sc))), Image.LANCZOS)
buf = io.BytesIO()
im.save(buf, 'JPEG', quality=90)
b64 = base64.b64encode(buf.getvalue()).decode()
print(f'图: {im.size[0]}x{im.size[1]} b64={len(b64)//1024}KB')

t2 = ("这是学生的作业页。学科：自动。\n"
      '标准答案：（无标准答案图，请按题目常识判断，并把不确定的项 uncertain 设为 true）\n'
      "逐题对照批改，只输出 JSON 数组，格式："
      '[{"no":"题号","question":"题目摘要(20字内)","student_answer":"学生答案",'
      '"correct_answer":"正确答案","wrong":true/false,"kp":"考察知识点","uncertain":true/false}]。'
      "字迹看不清的题 uncertain=true 且不要判错。不要其他文字。"
      "\n\n【学科判断】请判断真实学科，输出批改 JSON 数组后另起一行：@@科目:学科名")

from src.core import eye_client
print('Eye 就绪:', eye_client.models_ready())
msgs = [{'role': 'user', 'content': t2, 'images': [b64]}]
t0 = time.time()
ok, ans = eye_client.chat(msgs, timeout=300.0)
dt = time.time() - t0
if ok:
    print(f'[Eye 本地] OK {dt:.1f}s 回复{len(ans)}字 -> {ans[:120]}...')
else:
    print(f'[Eye 本地] FAIL {dt:.1f}s -> {ans[:150]}')
