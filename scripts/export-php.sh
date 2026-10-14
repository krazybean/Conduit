#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 X.Y.Z OUTPUT_DIR" >&2
  exit 2
fi

VERSION=$1
OUTPUT=$2
if [[ ! $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "invalid version: $VERSION" >&2
  exit 2
fi
if [[ -z "$OUTPUT" || "$OUTPUT" == "/" || "$OUTPUT" == "$ROOT" ]]; then
  echo "refusing unsafe output directory: $OUTPUT" >&2
  exit 2
fi
if [[ ! -f php/composer.json || ! -f php/providers.json || ! -d php/src || ! -f php/README.md || ! -f LICENSE ]]; then
  echo "PHP distribution inputs are incomplete" >&2
  exit 1
fi
if find php -type l -print -quit | grep -q .; then
  echo "PHP distribution refuses symlinked inputs" >&2
  exit 1
fi
command -v php >/dev/null || { echo "php is required for PHP distribution validation" >&2; exit 1; }
command -v composer >/dev/null || { echo "composer is required for PHP distribution validation" >&2; exit 1; }

php -r '
foreach (["php/composer.json", "php/providers.json"] as $path) {
    json_decode(file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
}
$composer = json_decode(file_get_contents("php/composer.json"), true, 512, JSON_THROW_ON_ERROR);
if (($composer["name"] ?? null) !== "krazybean/conduit") {
    fwrite(STDERR, "composer package name must be krazybean/conduit\n");
    exit(1);
}
'
composer validate --strict --no-check-publish --working-dir php >/dev/null

if [[ -e "$OUTPUT" ]]; then
  if [[ ! -d "$OUTPUT" ]]; then
    echo "output path is not a directory: $OUTPUT" >&2
    exit 1
  fi
  if find "$OUTPUT" -mindepth 1 -print -quit | grep -q .; then
    echo "output directory must be empty: $OUTPUT" >&2
    exit 1
  fi
else
  mkdir -p "$OUTPUT"
fi

cp php/composer.json "$OUTPUT/composer.json"
cp php/providers.json "$OUTPUT/providers.json"
cp php/README.md "$OUTPUT/README.md"
cp LICENSE "$OUTPUT/LICENSE"
cp -R php/src "$OUTPUT/src"

test -f "$OUTPUT/composer.json"
test -f "$OUTPUT/providers.json"
test -f "$OUTPUT/README.md"
test -f "$OUTPUT/LICENSE"
test -d "$OUTPUT/src"
test "$(find "$OUTPUT" -type f -name '*.php' | wc -l | tr -d ' ')" -gt 0
echo "Prepared PHP distribution v$VERSION in $OUTPUT"
