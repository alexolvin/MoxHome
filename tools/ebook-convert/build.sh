#!/usr/bin/env bash
# Сборка образа localhost/ebook-convert (Calibre для импорта, TZ-R §8).
# Одиночный вызов из корня каталога: ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

podman build -t localhost/ebook-convert .
echo "образ собран: localhost/ebook-convert"

# Контрольная проба тем же флагами, что обёртка (без монтирования).
podman run --rm --network=none --read-only --tmpfs /tmp \
    -e HOME=/tmp --userns=keep-id --memory=4g --pids-limit=256 \
    localhost/ebook-convert --version
