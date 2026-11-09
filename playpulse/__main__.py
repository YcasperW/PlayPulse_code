"""让 `python -m playpulse` 直接可用。"""
from .cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())
