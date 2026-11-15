import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')
from web_app import app
app.config['TESTING'] = True
from src.core.panel_routes import bp
app.register_blueprint(bp)
from src.core.panel_assist_routes import bp as bpa
app.register_blueprint(bpa)
c = app.test_client()
rec = [{'日期': '2026-09-16', '项目': '今日社保', '金额': '9000', '分类': '其他', '备注': ''}]
r = c.post('/api/assist/confirm', data=json.dumps({'panel_id': 'accounting_daily_1', 'domain': '', 'records': rec}, ensure_ascii=False).encode('utf-8'), content_type='application/json; charset=utf-8')
print('status:', r.status_code)
print('resp:', r.get_data(as_text=True)[:500])
r2 = c.get('/api/panels/accounting_daily_1/data', query_string={'limit': 50})
txt = r2.get_data(as_text=True)
import re
m = [x for x in json.loads(txt).get('records', []) if '社保' in str(x)]
print('社保记录数:', len(m), json.dumps(m, ensure_ascii=False)[:300])
