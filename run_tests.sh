#!/usr/bin/env bash
# Runs the whole suite: syntax check, unit tests, live collector tests, GUI tests with screenshots.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
echo "== byte-compile"
python3 -m compileall -q pulse tests bin/pulse || exit 1
echo "== unit + live + gui tests"
python3 -W ignore::DeprecationWarning -m unittest discover -s tests -t . -v "$@"
