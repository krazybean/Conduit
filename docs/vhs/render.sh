#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

rm -rf ./conduit-vhs-frames
vhs docs/vhs/conduit.tape

ffmpeg \
  -framerate 50 \
  -start_number 1 \
  -i './conduit-vhs-frames/frame-text-%05d.png' \
  -vf 'fps=15,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse' \
  -loop 0 \
  docs/assets/conduit-demo.gif

rm -rf ./conduit-vhs-frames
printf 'Wrote docs/assets/conduit-demo.gif\n'
