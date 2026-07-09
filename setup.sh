#!/bin/bash

set -e

echo "===== Setup ====="

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    uv venv
else
    echo "Virtual environment already exists."
fi

echo "Installing dependencies..."
uv sync

echo "Installing Playwright Chromium..."
uv run playwright install chromium

