import io, sys, sqlite3, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')
from src.core.panel_manager import get_panel_manager
pm = get_panel_manager()
db = pm._conn() if hasattr(pm, '_conn') else None
# 用 panel_manager 的连接方式找数据库路径
from src.core.panel_manager import PANELS_DIR
import inspect, src.core.panel_manager as pmod
src = inspect.getsource(pmod)
import re
m = re.search(r"panel_data.*?\.db|DB_PATH\s*=\s*['\"]([^'\"]+)", src)
print('marker')
