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
    """Numatytieji nustatymai testams.

    VINTED_BROWSE_ALL ir DEAL_MODE="rank" cia isjungiami, nes dauguma testu tikrina
    atrankos logika su savo SEARCH_QUERIES ir nuolaidos ribomis. Abi veiksenos turi
    atskirus testus (tests/test_vinted_browse.py, tests/test_rank.py)."""
    with contextlib.redirect_stdout(io.StringIO()):
        config.load("__nera__.json")
    config.cfg["VINTED_BROWSE_ALL"] = False
    config.cfg["DEAL_MODE"] = "discount"     # pigiausiu budas – tests/test_rank.py
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


def listing(*args, **kwargs):
    """Vinted katalogo irasas -> bendras `Listing` (toks, koki mato visa logika)."""
    from vinted.sources.vinted_source import VintedSource
    return VintedSource(client=object()).to_listing(item(*args, **kwargs))


def listings(*items):
    from vinted.sources.vinted_source import VintedSource
    source = VintedSource(client=object())
    return [source.to_listing(i) for i in items]
