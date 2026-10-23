#!/bin/bash
# Double-click to run LAN Chat on a Mac without building an app.
# First time only: right-click -> Open (or run: chmod +x run_mac.command).
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/"
  read -r -p "Press Enter to close..."; exit 1
fi

if [ ! -d venv ]; then
  python3 -m venv venv || exit 1
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install kivy pynacl zeroconf arabic-reshaper "python-bidi==0.4.2" || exit 1
fi

./venv/bin/python main.py
