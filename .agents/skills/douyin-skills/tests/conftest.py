"""测试引导：把 scripts/ 放进 sys.path，让测试可以直接 `import douyin.*`。

与运行方式保持一致 —— `python scripts/cli.py` 时 Python 会把 scripts/ 自动加入
sys.path，测试里手动补上同一件事即可。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
