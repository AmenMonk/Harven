import os

skills_dir = 'H:/HarvenAI/data/skills'
print('=== Skill系统检查 ===')

script_skills = []
prompt_skills = []
for item in sorted(os.listdir(skills_dir)):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full):
        has_script = os.path.exists(os.path.join(full, 'scripts', 'main.py'))
        has_skill_md = os.path.exists(os.path.join(full, 'SKILL.md'))
        if has_script:
            trigger = ''
            if has_skill_md:
                with open(os.path.join(full, 'SKILL.md'), 'r', encoding='utf-8') as f:
                    content = f.read(500)
                    if 'trigger:' in content:
                        idx = content.index('trigger:')
                        trigger = content[idx:].split('\n')[0][:80]
            script_skills.append(f'  OK {item} ({trigger})')
        elif has_skill_md:
            prompt_skills.append(f'  TEXT {item}')
    elif item.endswith('.md'):
        prompt_skills.append(f'  TEXT {item}')

print('可执行脚本Skill:')
for s in script_skills:
    print(s)

print('\n纯提示词Skill:')
for s in prompt_skills:
    print(s)

helpers_path = os.path.join(skills_dir, '_helpers.py')
if os.path.exists(helpers_path):
    print('\nOK _helpers.py exists')
    with open(helpers_path, 'r', encoding='utf-8') as f:
        content = f.read()
    funcs = [line.split('def ')[1].split('(')[0] for line in content.split('\n') if line.startswith('def ')]
    print(f'   Functions: {funcs}')
else:
    print('\nMISSING _helpers.py')

runner_path = 'H:/HarvenAI/src/core/scripts_runner.py'
if os.path.exists(runner_path):
    with open(runner_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if 'timeout: int = 300' in content:
        print('\nOK sandbox timeout: 300s')
    else:
        print('\nWARNING sandbox timeout: 30s')

kb_path = 'H:/HarvenAI/src/core/knowledge_base.py'
if os.path.exists(kb_path):
    with open(kb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if '.md' in content:
        print('\nOK knowledge base supports .md')
    if 'watchdog' in content.lower() or 'observer' in content.lower():
        print('OK has directory monitoring')
    else:
        print('MISSING no directory monitoring (needed for Obsidian)')

report_dir = 'H:/HarvenAI/data/reports'
if os.path.exists(report_dir):
    reports = os.listdir(report_dir)
    print(f'\nOK reports dir: {len(reports)} reports')
    for r in reports[:5]:
        print(f'   {r}')
else:
    print('\nMISSING reports dir')
