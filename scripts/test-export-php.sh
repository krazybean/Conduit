#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir "$TMP/bin"
cat > "$TMP/bin/php" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == "-r" ]]
EOF
cat > "$TMP/bin/composer" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/bin/php" "$TMP/bin/composer"
export PATH="$TMP/bin:$PATH"

for version in bootstrap 1.2.3; do
  output="$TMP/$version"
  "$ROOT/scripts/export-php.sh" "$version" "$output" >/dev/null
  test -f "$output/composer.json"
  test -f "$output/providers.json"
  test -f "$output/README.md"
  test -f "$output/LICENSE"
  test -d "$output/src"
done

if "$ROOT/scripts/export-php.sh" 1.2 "$TMP/invalid" >/dev/null 2>&1; then
  echo "non-SemVer version was accepted" >&2
  exit 1
fi

workflow="$ROOT/.github/workflows/php-distribution.yml"
grep -Fq "ref: ${{ github.event_name == 'workflow_dispatch' && 'main' || github.ref }}" "$workflow"
grep -Fq "SOURCE_TAG: ${{ github.event_name == 'push' && github.ref_name || '' }}" "$workflow"
grep -Fq 'Bootstrap complete: pushed distribution main without creating a tag.' "$workflow"
