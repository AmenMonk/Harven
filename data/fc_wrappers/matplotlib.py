"""FC封装: matplotlib (图表生成)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "create_chart",
        "description": "生成图表并保存为图片。支持折线图/柱状图/饼图。",
        "params": {"chart_type": "图表类型（line/bar/pie）", "filename": "保存路径（如output/chart.png）", "title": "图表标题", "labels": "标签（逗号分隔，如：1月,2月,3月）", "values": "数值（逗号分隔，如：100,200,150）"}
    }
]

def create_chart(chart_type, filename, title, labels, values):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    label_list = [l.strip() for l in labels.split(',')]
    value_list = [float(v.strip()) for v in values.split(',')]
    fig, ax = plt.subplots(figsize=(8, 5))
    if chart_type == 'line':
        ax.plot(label_list, value_list, marker='o')
    elif chart_type == 'bar':
        ax.bar(label_list, value_list)
    elif chart_type == 'pie':
        ax.pie(value_list, labels=label_list, autopct='%1.1f%%')
    else:
        ax.plot(label_list, value_list)
    ax.set_title(title)
    if chart_type != 'pie':
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
    plt.tight_layout()
    os.makedirs(os.path.dirname(filename) if os.path.dirname(filename) else '.', exist_ok=True)
    plt.savefig(filename, dpi=150)
    plt.close()
    return {"success": True, "path": os.path.abspath(filename), "type": chart_type}
