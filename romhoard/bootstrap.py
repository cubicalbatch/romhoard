"""Prepare persistent state before Docker starts application processes."""

import errno
import os
import pwd
from pathlib import Path


def prepare_runtime_secret() -> None:
    """Keep one private key readable by the supervised application user.

    Docker's entrypoint and migrations run as root, while supervised web
    and worker processes run as romhoard. Only the data directory and key
    need ownership preparation; metadata and ROM trees are left alone.

    An installation whose data volume is mounted read-only keeps the key it
    already has: ownership preparation is impossible there, but the key was
    provisioned together with that mount. A key created on this startup must
    still be handed to the runtime user, so its preparation failure aborts.
    """
    data_dir = Path(
        os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data")
    )
    secret_file = data_dir / ".secret_key"
    key_existed = secret_file.is_file()

    from .settings import _get_secret_key

    _get_secret_key()
    if os.environ.get("DJANGO_SECRET_KEY") or os.geteuid() != 0:
        return

    user = pwd.getpwnam("romhoard")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        os.chown(data_dir, user.pw_uid, user.pw_gid)
        os.chown(secret_file, user.pw_uid, user.pw_gid)
        secret_file.chmod(0o600)
    except OSError as exc:
        if key_existed and exc.errno == errno.EROFS:
            return
        raise


if __name__ == "__main__":
    prepare_runtime_secret()
