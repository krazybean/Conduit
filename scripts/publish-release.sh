#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 X.Y.Z [release-notes.md]" >&2
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
if [[ "$(git branch --show-current)" != main ]]; then
  echo "publication must run from main" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree must be clean before publication" >&2
  exit 1
fi

git fetch origin
if [[ "$(git rev-parse main)" != "$(git rev-parse origin/main)" ]]; then
  echo "local main does not match origin/main" >&2
  exit 1
fi

TS_VERSION="$(node -e 'process.stdout.write(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).version)' typescript/package.json)"
TS_LOCK_VERSION="$(node -e 'process.stdout.write(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).version)' typescript/package-lock.json)"
PY_VERSION="$(sed -nE 's/^version = "([^"]+)"$/\1/p' python/pyproject.toml | head -n 1)"
RUST_VERSION="$(sed -nE 's/^version = "([^"]+)"$/\1/p' rust/Cargo.toml | head -n 1)"
if [[ $TS_VERSION != "$TARGET" || $TS_LOCK_VERSION != "$TARGET" || $PY_VERSION != "$TARGET" || $RUST_VERSION != "$TARGET" ]]; then
  echo "package versions do not all equal $TARGET" >&2
  printf 'typescript=%s lock=%s python=%s rust=%s\n' "$TS_VERSION" "$TS_LOCK_VERSION" "$PY_VERSION" "$RUST_VERSION" >&2
  exit 1
fi

TAG="v$TARGET"
if git show-ref --verify --quiet "refs/tags/$TAG"; then
  echo "tag already exists locally: $TAG" >&2
  exit 1
fi
remote_tag_status=0
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 || remote_tag_status=$?
if [[ $remote_tag_status -eq 0 ]]; then
  echo "tag already exists on origin: $TAG" >&2
  exit 1
fi
if [[ $remote_tag_status -ne 2 ]]; then
  echo "could not verify whether tag exists on origin: $TAG" >&2
  exit 1
fi

echo "Checking push access to origin..."
git push --dry-run origin HEAD:main >/dev/null

command -v npm >/dev/null
npm whoami >/dev/null
command -v gh >/dev/null
gh auth status >/dev/null 2>&1

PYTHON="$ROOT/python/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "python/.venv/bin/python is required for publication." >&2
  echo "Create it with: python3 -m venv .venv" >&2
  echo "Then run: source .venv/bin/activate && python -m pip install --upgrade pip build twine" >&2
  exit 1
fi
"$PYTHON" -c 'import build, twine'

NOTES_FILE=""
TEMP_NOTES=""
DEFAULT_NOTES=0
cleanup() {
  if [[ -n "$TEMP_NOTES" ]]; then
    rm -f "$TEMP_NOTES"
  fi
}
trap cleanup EXIT
if [[ $# -eq 2 ]]; then
  if [[ ! -f $2 ]]; then
    echo "release notes file not found: $2" >&2
    exit 1
  fi
  NOTES_FILE="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
else
  TEMP_NOTES="$(mktemp)"
  printf 'Conduit v%s\n' "$TARGET" > "$TEMP_NOTES"
  NOTES_FILE="$TEMP_NOTES"
  DEFAULT_NOTES=1
fi

echo "Publishing npm..."
(cd "$ROOT/typescript" && npm publish --access public)
echo "npm published: @krazybean/conduit@$TARGET"

echo "Publishing PyPI..."
rm -rf "$ROOT/python/dist"
(cd "$ROOT/python" && "$PYTHON" -m build && "$PYTHON" -m twine upload dist/*)
echo "PyPI published: conduit-llm@$TARGET"

echo "Publishing crates.io..."
cargo publish --manifest-path "$ROOT/rust/Cargo.toml"
echo "crates.io published: conduit-ai@$TARGET"

git tag -a "$TAG" -m "Conduit v$TARGET"
git push origin "$TAG"
echo "Tag pushed: $TAG"

RELEASE_URL="$(gh release create "$TAG" --title "Conduit v$TARGET" --notes-file "$NOTES_FILE")"
echo "GitHub release created: $RELEASE_URL"
if [[ $DEFAULT_NOTES -eq 1 ]]; then
  echo "No release notes file supplied; edit the GitHub release afterward if needed."
fi
