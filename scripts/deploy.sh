#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")/.."
if [ ! -x .venv/bin/python ]; then
    echo '先に .venv を作成し、開発依存をインストールしてください。' >&2
    exit 1
fi
exec .venv/bin/python -m paperhub.deploy
