import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import requirements_review_agent as r
    print("OK", getattr(r, "AnalysisSubmission", None))
except Exception as e:
    print("IMPORT_ERROR", type(e).__name__, e)
