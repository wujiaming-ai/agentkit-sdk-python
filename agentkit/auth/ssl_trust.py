# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""TLS trust hardening for corporate networks.

Corporate networks frequently run a TLS-intercepting proxy that injects a
self-signed CA into the certificate chain. Browsers trust it (it is installed in
the OS trust store) but Python's ``urllib`` uses its own bundle and fails with
``CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain``.

:func:`harden_default_ssl_context` makes ``urllib`` trust exactly what the OS /
browser trusts, with no third-party dependency and regardless of which Python
interpreter runs the code:

1. If :mod:`truststore` is importable, use it (routes verification through the OS
   trust store directly — the cleanest option).
2. Otherwise build a CA bundle = ``certifi`` (public roots, if present) + the
   macOS keychain certificates (where the corporate CA lives) and force the
   default HTTPS context to use it.

The function is idempotent and safe to call at import time.
"""

from __future__ import annotations

import atexit
import os
import ssl
import subprocess
import sys
import tempfile

_HARDENED = False


def harden_default_ssl_context() -> str | None:
    """Make ``ssl.create_default_context`` trust the OS/corporate CA chain.

    Returns the strategy used (``"truststore"`` / ``"keychain-bundle"`` /
    ``"certifi"``) or ``None`` if nothing could be hardened (in which case the
    stock default context remains in effect).
    """
    global _HARDENED
    if _HARDENED:
        return "already"

    # 1. truststore — uses the native OS trust store directly.
    try:
        import truststore  # type: ignore

        truststore.inject_into_ssl()
        _HARDENED = True
        return "truststore"
    except Exception:
        pass

    pems: list[bytes] = []
    try:
        import certifi

        with open(certifi.where(), "rb") as fh:
            pems.append(fh.read())
    except Exception:
        pass

    # 2. macOS keychain certificates (where MDM-pushed corporate CAs live).
    # Best-effort: only the system keychains by default — the user's login keychain can
    # hold non-CA/leaf certs, so merging it would widen the trust surface beyond what the
    # OS trusts. Opt in with AGENTKIT_SSL_INCLUDE_LOGIN_KEYCHAIN=1.
    if sys.platform == "darwin":
        keychains = [
            "/System/Library/Keychains/SystemRootCertificates.keychain",
            "/Library/Keychains/System.keychain",
        ]
        if os.getenv("AGENTKIT_SSL_INCLUDE_LOGIN_KEYCHAIN") == "1":
            keychains.append(os.path.expanduser("~/Library/Keychains/login.keychain-db"))
        for keychain in keychains:
            try:
                out = subprocess.run(
                    ["security", "find-certificate", "-a", "-p", keychain],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                if out.stdout:
                    pems.append(out.stdout.encode())
            except Exception:
                pass

    if not pems:
        return None

    fd, path = tempfile.mkstemp(suffix="-agentkit-cabundle.pem")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"\n".join(pems))
    atexit.register(lambda: os.path.exists(path) and os.unlink(path))
    ctx = ssl.create_default_context(cafile=path)
    ssl._create_default_https_context = lambda *a, **k: ctx  # type: ignore[attr-defined]
    _HARDENED = True
    return "keychain-bundle" if sys.platform == "darwin" else "certifi"
