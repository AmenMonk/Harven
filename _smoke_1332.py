# -*- coding: utf-8 -*-
"""#1332 冒烟：提取实例 JS 用 node 验证 + 模板一致性检查（用完即删）"""
import re

src = open('H:/HarvenAI/data/panels/fridge_stock_1/index.html', encoding='utf-8').read()
m = re.search(r'function fCheckNewStock.*?\n\}', src, re.S)
assert m, '实例里找不到 fCheckNewStock'

js = ("var document={getElementById:function(){return {style:{},textContent:''}}},"
      "fetch:function(){return Promise.resolve({json:function(){return Promise.resolve({})}})};\n"
      + m.group(0) + "\nfCheckNewStock();")
open('C:/Users/usera/AppData/Local/Temp/_chk_f1332.js', 'w', encoding='utf-8').write(js)

tpl = open('H:/HarvenAI/src/core/panel_manager.py', encoding='utf-8').read()
print('模板含fCheckNewStock:', 'fCheckNewStock' in tpl, '| 模板含联动条:', 'fNewStockBar' in tpl)
print('实例与模板函数出现次数一致:', tpl.count('fCheckNewStock') == src.count('fCheckNewStock'))
