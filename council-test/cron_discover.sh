#!/usr/bin/env bash
# Weekly free-model discovery probe. Run by cron; emits JSON manifest.
#
# Cron entry (sudo crontab -u shellwire -e):
#   17 3 * * 0 /home/shellwire/backend/scripts/cron_discover.sh >> /home/shellwire/.discover.log 2>&1
set -euo pipefail

SHELLWIRE_DIR=/home/shellwire
BACKEND_DIR="$SHELLWIRE_DIR/backend"
SCRIPT="$BACKEND_DIR/scripts/discover_free_models.py"
OUT="$BACKEND_DIR/discovered_models.json"
ARCHIVE_DIR="$BACKEND_DIR/.model-discovery"

mkdir -p "$ARCHIVE_DIR"

# Source the keys so the probe sees provider env vars.
set -a
# shellcheck disable=SC1091
. "$SHELLWIRE_DIR/.env"
set +a

# Archive previous manifest before overwriting.
if [ -f "$OUT" ]; then
  cp "$OUT" "$ARCHIVE_DIR/discovered_models.$(date -u +%Y%m%dT%H%M%SZ).json"
fi

# Prune archives older than 90 days.
find "$ARCHIVE_DIR" -name 'discovered_models.*.json' -mtime +90 -delete 2>/dev/null || true

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "=== discover run @ $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
python3 "$SCRIPT" --out "$TMP" --timeout 25 --parallel 6

mv "$TMP" "$OUT"
echo "Wrote $OUT"

# Quick summary
python3 -c "
import json
d = json.load(open('$OUT'))
for p, r in sorted(d['providers'].items()):
    err = f\" err={r['error']}\" if r.get('error') else ''
    print(f\"  {p:<12} live={len(r['live']):<3} slow={len(r['slow']):<3} dead={len(r['dead']):<3} checked={r['checked']}{err}\")
"
