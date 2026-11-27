#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if (( $# > 1 )) || [[ "${1:-}" != "" && "$1" != "--sony" ]]; then
    echo "Usage: ./setup.sh [--sony]" >&2
    exit 2
fi

python_bin="${PYTHON:-python3.12}"
"$python_bin" -c 'import sys; sys.version_info[:2] == (3, 12) or sys.exit("Use Python 3.12.")'
"$python_bin" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

if [[ "$(uname -s)" == Darwin ]]; then
    mkdir -p build
    xcrun clang -fobjc-arc -O2 -Wall -Wextra -Wno-unused-parameter -mmacosx-version-min=14.4 \
        src/capture.m -framework Foundation -framework CoreAudio -framework AudioToolbox \
        -Wl,-sectcreate,__TEXT,__info_plist,src/CaptureInfo.plist -o build/mix-capture
    codesign --force --sign - build/mix-capture
fi

if [[ "${1:-}" == "--sony" ]]; then
    .venv/bin/python -m pip install -r requirements-sony.txt
    .venv/bin/python scripts/setup_sony.py
fi
