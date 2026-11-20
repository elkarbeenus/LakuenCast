"""Tests für GitHub Actions: Fehler zusätzlich als Annotation ausgeben (sichtbar ohne Log)."""
import sys
import unittest
from pathlib import Path

suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
result = unittest.TextTestRunner(verbosity=2).run(suite)
for test, tb in result.errors + result.failures:
    msg = f"{test}\n{tb[-1500:]}".replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title=Test fehlgeschlagen::{msg}")
sys.exit(0 if result.wasSuccessful() else 1)
