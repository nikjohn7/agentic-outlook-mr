# Root conftest so `pytest` resolves `import src.*` regardless of invocation
# style (`.venv/bin/pytest` vs `python -m pytest`).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
