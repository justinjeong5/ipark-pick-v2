"""Single-instance file lock — prevents two morning/winners runs from racing.

Uses POSIX fcntl. If another process holds the lock, the second invocation
exits cleanly (BlockingIOError → exit 75 = `EX_TEMPFAIL`). Lock files live
under data/state/ and are released automatically when the process exits.
"""
from __future__ import annotations

import errno
import fcntl
from collections.abc import Iterator
from contextlib import contextmanager

from .config import STATE_DIR

LOCK_DIR = STATE_DIR


class AlreadyRunningError(RuntimeError):
    pass


@contextmanager
def single_instance(name: str) -> Iterator[None]:
    """Acquire an exclusive lock named `name`. Raise if another holder exists."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{name}.lock"
    with open(lock_path, "w") as fp:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise AlreadyRunningError(
                    f"another '{name}' run is already in progress (lock={lock_path})"
                ) from exc
            raise
        try:
            yield
        finally:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
