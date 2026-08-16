import markdown

with open('H:/HarvenAI/docs/HarvenAI_技术详解_v5.md', 'r', encoding='utf-8') as f:
    md_content = f.read()

html_body = markdown.markdown(md_content, extensions=['tables', 'fenced_code'])

css = """
body { font-family: 'Microsoft YaHei', sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
th { background: #f5f5f5; }
code { background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 13px; }
h1 { color: #6366f1; border-bottom: 2px solid #6366f1; padding-bottom: 10px; }
h2 { color: #4f46e5; margin-top: 30px; }
h3 { color: #7c3aed; }
"""

html = '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<title>HarvenAI 技术详解 v6.3</title>\n<style>' + css + '</style>\n</head>\n<body>\n' + html_body + '\n</body>\n</html>'

with open('C:/Users/usera/Desktop/HarvenAI_技术详解_v6.2.html', 'w', encoding='utf-8') as f:
    f.write(html)
print(f'HTML: {len(html)} bytes')
