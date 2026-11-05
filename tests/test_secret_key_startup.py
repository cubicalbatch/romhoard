"""Persistent secret initialization must survive concurrency and Docker startup."""

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _environment(data_dir):
    env = os.environ.copy()
    env.pop("DJANGO_SECRET_KEY", None)
    env["DATA_DIR"] = str(data_dir)
    return env


def _settings_process(
    env, code="from romhoard.settings import SECRET_KEY; print(SECRET_KEY)"
):
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_concurrent_first_start_uses_one_complete_private_key(tmp_path):
    data_dir = tmp_path / "data"
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    env = _environment(data_dir)
    env["SECRET_TEST_READY"] = str(ready_dir)
    # Force all processes to reach key generation before any publishes it.
    # This reliably reproduces the old exists()/write_text() race.
    code = """
import hashlib, os, secrets, time
from pathlib import Path
original = secrets.token_urlsafe
def synchronized_generate(size):
    ready = Path(os.environ['SECRET_TEST_READY'])
    (ready / str(os.getpid())).touch()
    deadline = time.monotonic() + 10
    while len(list(ready.iterdir())) < 6:
        if time.monotonic() > deadline:
            raise RuntimeError('Key-generation barrier timed out')
        time.sleep(0.01)
    return original(size)
secrets.token_urlsafe = synchronized_generate
from romhoard.settings import SECRET_KEY
print(hashlib.sha256(SECRET_KEY.encode()).hexdigest())
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    try:
        outputs = [process.communicate(timeout=20) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    for process, (_, error) in zip(processes, outputs):
        assert process.returncode == 0, error
    secret_file = data_dir / ".secret_key"
    digest = hashlib.sha256(secret_file.read_text().encode()).hexdigest()
    assert {output.strip() for output, _ in outputs} == {digest}
    assert len(secret_file.read_text()) >= 50
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert list(data_dir.iterdir()) == [secret_file]


def test_existing_secret_is_preserved(tmp_path):
    secret_file = tmp_path / ".secret_key"
    secret_file.write_text("existing-installation-key\n")
    result = _settings_process(_environment(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "existing-installation-key"
    assert secret_file.read_text() == "existing-installation-key\n"


def test_environment_secret_does_not_touch_persisted_key(tmp_path):
    secret_file = tmp_path / ".secret_key"
    secret_file.write_text("")
    env = _environment(tmp_path)
    env["DJANGO_SECRET_KEY"] = "explicit-environment-key"
    result = _settings_process(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "explicit-environment-key"
    assert secret_file.read_text() == ""


def test_empty_secret_fails_without_rotating_it(tmp_path):
    secret_file = tmp_path / ".secret_key"
    secret_file.write_text(" \n")
    result = _settings_process(_environment(tmp_path))
    assert result.returncode != 0
    assert "is empty" in result.stderr
    assert secret_file.read_text() == " \n"


def test_unwritable_data_path_fails_instead_of_using_ephemeral_key(tmp_path):
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("occupied")
    result = _settings_process(_environment(not_a_directory))
    assert result.returncode != 0
    assert "Set DJANGO_SECRET_KEY" in result.stderr
    assert not_a_directory.read_text() == "occupied"


@pytest.mark.parametrize("existing", [False, True])
def test_bootstrap_preserves_key_and_prepares_only_target_permissions(
    tmp_path, existing
):
    data_dir = tmp_path / "custom-data"
    data_dir.mkdir()
    secret_file = data_dir / ".secret_key"
    if existing:
        secret_file.write_text("existing-installation-key")
        secret_file.chmod(0o644)
    metadata = data_dir / "metadata"
    metadata.mkdir(mode=0o750)
    image = metadata / "image.png"
    image.write_bytes(b"unchanged")
    image.chmod(0o640)
    # Exercise the root bootstrap branch without requiring root or a
    # romhoard account on the test host. Real chown is limited to our UID.
    code = """
import os, pwd
from types import SimpleNamespace
def runtime_user(name):
    assert name == 'romhoard'
    return SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
