#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""HTTPS simplestreams server for the refresh-investigation probe.

Runs on the test VM. Generates a self-signed CA + leaf certificate
(if not already present in the work directory), then serves the work
directory over HTTPS on 127.0.0.1.

Usage:
    serve-streams.py <work-dir> [--port PORT]

Output (stdout, line-buffered, in this order):
    READY <port> <ca-pem-path> <pid>

The probe driver waits for the READY line before installing the CA into
the system trust store and triggering an incus image fetch.

Why a CA + leaf instead of a single self-signed cert?
    The probe installs the CA into the system trust store via
    `trust anchor --store`. Trusting a CA is additive: incus continues
    to trust real-world CAs (Mozilla bundle) for legitimate image
    servers like images.linuxcontainers.org. Trusting a single
    self-signed leaf would *also* be additive in principle, but the
    `trust anchor` workflow is designed around CA certs.

The certificates have a 1-day validity. The probe is meant for
short-lived investigation sessions; if you leave it running for longer,
re-run setup to regenerate.
"""

from __future__ import annotations

import argparse
import http.server
import os
import ssl
import subprocess
import sys
from pathlib import Path

CA_VALIDITY_DAYS = 1
LEAF_VALIDITY_DAYS = 1


def generate_certs(work_dir: Path) -> tuple[Path, Path, Path]:
    """Generate CA + leaf cert in work_dir if not already present.

    Returns (ca_pem, leaf_pem, leaf_key) paths. Idempotent: if the cert
    files already exist, they are reused. To force regeneration, delete
    them before running.
    """
    ca_key = work_dir / "ca.key"
    ca_pem = work_dir / "ca.pem"
    leaf_key = work_dir / "leaf.key"
    leaf_pem = work_dir / "leaf.pem"

    if ca_pem.exists() and leaf_pem.exists() and leaf_key.exists():
        return ca_pem, leaf_pem, leaf_key

    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. CA (self-signed, used to sign the leaf and installed into the
    # system trust store on the test VM).
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-subj", "/CN=Kapsule Refresh Probe Test CA",
            "-days", str(CA_VALIDITY_DAYS),
            "-keyout", str(ca_key),
            "-out", str(ca_pem),
        ],
        check=True,
        capture_output=True,
    )

    # 2. Leaf cert signed by the CA, with SAN for 127.0.0.1 (the only
    # address the server binds to). Without the SAN, modern TLS clients
    # reject the cert even if the CN matches.
    leaf_csr = work_dir / "leaf.csr"
    subprocess.run(
        [
            "openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
            "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(leaf_key),
            "-out", str(leaf_csr),
        ],
        check=True,
        capture_output=True,
    )

    leaf_ext = work_dir / "leaf.ext"
    leaf_ext.write_text("subjectAltName=IP:127.0.0.1\n")

    subprocess.run(
        [
            "openssl", "x509", "-req",
            "-in", str(leaf_csr),
            "-CA", str(ca_pem), "-CAkey", str(ca_key), "-CAcreateserial",
            "-out", str(leaf_pem),
            "-days", str(LEAF_VALIDITY_DAYS),
            "-extfile", str(leaf_ext),
        ],
        check=True,
        capture_output=True,
    )

    # World-readable so the daemon (running as root or its own uid)
    # can read the cert. Keys remain mode 600 by openssl default.
    ca_pem.chmod(0o644)
    leaf_pem.chmod(0o644)

    return ca_pem, leaf_pem, leaf_key


def serve(work_dir: Path, port: int) -> None:
    """Serve work_dir over HTTPS on 127.0.0.1:port. Blocks forever.

    Logs every request to stderr (default for SimpleHTTPRequestHandler);
    the probe driver redirects stderr to a logfile so it can be diffed
    between phases to see exactly what incus fetched.
    """
    ca_pem, leaf_pem, leaf_key = generate_certs(work_dir)

    os.chdir(work_dir)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(leaf_pem), keyfile=str(leaf_key))

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), http.server.SimpleHTTPRequestHandler
    )
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    # Signal readiness on stdout BEFORE serve_forever blocks. The probe
    # driver reads this line to know when it is safe to install the CA
    # and trigger image operations.
    print(
        f"READY {port} {ca_pem} {os.getpid()}",
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "work_dir",
        type=Path,
        help="Directory to serve (and to write certs into).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18443,
        help="TCP port to listen on (default: 18443).",
    )
    args = parser.parse_args()

    serve(args.work_dir, args.port)


if __name__ == "__main__":
    main()
