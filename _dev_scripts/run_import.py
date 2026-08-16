"""Run batch import and save results to file"""
import sys, os
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from batch_import_knowledge import main
import io
from contextlib import redirect_stdout

result_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_result.txt")
with open(result_file, "w", encoding="utf-8") as f:
    with redirect_stdout(f):
        main()
print(f"Done. Results saved to {result_file}")
