#!/usr/bin/env bash
# Robust apt install for GitHub runners: no man-db rebuild (can take minutes),
# network timeouts, and retries with a hard time limit per attempt.
# usage: .github/actions/setup/apt.sh pkg1 pkg2 ...
set -uo pipefail
missing=()
for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
[ ${#missing[@]} -eq 0 ] && { echo "apt: already installed: $*"; exit 0; }
sudo rm -f /var/lib/man-db/auto-update
opts=(-o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 -o DPkg::Lock::Timeout=60)
for attempt in 1 2 3; do
  echo "apt: installing ${missing[*]} (attempt $attempt)"
  if timeout 240 sudo apt-get "${opts[@]}" update -qq && \
     timeout 300 sudo DEBIAN_FRONTEND=noninteractive apt-get "${opts[@]}" install -y -qq --no-install-recommends "${missing[@]}" >/dev/null; then
    exit 0
  fi
  sleep $((attempt * 5))
done
echo "::error::apt install failed after 3 attempts: ${missing[*]}"
exit 1
