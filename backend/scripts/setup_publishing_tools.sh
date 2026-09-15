#!/usr/bin/env bash
# Installs the manuscript export tools without sudo: Pandoc (Word, LaTeX, and citation formatting with CSL styles),
# Tectonic (PDF from LaTeX; it downloads TeX packages on first use), and rsvg-convert (SVG figures in PDFs).
# They go into ~/.local/share/omnireview/tools (override with OMNIREVIEW_TOOLS_PREFIX) using micromamba.
set -euo pipefail

PREFIX="${OMNIREVIEW_TOOLS_PREFIX:-$HOME/.local/share/omnireview/tools}"
BIN="$HOME/.local/bin"
MICROMAMBA="$BIN/micromamba"

if [ ! -x "$MICROMAMBA" ]; then
  mkdir -p "$BIN"
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C "$HOME/.local" bin/micromamba
fi

for attempt in 1 2 3 4; do
  if "$MICROMAMBA" create -y -p "$PREFIX" -c conda-forge pandoc tectonic librsvg; then
    break
  fi
  echo "Install attempt $attempt failed; retrying" >&2
  sleep 10
done

for tool in pandoc tectonic rsvg-convert; do
  if [ ! -x "$PREFIX/bin/$tool" ]; then
    echo "Error: $tool wasn't installed" >&2
    exit 1
  fi
done
echo "Publishing tools are ready. Add these to backend/.env:"
echo "PANDOC_PATH=$PREFIX/bin/pandoc"
echo "TECTONIC_PATH=$PREFIX/bin/tectonic"
echo "RSVG_CONVERT_PATH=$PREFIX/bin/rsvg-convert"
