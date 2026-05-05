#!/usr/bin/env bash
# Pre-release validation: verify the wheel builds cleanly.
set -euo pipefail
echo "Building wheel..."
uv build --wheel --quiet
rm -rf dist/
echo "Pre-release checks passed."
