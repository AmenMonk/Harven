import os

SKILLS_DIR = 'H:/HarvenAI/data/skills'
print("=== HarvenAI Skills ===\n")

for item in sorted(os.listdir(SKILLS_DIR)):
    full = os.path.join(SKILLS_DIR, item)
    if os.path.isdir(full):
        has_script = os.path.exists(os.path.join(full, 'scripts', 'main.py'))
        has_skill_md = os.path.exists(os.path.join(full, 'SKILL.md'))
        if has_script:
            print(f"  ✅ {item} (可执行脚本)")
            # 读触发词
            skill_md = os.path.join(full, 'SKILL.md')
            if has_skill_md:
                with open(skill_md, 'r', encoding='utf-8') as f:
                    content = f.read(500)
                    if 'trigger:' in content:
                        idx = content.index('trigger:')
                        trigger_line = content[idx:idx+100].split('\n')[0]
                        print(f"     触发词: {trigger_line}")
        elif has_skill_md:
            print(f"  📝 {item} (纯提示词)")
        else:
            print(f"  ❓ {item}")
    elif item.endswith('.md'):
        print(f"  📝 {item} (纯提示词)")

# WorkBuddy的skills
print("\n=== WorkBuddy Skills（对比）===")
wb_skills = [
    "Deep Research (深度调研)",
    "tencent-pptx (PPT生成)",
    "skill-creator (创建Skill)",
    "expert-manager (专家包管理)",
    "cloudstudio-deploy (部署)",
]
for s in wb_skills:
    print(f"  🔄 {s}")
