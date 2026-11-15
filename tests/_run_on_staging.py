# -*- coding: utf-8 -*-
"""用 staging 树（r47 代码）跑同一测试文件，对照失败是否 r48 引入。"""
import sys, os
STG = r'H:\HarvenAI\_package_staging'
sys.path.insert(0, STG)
os.chdir(STG)
test_file = sys.argv[1] if len(sys.argv) > 1 else r'H:\HarvenAI\tests\test_fastlane_nav.py'
src = open(test_file, encoding='utf-8').read()
# 测试文件里的 sys.path.insert(0, H:\HarvenAI) 会让 live 抢先——删掉它保 staging 优先
src = src.replace("sys.path.insert(0, r'H:\\HarvenAI')", '')
src = src.replace('sys.exit(1)', 'print("EXITED 1")').replace('sys.exit(0)', 'print("EXITED 0")')
exec(compile(src, test_file, 'exec'))
