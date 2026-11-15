import io, sys, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
conn.execute("DELETE FROM panel_accounting_daily_1 WHERE 项目 LIKE '%水果%'")
conn.commit(); conn.close()
print('CLEANED')
