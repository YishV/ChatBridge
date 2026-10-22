"""Run RVC's CLI under its bundled Python while adding the repo root to sys.path."""
import runpy
import sys
from pathlib import Path

rvc_home = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(rvc_home))
script = rvc_home / 'infer' / 'cli.py'
sys.argv = [str(script), *sys.argv[2:]]
runpy.run_path(str(script), run_name='__main__')
