#!/bin/sh
set -eu
base=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$base/.venv/bin/python" ]; then
    printf 'Run ./setup.sh in "%s" first.\n' "$base" >&2
    exit 1
fi
exec "$base/.venv/bin/python" "$base/src/listener.py" "$@"
