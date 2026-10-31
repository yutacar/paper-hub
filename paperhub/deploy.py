"""Deployment orchestration; no source addresses are embedded in commands."""

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .config import Settings

EXCLUDES = (".git/", ".venv/", ".env", ".env.*", "data/", "out/", "__pycache__/",
            "firmware/", ".pytest_cache/", "*.egg-info/", "build/", "dist/")

PREPARE = r'''
set -eu
case "$1" in
    "~/"*) directory="$HOME/${1#\~/}" ;;
    *) directory="$1" ;;
esac
mkdir -p -- "$directory"
cd -- "$directory"
resolved=$(pwd -P)
home_directory=$(cd "$HOME" && pwd -P)
if [ "$resolved" = / ] || [ "$resolved" = "$home_directory" ]; then
    echo '転送先にルートやホーム全体は指定できません。' >&2
    exit 1
fi
printf '%s\n' "$resolved"
'''

INSTALL = r'''
set -eu
cd -- "$1"
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
if systemctl --user is-enabled --quiet paper-hub.service; then
    systemctl --user restart paper-hub.service
else
    echo 'paper-hub.service は未有効です。README の常駐化手順を実施してください。'
fi
.venv/bin/python -m paperhub.deploy --check-health
'''


def validate_target(values):
    target, directory = values.get("DEPLOY_TARGET", ""), values.get("DEPLOY_DIR", "")
    if not target or not directory:
        raise ValueError("DEPLOY_TARGET と DEPLOY_DIR を設定してください")
    if target.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:\[\]-]+", target):
        raise ValueError("DEPLOY_TARGET の形式が不正です")
    parts = directory.split("/")
    if (not re.fullmatch(r"[A-Za-z0-9_./~ -]+", directory) or
            any(part in {".", ".."} for part in parts) or
            directory.strip("/ ") in {"", "~"} or
            ("~" in directory and not directory.startswith("~/"))):
        raise ValueError("DEPLOY_DIR は専用のディレクトリを指定してください")
    return target, directory


def deploy(values, *, root, run=subprocess.run):
    target, directory = validate_target(values)
    # No remote mutation is attempted unless local tests pass.
    run([sys.executable, "-m", "pytest"], cwd=root, check=True)
    prepared = run(["ssh", target, "sh -s -- " + shlex.quote(directory)],
                   input=PREPARE, text=True, capture_output=True, check=True)
    resolved = prepared.stdout.strip()
    if not resolved.startswith("/") or "\n" in resolved or resolved == "/":
        raise ValueError("転送先の専用ディレクトリを確認できません")
    command = ["rsync", "-av", "--delete"]
    # Place include before .env.* so only the empty template is sent.
    command += ["--include=.env.sample"]
    command += ["--exclude=" + item for item in EXCLUDES]
    command += [str(root) + "/", target + ":" + shlex.quote(resolved + "/")]
    run(command, check=True)
    run(["ssh", target, "sh -s -- " + shlex.quote(resolved)], input=INSTALL,
        text=True, check=True)


def check_health(settings, *, transport=None, sleep=time.sleep):
    host = settings.host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = "[" + host + "]"
    url = f"http://{host}:{settings.port}/healthz"
    with httpx.Client(timeout=3, trust_env=False, transport=transport) as client:
        for attempt in range(10):
            try:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("ok") is True:
                    print("healthz: OK")
                    return
            except (httpx.HTTPError, ValueError):
                pass
            if attempt < 9:
                sleep(1)
    raise ValueError("healthz の確認に失敗しました。サービス状態と設定を確認してください")


def main():
    try:
        if sys.argv[1:] == ["--check-health"]:
            check_health(Settings.load())
        else:
            values = {**dotenv_values(".env", interpolate=False), **os.environ}
            deploy(values, root=Path.cwd())
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except (OSError, subprocess.CalledProcessError):
        # Avoid leaking SSH command arguments or environment-specific paths.
        raise SystemExit("デプロイに失敗しました。必須設定、テスト、転送・サービス状態を確認してください。") from None


if __name__ == "__main__":
    main()
