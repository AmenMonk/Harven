"""FC封装: python-pptx (PowerPoint生成)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "create_pptx",
        "description": "创建PowerPoint演示文稿。生成.pptx文件，支持多页幻灯片。",
        "params": {"filename": "保存路径", "title": "标题幻灯片文字", "slides": "幻灯片内容（每页用---分隔，每页第一行是标题）"}
    }
]

def create_pptx(filename, title, slides):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    # 标题页
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    # 内容页
    for slide_content in slides.split('---'):
        slide_content = slide_content.strip()
        if not slide_content:
            continue
        lines = slide_content.split('\n')
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = lines[0].strip()
        body = '\n'.join(lines[1:]).strip()
        if body:
            slide.placeholders[1].text = body
    os.makedirs(os.path.dirname(filename) if os.path.dirname(filename) else '.', exist_ok=True)
    prs.save(filename)
    return {"success": True, "path": os.path.abspath(filename), "slides": len(slides.split('---')) + 1}
