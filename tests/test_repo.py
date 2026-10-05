# -*- coding: utf-8 -*-
"""Kas NETURI buti repozitoriume.

Sie testai saugo ne koda, o repo tvarka. Prasme labai konkreti: iki v50 gyva
busena buvo sekama `main` sakoje. Pasekmes, ismatuotos 2026-10-05:

    1819 commitu, kuriuose yra seen.json arba state.json
    ~2 MB vien dabartineje ju versijoje

Bet dydis cia ne svarbiausia. state.json yra pardaveju ir vartotoju duomenys,
tad kiekvienas tu commitu juos amzinai ideda i VIESA git istorija. Busenos
vieta – `busena` saka, kuria check.yml perraso is naujo (`git init` laikinoje
vietoje + push --force), tad ji istorijos nekaupia.

Grąžinti tokius failus i sekamus labai lengva – pakanka vieno `git add .`
neatsargumo. Todel tai testas, o ne komentaras.
"""

import glob
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Gyva busena. Ja atsiunčia ir issaugo check.yml is `busena` sakos.
STATE_FILES = ["seen.json", "state.json"]


def find_git():
    """git.exe keliai. PATH'e jo buti nebutina.

    Windows'e GitHub Desktop turi savo git ir nededa jo i PATH, tad `git` is
    PATH'o neatsiranda. Be sios paieskos testas butu tyliai praleidziamas
    butent toje masinoje, kurioje kodas rasomas – o praleidziamas testas
    nesaugo nieko (zr. isimta AndroidConfigTest test_topic.py)."""
    found = shutil.which("git")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for path in sorted(glob.glob(os.path.join(
                local, "GitHubDesktop", "app-*", "resources", "app", "git", "cmd", "git.exe")),
                reverse=True):
            return path
    return None


def tracked_files():
    """Sekamu failu sarasas arba None, jei git tikrai nepasiekiamas (pvz. zip'e)."""
    git = find_git()
    if not git:
        return None
    try:
        out = subprocess.run([git, "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


class StateIsNotTrackedTest(unittest.TestCase):
    def setUp(self):
        self.files = tracked_files()
        if self.files is None:
            self.skipTest("git nepasiekiamas – nera ko tikrinti")

    def test_state_files_are_not_tracked(self):
        for name in STATE_FILES:
            self.assertNotIn(name, self.files,
                             f"{name} yra gyva busena – jos vieta `busena` saka, ne `main`")

    def test_archive_is_not_tracked(self):
        bad = [f for f in self.files if f.startswith("archive/")]
        self.assertEqual(bad, [], "archyvas kaupiamas `busena` sakoje")

    def test_no_backup_or_temp_files_tracked(self):
        bad = [f for f in self.files if f.endswith((".tmp", ".bak", ".pyc"))]
        self.assertEqual(bad, [])


class GitignoreTest(unittest.TestCase):
    """Atskirai nuo git – sis patikrinimas veikia visada."""

    def test_gitignore_lists_the_state_files(self):
        """Neuztenka isimti is sekamu – be .gitignore jie grįžtu su pirmu
        neatsargiu `git add .`, ir vel tyliai."""
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in STATE_FILES + ["archive/"]:
            self.assertIn(name, text, f"{name} turi buti .gitignore")


class ConfigIsCleanTest(unittest.TestCase):
    def test_no_personal_ids_in_config(self):
        """ADMIN_IDS yra asmens duomuo – jis ateina is aplinkos (GitHub Secrets).
        Zr. config._admins_from_env ir tests/test_privacy.py."""
        with open(ROOT / "config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg.get("ADMIN_IDS", []), [])

    def test_no_token_shaped_strings_in_config(self):
        """Bot token'o forma: '123456789:AA...'. Jei toks atsidurtu config.json,
        jis butu viesas visiems ir leistu raso bot'o vardu."""
        import re
        text = (ROOT / "config.json").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\d{8,10}:[A-Za-z0-9_-]{30,}", text))

    def test_every_config_key_is_a_real_setting(self):
        """Nebenaudojamas raktas config.json tyliai nieko nedaro – tai klaidina
        labiau nei jo nebuvimas (ji pakeitus niekas nepasikeicia)."""
        from vinted import config
        with open(ROOT / "config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        unknown = [k for k in cfg if k not in config.DEFAULTS]
        self.assertEqual(unknown, [], f"nezinomi raktai: {unknown}")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
