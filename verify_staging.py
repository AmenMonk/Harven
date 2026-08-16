import os

dst = 'H:/HarvenAI/_package_staging'
checks = {
    'web_app.py': os.path.exists(dst+'/web_app.py'),
    'engine.py': os.path.exists(dst+'/src/core/engine.py'),
    'scripts_runner.py': os.path.exists(dst+'/src/core/scripts_runner.py'),
    'task_scheduler.py': os.path.exists(dst+'/src/core/task_scheduler.py'),
    'airllm_integration.py': os.path.exists(dst+'/src/core/airllm_integration.py'),
    '_helpers.py': os.path.exists(dst+'/data/skills/_helpers.py'),
    'song_production': os.path.exists(dst+'/data/skills/song_production/scripts/main.py'),
    'tab-流程编辑器': os.path.exists(dst+'/templates/includes/tab-流程编辑器.html'),
    'tab-高级功能': os.path.exists(dst+'/templates/includes/tab-高级功能.html'),
    'README.md': os.path.exists(dst+'/README.md'),
    'CONTRIBUTING.md': os.path.exists(dst+'/CONTRIBUTING.md'),
    '.gitignore': os.path.exists(dst+'/.gitignore'),
    'config.example.yaml': os.path.exists(dst+'/config.example.yaml'),
    '技术详解v7': os.path.exists(dst+'/docs/HarvenAI_技术详解_v7.md'),
    '安装详解v2.0': os.path.exists(dst+'/HarvenAI_安装详解_v2.0.md'),
    'VERSION': os.path.exists(dst+'/VERSION'),
}
ok = sum(checks.values())
for name, exists in checks.items():
    print(('  OK ' if exists else '  MISSING ') + name)
print()
print('Result: %d/%d OK' % (ok, len(checks)))

skills_dir = dst + '/data/skills'
skills = [d for d in os.listdir(skills_dir) if os.path.isdir(os.path.join(skills_dir, d)) and not d.startswith('_')]
print('Skills: %d' % len(skills))
for s in sorted(skills):
    has_script = os.path.exists(os.path.join(skills_dir, s, 'scripts/main.py'))
    print('  %s [%s]' % (s, 'script' if has_script else 'prompt'))
