"""Startpunkt im AppImage."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Das eingebaute Python kennt die Zertifikate von SteamOS/Arch nicht von selbst.
if not os.environ.get("SSL_CERT_FILE"):
    for p in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
              "/etc/ssl/ca-bundle.pem", "/etc/ssl/cert.pem"):
        if os.path.isfile(p):
            os.environ["SSL_CERT_FILE"] = p
            break

from lakuencast.__main__ import main  # noqa: E402

sys.exit(main())
