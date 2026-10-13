"""Run backend checks without loading local credentials or production databases."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch


def main() -> int:
    backend = Path(__file__).resolve().parents[1] / "backend"
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    # Clearing the environment also isolates optional external integrations.
    environment = {
        "DATABASE_URL": "sqlite:///:memory:",
        "JWT_SECRET": "isolated-test-secret-never-use-in-production",
        "APP_ENV": "test",
        "PYTHON_DOTENV_DISABLED": "1",
    }
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"):
        if name in os.environ:
            environment[name] = os.environ[name]
    # Windows sandbox TEMP can be readable but not writable by child Python.
    # Keep disposable test fixtures in the explicitly writable workspace.
    test_temp = backend.parent / '.tmp' / 'backend-check-temp'
    test_temp.mkdir(parents=True, exist_ok=True)
    environment.update(TEMP=str(test_temp), TMP=str(test_temp))
    with patch.dict(os.environ, environment, clear=True), patch("dotenv.load_dotenv"):
        suite = unittest.defaultTestLoader.discover(str(backend / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
