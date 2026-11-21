"""Version von LakuenCast und Versionsvergleich für den Updater.

VERSION wird für Releases hier gepflegt. Testbuilds: "0.2.0-test.1".
"""

import re

VERSION = "0.2.1"
REPO = "elkarbeenus/LakuenCast"

_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?test[.-]?(\d+))?", re.I)


def parse(text: str):
    m = _RE.match(str(text or "").strip())
    if not m:
        return None
    test = float("inf") if m.group(4) is None else int(m.group(4))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), test)


def is_newer(remote: str, installed: str) -> bool:
    a, b = parse(remote), parse(installed)
    return bool(a and b and a > b)


def display(text: str = VERSION) -> str:
    v = parse(text)
    if not v:
        return str(text)
    base = f"v{v[0]}.{v[1]}.{v[2]}"
    return base if v[3] == float("inf") else f"{base} test {v[3]}"
