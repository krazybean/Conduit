#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 X.Y.Z" >&2
  exit 2
fi
TARGET=$1
if [[ ! $TARGET =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "invalid version: $TARGET" >&2
  exit 2
fi
if [[ "$(git rev-parse --show-toplevel)" != "$ROOT" ]]; then
  echo "not a repository root: $ROOT" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree must be clean before preparing a release" >&2
  exit 1
fi

TS_VERSION="$(node -e 'process.stdout.write(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).version)' typescript/package.json)"
TS_LOCK_VERSION="$(node -e 'process.stdout.write(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).version)' typescript/package-lock.json)"
PY_VERSION="$(sed -nE 's/^version = "([^"]+)"$/\1/p' python/pyproject.toml | head -n 1)"
RUST_VERSION="$(sed -nE 's/^version = "([^"]+)"$/\1/p' rust/Cargo.toml | head -n 1)"

if [[ $TS_VERSION != "$TS_LOCK_VERSION" || $TS_VERSION != "$PY_VERSION" || $TS_VERSION != "$RUST_VERSION" ]]; then
  echo "release versions are not synchronized" >&2
  printf 'typescript=%s lock=%s python=%s rust=%s\n' "$TS_VERSION" "$TS_LOCK_VERSION" "$PY_VERSION" "$RUST_VERSION" >&2
  exit 1
fi
CURRENT=$TS_VERSION

python3 - "$CURRENT" "$TARGET" <<'PY'
from pathlib import Path
import sys

current, target = sys.argv[1:]
updates = {
    Path("typescript/package.json"): (1, f'"version": "{current}",', f'"version": "{target}",'),
    Path("typescript/package-lock.json"): (2, f'"version": "{current}",', f'"version": "{target}",'),
    Path("python/pyproject.toml"): (1, f'version = "{current}"', f'version = "{target}"'),
    Path("rust/Cargo.toml"): (1, f'version = "{current}"', f'version = "{target}"'),
}
for path, (expected, old, new) in updates.items():
    text = path.read_text()
    if text.count(old) != expected:
        raise SystemExit(f"unexpected version layout in {path}")
    path.write_text(text.replace(old, new))

readme = Path("rust/README.md")
text = readme.read_text()
old = f'conduit-ai = "{current}"'
if old in text:
    if text.count(old) != 1:
        raise SystemExit(f"unexpected version layout in {readme}")
    readme.write_text(text.replace(old, f'conduit-ai = "{target}"'))
PY

echo "Validating TypeScript..."
npm test --prefix "$ROOT/typescript"
(cd "$ROOT/typescript" && npm pack --dry-run)

PYTHON="python3"
if [[ -x "$ROOT/python/.venv/bin/python" ]]; then
  PYTHON="$ROOT/python/.venv/bin/python"
fi
echo "Validating Python with $PYTHON..."
PYTHONPATH="$ROOT/python${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m unittest discover -s "$ROOT/python/tests"

echo "Validating Rust..."
cargo test --manifest-path "$ROOT/rust/Cargo.toml"
cargo fmt --manifest-path "$ROOT/rust/Cargo.toml" -- --check
cargo publish --dry-run --allow-dirty --manifest-path "$ROOT/rust/Cargo.toml"

git diff --check

echo
echo "Prepared Conduit v$TARGET."
echo "Changed files:"
git diff --name-only
echo
echo "Next: review the diff, commit, push, and open/merge the release PR."
