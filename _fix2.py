import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')
from src.core.panel_manager import get_panel_manager
pm = get_panel_manager()
# id=67 补录的社保：分类→缴费，补元数据
pm.update_panel_data('accounting_daily_1', 67, {'分类': '缴费', 'kind': 'flow', '方向': '支出', '来源': '哈文助手'})
# id=64 早餐：买菜→餐饮
pm.update_panel_data('accounting_daily_1', 64, {'分类': '餐饮'})
# id=19 老数据：补 kind/方向
pm.update_panel_data('accounting_daily_1', 19, {'kind': 'flow', '方向': '支出'})
for pid in [19, 63, 64, 66, 67]:
    pass
recs = pm.get_panel_data('accounting_daily_1', limit=50) if hasattr(pm, 'get_panel_data') else None
print('UPDATED')
