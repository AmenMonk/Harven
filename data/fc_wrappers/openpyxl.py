"""FC封装: openpyxl (Excel表格操作)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "create_xlsx",
        "description": "创建Excel表格。生成.xlsx文件，支持多列数据。",
        "params": {"filename": "保存路径", "headers": "列标题（逗号分隔，如：姓名,年龄,城市）", "rows": "数据行（每行用分号分隔，如：张三,25,北京;李四,30,上海）"}
    },
    {
        "name": "read_xlsx",
        "description": "读取Excel表格内容。提取.xlsx文件中的数据。",
        "params": {"filename": "文件路径"}
    }
]

def create_xlsx(filename, headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers.split(','))
    for row in rows.split(';'):
        ws.append([c.strip() for c in row.split(',')])
    os.makedirs(os.path.dirname(filename) if os.path.dirname(filename) else '.', exist_ok=True)
    wb.save(filename)
    return {"success": True, "path": os.path.abspath(filename), "rows": len(rows.split(';'))}

def read_xlsx(filename):
    from openpyxl import load_workbook
    wb = load_workbook(filename)
    ws = wb.active
    data = []
    for row in ws.iter_rows(values_only=True):
        data.append([str(c) if c is not None else '' for c in row])
    return {"success": True, "rows": len(data), "data": str(data[:100])}
