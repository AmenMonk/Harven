"""FC封装: python-docx (Word文档操作)"""
TOOL_DEFINITIONS = [
    {
        "name": "create_docx",
        "description": "创建Word文档。生成.docx文件，包含标题和正文内容。",
        "params": {"filename": "保存路径（如output/报告.docx）", "title": "文档标题", "content": "正文内容（支持\\n换行）"}
    },
    {
        "name": "read_docx",
        "description": "读取Word文档内容。提取.docx文件中的所有文字。",
        "params": {"filename": "文件路径"}
    }
]

def create_docx(filename, title, content):
    from docx import Document
    doc = Document()
    doc.add_heading(title, 0)
    for para in content.split('\n'):
        if para.strip():
            doc.add_paragraph(para)
    os.makedirs(os.path.dirname(filename) if os.path.dirname(filename) else '.', exist_ok=True)
    doc.save(filename)
    return {"success": True, "path": os.path.abspath(filename), "message": f"Word文档已创建: {filename}"}

def read_docx(filename):
    from docx import Document
    doc = Document(filename)
    text = '\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
    return {"success": True, "content": text[:5000], "paragraphs": len(doc.paragraphs)}

import os
