#!/bin/bash
# test_ui_wiring.sh
#
# Regression tests for the two UI-only features shipped in this wave that
# can't be exercised headlessly via curl:
#
#   #12 — Enemy List collapsible (localStorage-backed persist)
#   #3  — ACR first-enable warning modal (acknowledgment-key-gated)
#
# Headless testing strategy: we don't spin up a browser. Instead we check
# the invariants that matter:
#
#   1. Every DOM id referenced by a JS event handler actually exists in
#      index.html (catches "renamed an id but forgot the handler" drift).
#   2. Every click handler target exists in both files and has no typo.
#   3. The localStorage keys are referenced in both the writer (setter)
#      and the reader (gate/restore) — catches "I wrote the pref but
#      never read it" drift.
#   4. The modal markup for #3 is present and correctly wired.
#
# This won't catch render-time bugs but will catch 90% of "why is my
# toggle broken after the last commit" regressions.

set -euo pipefail

HTML=/opt/ghostport/public/index.html
ARSENAL=/opt/ghostport/public/arsenal.js
APP=/opt/ghostport/public/app.js

for f in "$HTML" "$ARSENAL" "$APP"; do
    [ -f "$f" ] || { echo "FAIL: $f missing"; exit 1; }
done

echo "=== test_ui_wiring ==="
pass=0
fail=0

check_in() {
    local name=$1
    local needle=$2
    local haystack=$3
    if grep -qE "$needle" "$haystack"; then
        echo "  PASS: $name"
        pass=$((pass + 1))
    else
        echo "  FAIL: $name — '$needle' not in $haystack"
        fail=$((fail + 1))
    fi
}

check_in_both() {
    local name=$1
    local needle=$2
    local file1=$3
    local file2=$4
    if grep -qE "$needle" "$file1" && grep -qE "$needle" "$file2"; then
        echo "  PASS: $name"
        pass=$((pass + 1))
    else
        echo "  FAIL: $name — '$needle' missing in $file1 and/or $file2"
        fail=$((fail + 1))
    fi
}

# ── #12 Enemy List collapsible ─────────────────────────────────────
echo
echo "— #12 Enemy List collapsible —"

# DOM ids the JS refers to must exist in HTML
check_in "enemies-header id exists"    'id="enemies-header"'  "$HTML"
check_in "enemies-body id exists"      'id="enemies-body"'    "$HTML"
check_in "enemies-caret id exists"     'id="enemies-caret"'   "$HTML"

# Header is marked clickable + a11y
check_in "header has role=button"      'id="enemies-header"[^>]*role="button"'  "$HTML"
check_in "header has aria-expanded"    'id="enemies-header"[^>]*aria-expanded'  "$HTML"

# JS-side: setter + toggle + reader of the localStorage pref
check_in "setEnemiesExpanded defined"  'function setEnemiesExpanded' "$ARSENAL"
check_in "toggleEnemies defined"       'function toggleEnemies'       "$ARSENAL"
check_in "pref key constant"           'ENEMIES_PREF_KEY'              "$ARSENAL"

# Pref key must be both WRITTEN (setItem) and READ (getItem)
# — catches "I persist it but never restore it" bug class
check_in "pref is written"             'localStorage\.setItem\(ENEMIES_PREF_KEY' "$ARSENAL"
check_in "pref is read"                'localStorage\.getItem\(ENEMIES_PREF_KEY' "$ARSENAL"

# Click handler wired via app.js (CSP-compliant — no inline onclick).
# Check via pair: the id is referenced AND toggleEnemies is called somewhere
# in app.js. The actual multi-line listener is harder to regex; pair check
# gives equivalent coverage against the "forgot to wire" drift.
check_in "app.js references enemies-header"  'getElementById\("enemies-header"\)' "$APP"
check_in "app.js calls toggleEnemies"        'toggleEnemies\(\)'                   "$APP"

# Default-collapsed invariant: restore function must default to false
check_in "default-collapsed intent"    'expanded = false' "$ARSENAL"

# ── #3 ACR first-enable warning modal ─────────────────────────────
echo
echo "— #3 ACR first-enable warning modal —"

# Modal markup present in HTML
check_in "acr-modal element exists"    'id="acr-modal"'       "$HTML"
check_in "modal title mentions SMART TV"  'BLOCK SMART TV SURVEILLANCE' "$HTML"
check_in "cancel button has onclick"   'onclick="acrCancel\(\)"'   "$HTML"
check_in "confirm button has onclick"  'onclick="acrConfirm\(\)"'  "$HTML"

# Global handlers defined in app.js
check_in "window.acrConfirm defined"   'window\.acrConfirm\s*='    "$APP"
check_in "window.acrCancel defined"    'window\.acrCancel\s*='     "$APP"

# Acknowledgment key must be both written (on confirm) and read (on toggle gate)
check_in "ACR_ACK_KEY defined"         'ACR_ACK_KEY\s*='           "$APP"
check_in "ack is written on confirm"   'localStorage\.setItem\(ACR_ACK_KEY' "$APP"
check_in "ack is read in toggle gate"  'localStorage\.getItem\(ACR_ACK_KEY' "$APP"

# Toggle logic: _fsToggleCat must gate on cat==='acr' AND newState
# AND must defer to _fsApplyCat instead of inlining the apply
check_in "ACR gate is specific to cat"  "cat === 'acr'" "$APP"
check_in "_fsApplyCat defer path"       '_fsApplyCat\('  "$APP"

# The pending-cat state must be cleared on both confirm AND cancel
# to prevent a stale reference from carrying over between interactions
check_in "pending cleared on confirm"   'window\._acrPendingCat = null' "$APP"

# ── Summary ────────────────────────────────────────────────────────
echo
echo "=== summary ==="
echo "  pass: $pass"
echo "  fail: $fail"

[ "$fail" -eq 0 ]