pwd.getpwnam = runtime_user
os.geteuid = lambda: 0
from romhoard.bootstrap import prepare_runtime_secret
prepare_runtime_secret()
"""
    result = _settings_process(_environment(data_dir), code)
    assert result.returncode == 0, result.stderr
    assert secret_file.read_text()
    if existing:
        assert secret_file.read_text() == "existing-installation-key"
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert secret_file.stat().st_uid == os.getuid()
    assert stat.S_IMODE(metadata.stat().st_mode) == 0o750
    assert stat.S_IMODE(image.stat().st_mode) == 0o640
    assert image.read_bytes() == b"unchanged"


def test_bootstrap_environment_key_preserves_disk_key_and_mode(tmp_path):
    secret_file = tmp_path / ".secret_key"
    secret_file.write_text("original-disk-key")
    secret_file.chmod(0o640)
    env = _environment(tmp_path)
    env["DJANGO_SECRET_KEY"] = "explicit-environment-key"
    code = """
import os, pwd
from types import SimpleNamespace
pwd.getpwnam = lambda name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
os.geteuid = lambda: 0
from romhoard.bootstrap import prepare_runtime_secret
prepare_runtime_secret()
"""
    result = _settings_process(env, code)
    assert result.returncode == 0, result.stderr
    assert secret_file.read_text() == "original-disk-key"
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o640


def test_bootstrap_environment_key_does_not_require_data_directory(tmp_path):
    occupied = tmp_path / "file"
    occupied.write_text("unchanged")
    env = _environment(occupied)
    env["DJANGO_SECRET_KEY"] = "explicit-environment-key"
    result = _settings_process(
        env,
        """
import os
os.geteuid = lambda: 0
from romhoard.bootstrap import prepare_runtime_secret
prepare_runtime_secret()
""",
    )
    assert result.returncode == 0, result.stderr
    assert occupied.read_text() == "unchanged"


@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_existing_key_on_read_only_storage_survives_bootstrap(tmp_path, mode):
    key = tmp_path / ".secret_key"
    key.write_text("existing-installation-key")
    key.chmod(mode)
    code = """
import errno, os, pwd
from pathlib import Path
from types import SimpleNamespace
pwd.getpwnam = lambda name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
os.geteuid = lambda: 0
def read_only(*args, **kwargs):
    raise OSError(errno.EROFS, 'Read-only filesystem')
os.chown = read_only
Path.chmod = read_only
from romhoard.bootstrap import prepare_runtime_secret
prepare_runtime_secret()
from romhoard.settings import SECRET_KEY
assert SECRET_KEY == 'existing-installation-key'
"""
    result = _settings_process(_environment(tmp_path), code)
    assert result.returncode == 0, result.stderr
    assert key.read_text() == "existing-installation-key"
    assert stat.S_IMODE(key.stat().st_mode) == mode


def test_new_key_requires_working_ownership_preparation(tmp_path):
    code = """
import os, pwd
from types import SimpleNamespace
pwd.getpwnam = lambda name: SimpleNamespace(pw_uid=os.getuid() + 1, pw_gid=os.getgid())
os.geteuid = lambda: 0
def denied(*args, **kwargs):
    raise PermissionError('Ownership change denied')
os.chown = denied
from romhoard.bootstrap import prepare_runtime_secret
prepare_runtime_secret()
"""
    result = _settings_process(_environment(tmp_path), code)
    assert result.returncode != 0
    assert "Ownership change denied" in result.stderr


@pytest.mark.parametrize(
    "mode", ["all", "web", "worker", "migrate", "shell", "collectstatic"]
)
def test_entrypoint_prepares_secret_before_application_commands(tmp_path, mode):
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "commands.log"
    for name in ["python", "gunicorn", "supervisord"]:
        executable = commands / name
        executable.write_text(
            '#!/bin/sh\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
        )
        executable.chmod(0o755)
    env = _environment(tmp_path / "unused-data")
    env["PATH"] = f"{commands}:{env['PATH']}"
    env["COMMAND_LOG"] = str(log)
    result = subprocess.run(
        ["bash", str(ROOT / "docker/docker-entrypoint.sh"), mode],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[0] == "python -m romhoard.bootstrap"
