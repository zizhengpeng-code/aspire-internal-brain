#!/usr/bin/env bash
# Capture one fresh, internally consistent set of page screenshots against the *current* code.
#
#   PAGES=Chat,Admin SHOT_DIALOG=1 results/capture-review.sh
#
# The start → wait → probe → reap lifecycle lives in `results/with-app.sh`, because a server
# started as its own background shell dies with that shell.
set -u
cd "$(dirname "$0")/.." || exit 1

export CHROME_PATH="C:/Program Files/Google/Chrome/Application/chrome.exe"
export ACCOUNT="${ACCOUNT:-Grace Lim}"
export PAGES="${PAGES:-Chat,Admin,Compliance,ROI}"
export ASK="${ASK-Show me the latest runbook for the payment service}"
export SHOT_SELECTED="${SHOT_SELECTED-}"
export SHOT_DIALOG="${SHOT_DIALOG-}"
# The default prefix is what `build_review_sheet.py` reads, so running this script with no
# arguments regenerates exactly the set the sheet consumes — no chance of the sheet quietly
# sitting on a previous generation's screenshots.
export OUT_PREFIX="${OUT_PREFIX:-results/ui/final}"
export LOG="${LOG:-results/capture-app.log}"

NODE="C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
bash results/with-app.sh "$NODE" results/ui-probe.mjs
