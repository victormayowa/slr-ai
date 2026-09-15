#!/usr/bin/env bash
# Creates the R environment for the statistics engine in user space (no sudo), using micromamba and conda-forge.
#   backend/scripts/setup_r_env.sh
# Then set RSCRIPT_PATH in backend/.env to the Rscript it prints.
set -euo pipefail

PREFIX="${OMNIREVIEW_R_PREFIX:-$HOME/.local/share/omnireview/r}"
BIN_DIR="$HOME/.local/bin"
MICROMAMBA="$BIN_DIR/micromamba"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -x "$MICROMAMBA" ]; then
  mkdir -p "$BIN_DIR"
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C "$HOME/.local" bin/micromamba
fi

# Downloads from conda-forge can be slow; retry a few times, reusing what was already downloaded.
for attempt in 1 2 3 4; do
  if [ -d "$PREFIX/conda-meta" ]; then
    "$MICROMAMBA" install -y -p "$PREFIX" -f "$HERE/../stats/environment.yml" && break
  else
    "$MICROMAMBA" create -y -p "$PREFIX" -f "$HERE/../stats/environment.yml" && break
  fi
  if [ "$attempt" = 4 ]; then exit 1; fi
  echo "Retrying the R environment install (attempt $((attempt + 1)))..."
  sleep 10
done

# Packages not built on conda-forge are installed from CRAN into the same environment (pure R, no compiler needed).
"$PREFIX/bin/Rscript" -e '
wanted <- c("jsonlite", "metafor", "meta", "netmeta", "mada", "clubSandwich", "lme4", "bayesmeta", "ggplot2", "svglite", "robvis")
missing <- wanted[!vapply(wanted, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) install.packages(missing, repos = "https://cloud.r-project.org")
still_missing <- wanted[!vapply(wanted, requireNamespace, logical(1), quietly = TRUE)]
if (length(still_missing)) stop("Could not install: ", paste(still_missing, collapse = ", "))
'

echo "R is ready. Add this to backend/.env:"
echo "RSCRIPT_PATH=$PREFIX/bin/Rscript"
