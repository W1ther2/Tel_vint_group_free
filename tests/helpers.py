import contextlib
import sys
from pathlib import Path

# kad 'vinted' paketas butu randamas, nesvarbu, is kur paleisti testai
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import io
import os
import tempfile

from vinted import config


def reset_config(**overrides):
    with contextlib.redirect_stdout(io.StringIO()):
        config.load("__nera__.json")
    config.cfg.update(overrides)


class TempDir:
    """Paleidzia testa laikiname aplanke (seen.json / state.json neliecia tikru failu)."""

    def __enter__(self):
        self.old = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        return self.tmp.name

    def __exit__(self, *exc):
        os.chdir(self.old)
        self.tmp.cleanup()


def item(iid, title, price, user_id=1, status="Labai gera", **extra):
    d = {"id": iid, "title": title, "price": {"amount": str(price), "currency_code": "EUR"},
         "url": f"/items/{iid}", "user": {"id": user_id}, "status": status}
    d.update(extra)
    return d
