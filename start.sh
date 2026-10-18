#!/bin/sh
# Starts the U1 DOOM page; the first start sets up .venv with paramiko (SSH).
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv && .venv/bin/pip install --quiet paramiko || exit 1
fi
exec .venv/bin/python launcher.py
