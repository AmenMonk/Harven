import urllib.request
R = []
# 1. Flask 活着
try:
    urllib.request.urlopen('http://127.0.0.1:7000/api/panels', timeout=10)
    R.append('FLASK-UP')
except Exception as e:
    R.append('FLASK-DOWN ' + str(e))
# 2. css 文件可访问
try:
    c = urllib.request.urlopen('http://127.0.0.1:7000/static/panel-assist.css', timeout=10)
    R.append('CSS-200 len=' + str(len(c.read())))
except Exception as e:
    R.append('CSS-FAIL ' + str(e))
# 3. 各面板页面是否带 css 引用（抽查 home_items + 另外两个生成品）
for pid in ['home_items', 'fridge_stock_1', 'car_maintenance_1', 'family_todo']:
    try:
        html = urllib.request.urlopen(f'http://127.0.0.1:7000/panel/{pid}/index.html', timeout=10).read().decode('utf-8')
        has_css = 'panel-assist.css' in html
        has_js = 'panel-assist.js' in html
        no_dead = 'panel-api.css' not in html
        R.append(f'{pid}: css={has_css} js={has_js} dead-link-removed={no_dead}')
    except Exception as e:
        R.append(f'{pid}: FAIL ' + str(e))
print('\n'.join(R))