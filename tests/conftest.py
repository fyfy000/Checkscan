import os
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="chequescript-test-"))
os.environ["CHECKSCAN_DATA"] = str(_root)
os.environ["CHECKSCAN_IGNORE_ENV"] = "1"
