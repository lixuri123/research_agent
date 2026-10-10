"""Offline first-stage regression: execute meaningful tests, no external model calls."""
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', str(root / 'tests'), '-q'], cwd=root))
