"""Rutas e importacion de los scripts (algunos empiezan con numero y no se pueden importar normal)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    """Importa scripts/<name>.py aunque el nombre empiece con un numero."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"script_{name.replace('-', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
