#!/usr/bin/env bash
# Local test runner for p2pool. There is no CI (by decision), so this is the
# only automated check that guards the py3 port.
#
#   ./contrib/run-tests.sh            # setup (once) + full run
#   ./contrib/run-tests.sh --setup    # just (re)build the venv
#   ./contrib/run-tests.sh --gate     # golden-vector gate only (fast)
#   ./contrib/run-tests.sh --no-setup # use P2POOL_VENV as-is, skip setup
#
# Two independent checks:
#   1. GATE  -- p2pool/test/golden_check.py diffs consensus-critical output
#               against the frozen py2 reference. Non-negotiable: if this
#               fails, the port is NOT consensus-faithful and the node will
#               fork off the P2P network.
#   2. SUITE -- twisted.trial p2pool. Note the py2 baseline is NOT all-green;
#               see p2pool/test/PY2_BASELINE.md.
#
# Exit 0 only when both pass.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${P2POOL_VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_rst=$'\033[0m'
say() { printf '%s\n' "$*"; }
hr()  { say "------------------------------------------------------------"; }

do_setup() {
  hr; say "Setting up venv at $VENV"; hr
  command -v python3 >/dev/null || { say "${c_red}python3 not found${c_rst}"; exit 2; }
  if [ ! -x "$PY" ]; then
    python3 -m venv "$VENV" || {
      say "${c_red}venv creation failed${c_rst}"; exit 2; }
  fi

  # Debian/Ubuntu split these out; without them the venv has no pip at all.
  if ! "$PY" -m pip --version >/dev/null 2>&1; then
    say "${c_yel}This venv has no pip module.${c_rst}"
    say "  Debian/Ubuntu:  sudo apt-get install python3-venv python3-pip"
    say "  or:            $PY -m ensurepip --upgrade"
    say "  or:            curl -sS https://bootstrap.pypa.io/get-pip.py | $PY"
    say ""
    say "Alternatively skip setup and point at your own interpreter:"
    say "  P2POOL_VENV=/path/to/venv ./contrib/run-tests.sh --no-setup"
    exit 2
  fi

  "$PY" -m pip install --quiet --upgrade pip setuptools wheel || {
    say "${c_red}pip bootstrap failed${c_rst}"; exit 2; }

  say "installing runtime requirements..."
  "$PY" -m pip install --quiet -r "$ROOT/requirements.txt" || {
    say "${c_red}requirements.txt install failed${c_rst}"; exit 2; }

  # Test deps (mock, coverage) are not pinned in requirements.txt; install
  # what the suite actually imports.
  say "installing test requirements..."
  "$PY" -m pip install --quiet mock coverage || {
    say "${c_yel}warning: some test deps unavailable${c_rst}"; }

  say "${c_grn}setup complete${c_rst}"
}

run_gate() {
  hr; say "GOLDEN-VECTOR GATE (consensus fidelity vs frozen py2 reference)"; hr
  if "$PY" "$ROOT/p2pool/test/golden_check.py" "$@"; then
    GATE_RC=0; say "${c_grn}gate: PASS${c_rst}"
  else
    GATE_RC=1; say "${c_red}gate: FAIL${c_rst}"
  fi
}

run_suite() {
  hr; say "TEST SUITE (twisted.trial p2pool)"; hr
  # Tests requiring a live bitcoind or a real network are excluded; see
  # p2pool/test/PY2_BASELINE.md.
  cd "$ROOT"
  if "$PY" -m twisted.trial p2pool; then
    SUITE_RC=0; say "${c_grn}suite: PASS${c_rst}"
  else
    SUITE_RC=1; say "${c_yel}suite: FAIL (compare against PY2_BASELINE.md)${c_rst}"
  fi
}

GATE_RC=1; SUITE_RC=1

case "${1:-}" in
  --setup) do_setup; exit 0 ;;
  --gate)  do_setup; run_gate -v; exit $GATE_RC ;;
  --no-setup)
    [ -x "$PY" ] || { say "${c_red}no interpreter at $PY${c_rst}"; exit 2; }
    run_gate; run_suite ;;
  -h|--help) sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

do_setup
run_gate
run_suite

hr
if [ "$GATE_RC" -eq 0 ] && [ "$SUITE_RC" -eq 0 ]; then
  say "${c_grn}ALL CHECKS PASSED${c_rst}"; exit 0
fi
[ "$GATE_RC" -ne 0 ] && say "${c_red}golden gate failed${c_rst} -- do not ship"
say "${c_yel}one or more checks failed${c_rst}"
exit 1