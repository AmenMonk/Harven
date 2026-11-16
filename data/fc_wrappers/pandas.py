"""FC封装: pandas (数据分析)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "analyze_csv",
        "description": "分析CSV文件。读取CSV数据，计算统计信息（总和/平均/最大/最小）。",
        "params": {"file_path": "CSV文件路径", "column": "要分析的列名（留空分析所有数值列）"}
    }
]

def analyze_csv(file_path, column=''):
    try:
        import pandas as pd
        df = pd.read_csv(file_path)
        result = {"success": True, "rows": len(df), "columns": list(df.columns)}
        if column:
            if column in df.columns:
                col = df[column]
                if pd.api.types.is_numeric_dtype(col):
                    result["stats"] = {
                        "sum": float(col.sum()),
                        "mean": float(col.mean()),
                        "max": float(col.max()),
                        "min": float(col.min()),
                    }
                else:
                    result["unique_values"] = int(col.nunique())
                    result["top_values"] = col.value_counts().head(5).to_dict()
            else:
                result["error"] = f"列 '{column}' 不存在，可用列: {list(df.columns)}"
        else:
            numeric_cols = df.select_dtypes(include=['number']).columns
            if len(numeric_cols) > 0:
                result["stats"] = df[numeric_cols].describe().to_dict()
        return result
    except ImportError:
        return {"success": False, "error": "pandas未安装"}
    except Exception as e:
        return {"success": False, "error": f"分析失败: {e}"}
