import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')
from web_app import app
app.config['TESTING'] = True
c = app.test_client()
for url in ['/spa/index.html?panel=accounting_daily_1', '/spa/index.html', '/panel/accounting_daily_1', '/panel/accounting_daily_1/index.html']:
    r = c.get(url)
    body = r.get_data(as_text=True)[:300].replace('\n', ' | ')
    print('===', url, '→', r.status_code)
    print(body)
