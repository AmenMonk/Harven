import json, urllib.request, sqlite3
from pathlib import Path

def req(method, url, data=None):
    r = urllib.request.Request(url, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read().decode())

R = []
# 卸载两个重复面板（均为普通保护、0条数据）
for pid in ['daily_todo_1', 'user_0918162906']:
    j = req('DELETE', f'http://127.0.0.1:7000/api/panels/{pid}')
    R.append(f'uninstall {pid}: {j}')

# 验证1：列表数量
lst = req('GET', 'http://127.0.0.1:7000/api/panels')
ids = [p['id'] for p in lst['panels']]
R.append(f'panel-count={len(ids)} gone={("daily_todo_1" not in ids) and ("user_0918162906" not in ids)}')

# 验证2：目录已删
R.append(f'dir-daily={Path(r"H:\HarvenAI\data\panels\daily_todo_1").exists()} dir-user={Path(r"H:\HarvenAI\data\panels\user_0918162906").exists()}')

# 验证3：数据表已删
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
ts = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
conn.close()
R.append(f'table-daily-gone={"panel_daily_todo_1" not in ts} table-user-gone={"panel_user_0918162906" not in ts}')

# 验证4：family_todo / kid_archive 保留
R.append(f'family_todo-kept={"family_todo" in ids} kid_archive-kept={"kid_archive" in ids}')
print('\n'.join(R))