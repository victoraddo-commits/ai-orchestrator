import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MEDIA_DATA_DIR", "/tmp/kai-media-tests")
os.environ.setdefault("MEDIA_ENABLED", "true")
