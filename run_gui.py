"""Start the BITS Conversion Tool desktop application."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ui.main_window import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
