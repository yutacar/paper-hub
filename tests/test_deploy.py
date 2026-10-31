import subprocess

import httpx
import pytest

from paperhub.config import Settings
from paperhub.deploy import INSTALL, check_health, deploy, validate_target


def test_deploy_steps_and_protected_files(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=str(tmp_path) + "\n")
    deploy({"DEPLOY_TARGET": "127.0.0.1", "DEPLOY_DIR": "~/paper-hub"}, root=tmp_path, run=run)
    assert calls[0][0][1:] == ["-m", "pytest"]
    transfer = calls[2][0]
    assert transfer[:3] == ["rsync", "-av", "--delete"]
    for item in (".env", ".venv/", "data/", "out/", "firmware/", ".git/", "__pycache__/"):
        assert "--exclude=" + item in transfer
    assert transfer.index("--include=.env.sample") < transfer.index("--exclude=.env.*")
    assert "is-enabled --quiet" in INSTALL
    assert "--check-health" in calls[3][1]["input"]


def test_tests_fail_before_remote_mutations(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    with pytest.raises(subprocess.CalledProcessError):
        deploy({"DEPLOY_TARGET": "127.0.0.1", "DEPLOY_DIR": "release"}, root=tmp_path, run=run)
    assert len(calls) == 1


@pytest.mark.parametrize("directory", ["", "/", "~/", "~", "../release", "a/../b", "$(bad)", "a\nb"])
def test_unsafe_deploy_destination(directory):
    with pytest.raises(ValueError):
        validate_target({"DEPLOY_TARGET": "127.0.0.1", "DEPLOY_DIR": directory})


def test_health_retry_and_failures():
    settings = Settings(font_path="test", font_path_bold="test")
    statuses = iter([503, 200])
    def handler(request):
        assert request.url.path == "/healthz"
        return httpx.Response(next(statuses), json={"ok": True})
    check_health(settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(ValueError, match="healthz"):
        check_health(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False})),
                     sleep=lambda _: None)


def test_remote_prepare_rejects_home_symlink(tmp_path):
    import os
    from paperhub.deploy import PREPARE
    home = tmp_path / "home"
    home.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    result = subprocess.run(["sh", "-s", "--", str(alias)], input=PREPARE,
                            text=True, capture_output=True, env={**os.environ, "HOME": str(home)})
    assert result.returncode != 0
    result = subprocess.run(["sh", "-s", "--", "~/release"], input=PREPARE,
                            text=True, capture_output=True, env={**os.environ, "HOME": str(home)})
    assert result.returncode == 0
    assert result.stdout.strip() == str((home / "release").resolve())
