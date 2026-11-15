/* panel-assist.js tryAssistNav 验证用例（无需浏览器，stub window/localStorage/fetch） */
const fs = require('fs');
global.window = {};
global.localStorage = { getItem: () => null, setItem: () => {} };
global.fetch = () => Promise.resolve({ json: () => Promise.resolve({ panels: [
  { id: 'fridge_stock_1', name: '食物库存' },
  { id: 'accounting_daily_1', name: '记账账本' },
  { id: 'kid_archive', name: '孩子成长档案' },
  { id: 'home_calendar', name: '家庭日历' },
]})});
eval(fs.readFileSync('H:/HarvenAI/static/panel-assist.js', 'utf8'));
const nav = global.window.PanelAssist.__tryNav;

const CASES = [
  // [输入, 期望path]（null=不跳，走聊天管线）
  ['打开家庭生活', '/space/family'],
  ['打开家庭', '/space/family'],
  ['打开面板', '/panels'],
  ['打开面板管理', '/panels'],
  ['去知识库', '/knowledge'],
  ['进入成长档案', '/panel/kid_archive'],
  ['打开冰箱', '/panel/fridge_stock_1'],
  ['打开记账', '/panel/accounting_daily_1'],
  ['打开设置', '/settings'],
  ['打开家庭生活吧', '/space/family'],
  // —— 不该跳的 ——
  ['帮我看看家庭面板里都有啥', null],
  ['牛奶还有几盒？', null],
  ['家庭面板里有什么', null],
  ['打开D盘', null],
  ['打开思路文档', null],
  ['帮我记一笔买菜花了86块', null],
  ['今天天气怎么样', null],
];

(async () => {
  let pass = 0, fail = 0;
  for (const [text, expect] of CASES) {
    let got = null;
    try { const r = await nav(text); got = r ? r.path : null; } catch (e) { got = 'ERR:' + e.message; }
    if (got === expect) { pass++; console.log('PASS  ' + text + ' → ' + got); }
    else { fail++; console.log('FAIL  ' + text + ' → 期望 ' + expect + ' 实得 ' + got); }
  }
  console.log('\n结果: ' + pass + '/' + CASES.length + ' 通过');
  process.exit(fail ? 1 : 0);
})();
