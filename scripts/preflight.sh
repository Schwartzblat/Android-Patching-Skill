#!/usr/bin/env bash
# Environment check for the android_patching skill.
# Exit 0 = every hard requirement met. Exit 1 = at least one FAIL.
set -uo pipefail

fails=0
warns=0
row() { printf '  %-7s %-22s %s\n' "$1" "$2" "${3:-}"; }
pass() { row PASS "$1" "${2:-}"; }
fail() { row FAIL "$1" "${2:-}"; fails=$((fails + 1)); }
warn() { row WARN "$1" "${2:-}"; warns=$((warns + 1)); }
info() { row '--' "$1" "${2:-}"; }

echo "android_patching preflight"
echo

# --- JDK 17+ (apktool 3.x and AGP 8.10 both need it) ---
if command -v java >/dev/null 2>&1; then
    jver=$(java -version 2>&1 | head -1 | sed -E 's/.*version "([0-9]+).*/\1/')
    if [[ "$jver" =~ ^[0-9]+$ ]] && [ "$jver" -ge 17 ]; then
        pass "java" "$(java -version 2>&1 | head -1)"
    else
        fail "java" "need 17+, found: $(java -version 2>&1 | head -1)"
    fi
else
    fail "java" "not on PATH"
fi

# --- a reader for stage 2: asc preferred, jadx the fallback ---
# Not required: stage 2 only needs a target class/method/signature, and that can
# come from the user, the jadx GUI, or the jadx-mcp-server tools instead.
ASC_SKILL=${ASC_SKILL:-$HOME/.claude/skills/reversing-apks-with-asc}
if command -v asc >/dev/null 2>&1; then
    if [ -x "$ASC_SKILL/scripts/ascq" ]; then
        pass "asc" "$(command -v asc) + reversing-apks-with-asc skill"
    else
        pass "asc" "$(command -v asc) -- skill not installed, see stage 2 for the commands"
    fi
elif command -v jadx >/dev/null 2>&1; then
    pass "jadx" "$(command -v jadx) -- no asc, using the jadx fallback"
else
    warn "reader" "no asc or jadx -- supply the target class/method yourself, or use the jadx GUI / MCP"
fi

# --- python 3.11+, which stitch requires ---
PY=${PYTHON:-python3}
if command -v "$PY" >/dev/null 2>&1; then
    pyv=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
    if "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        pass "python" "$pyv ($PY)"
    else
        fail "python" "need 3.11+ (stitch requires it), found $pyv at $PY"
    fi
else
    fail "python" "$PY not found (set PYTHON=/path/to/venv/bin/python)"
fi

# --- stitch, and the apktool jar it bundles ---
if "$PY" -c 'import stitch' >/dev/null 2>&1; then
    sinfo=$("$PY" - <<'PYEOF' 2>/dev/null
import stitch, pathlib
from stitch.common import APKTOOL_PATH, UBER_APK_SIGNER_PATH
v = "?"
try:
    from importlib.metadata import version
    v = version("stitch")
except Exception:
    pass
print(v, pathlib.Path(str(APKTOOL_PATH)).name, pathlib.Path(str(UBER_APK_SIGNER_PATH)).name,
      pathlib.Path(str(APKTOOL_PATH)).exists(), pathlib.Path(str(UBER_APK_SIGNER_PATH)).exists())
PYEOF
)
    read -r sver apkt signer apkt_ok signer_ok <<<"$sinfo"
    pass "stitch" "$sver ($PY)"
    [ "$apkt_ok" = "True" ] && pass "apktool" "bundled $apkt" || fail "apktool" "bundled jar missing: $apkt"
    [ "$signer_ok" = "True" ] && pass "signer" "bundled $signer" || fail "signer" "bundled jar missing: $signer"
else
    fail "stitch" "cannot 'import stitch' with $PY -- pip install stitch"
    info "apktool" "skipped (stitch supplies the jar)"
    info "signer" "skipped (stitch supplies the jar)"
fi

# --- Android SDK, for gradlew ---
sdk=""
for cand in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}" "$HOME/Android/Sdk" "$HOME/Library/Android/sdk"; do
    [ -n "$cand" ] && [ -d "$cand/platforms" ] && { sdk="$cand"; break; }
done
if [ -n "$sdk" ]; then
    pass "sdk" "$sdk"
else
    fail "sdk" "no Android SDK found (set ANDROID_HOME)"
fi

echo
# --- optional: only stage 3 (frida probe) needs these ---
if command -v adb >/dev/null 2>&1; then
    devs=$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device"' | wc -l | tr -d ' ')
    info "adb" "$devs device(s) attached  [optional, stage 3 only]"
else
    info "adb" "not on PATH  [optional, stage 3 only]"
fi
if command -v frida >/dev/null 2>&1; then
    info "frida" "$(frida --version 2>/dev/null)  [optional, stage 3 only]"
else
    info "frida" "not on PATH  [optional, stage 3 only]"
fi

echo
if [ "$fails" -eq 0 ]; then
    if [ "$warns" -eq 0 ]; then
        echo "preflight OK"
    else
        echo "preflight OK ($warns warning(s))"
    fi
    exit 0
fi
echo "preflight FAILED ($fails failure(s), $warns warning(s))"
exit 1
