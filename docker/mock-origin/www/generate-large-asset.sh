#!/usr/bin/env bash
# Generate a deterministic large asset (~29MB) used by Range-header tests (§5.1 of the paper).
set -euo pipefail

out="${1:-/usr/share/nginx/html/test.png}"
size_mb="${2:-29}"
dd if=/dev/urandom of="$out" bs=1M count="$size_mb" status=none
echo "wrote $out ($(stat -c%s "$out") bytes)"
