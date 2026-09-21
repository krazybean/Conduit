#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

: "${GROQ_API_KEY:?GROQ_API_KEY is required}"

rm -rf ./conduit-vhs-remote-frames
vhs docs/vhs/groq.tape

ffmpeg \
  -framerate 50 \
  -start_number 1 \
  -i './conduit-vhs-remote-frames/frame-text-%05d.png' \
  -vf 'fps=15,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse' \
  -loop 0 \
  docs/assets/conduit-remote-demo.gif

rm -rf ./conduit-vhs-remote-frames
printf 'Wrote docs/assets/conduit-remote-demo.gif\n'
