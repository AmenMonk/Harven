import re
import sys
import os

# 强制 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')

# 动态计算项目根目录（无论脚本从哪里运行都能找到文件）
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_root, 'src', 'core', 'morality.py'), 'r', encoding='utf-8') as f:
    content = f.read()

start_marker = 'SYSTEM_PROMPT = """'
start = content.find(start_marker)
if start == -1:
    print('未找到 SYSTEM_PROMPT')
else:
    rest = content[start + len(start_marker):]
    end = rest.find('"""')
    if end == -1:
        print('未找到结尾')
    else:
        prompt = rest[:end]
        chars_with_space = len(prompt)
        chars_no_space = len(prompt.replace(' ', '').replace('\n', '').replace('\r', ''))
        lines = prompt.count('\n') + 1
        print(f'行数: {lines}')
        print(f'字符数（含空格/换行）: {chars_with_space}')
        print(f'字符数（不含空格）: {chars_no_space}')
        est_tokens = int(chars_no_space / 1.8)
        print(f'估算 token 数: ~{est_tokens}')
        print()
        print('--- 各行字数和内容预览 ---')
        for i, line in enumerate(prompt.split('\n'), 1):
            if line.strip():
                print(f'  L{i:3d} ({len(line.strip())}字): {line.strip()[:60]}')
