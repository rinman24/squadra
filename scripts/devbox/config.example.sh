# shellcheck shell=bash
# config.example.sh — template for per-developer overrides of the flotilla
# dev-container host scripts.
#
# These scripts work out of the box with NO local config: they only drive
# `docker compose` on the gswa-devbox host (no secrets, no Azure subscription). Copy
# this file to config.local.sh (gitignored) ONLY if you need to override a default:
#
#     cp scripts/devbox/config.example.sh scripts/devbox/config.local.sh
#
# lib.sh sources config.sh (tracked defaults) and then config.local.sh if it exists, so
# anything set here wins over the defaults. Examples (uncomment + edit):

# # Point the stack at a clone elsewhere on the host instead of this checkout:
# FLOTILLA_REPO_DIR="${FLOTILLA_REPO_DIR:-/home/azureuser/flotilla}"

# # After the GitHub migration (migrate-flotilla plan, Phase 2), repoint the
# # clone-if-absent source:
# FLOTILLA_REPO_URL="${FLOTILLA_REPO_URL:-https://github.com/<USER>/flotilla.git}"
