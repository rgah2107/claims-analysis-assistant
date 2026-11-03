"""Drop root after the data directory is writable.

Celery warns, and keeps warning, when the worker's uid is 0. The shared upload
volume is often root-owned because Docker creates it that way. This process
starts as root, gives that directory to uid 1000, then becomes that user
before the API or the worker runs.
"""

from __future__ import annotations

import os
import pwd
import sys

DATA_DIR = "/data"
RUN_AS = "claims"


def _own(path: str, uid: int, gid: int) -> None:
    """Give one tree to the runtime user. The demo volume is small."""
    os.chown(path, uid, gid)
    for directory, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            os.chown(os.path.join(directory, name), uid, gid)


def main() -> None:
    """Exec the image command as the non-root user."""
    account = pwd.getpwnam(RUN_AS)
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.geteuid() == 0:
        _own(DATA_DIR, account.pw_uid, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
