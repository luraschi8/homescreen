#!/usr/bin/env bash
# The plan claims each decision is defended by a named test. This checks that
# the claim is true -- the mechanism that stops the document drifting from the
# code again. Run it before committing a plan change.
set -u
PLAN=docs/superpowers/plans/2026-08-26-round-screen-firmware.md
fail=0

fenced=$(awk '/^```/{f=!f; next} f{c++} END{print c+0}' "$PLAN")
if [ "$fenced" -gt 40 ]; then
  echo "FAIL: $fenced lines of code in the plan. Code belongs in firmware/,"
  echo "      where a compiler judges it. This is how revisions 1-3 stopped"
  echo "      converging: 2,738 of 3,785 lines were unverifiable prose."
  fail=1
fi

# Every backticked test_* name in the decision table must exist somewhere.
grep -oE '`test_[a-z0-9_]+`' "$PLAN" | tr -d '`' | sort -u | while read -r t; do
  if grep -rq "RUN_TEST($t)" firmware/test/ 2>/dev/null; then continue; fi
  if grep -rq "def $t" tests/ 2>/dev/null; then continue; fi
  echo "FAIL: the plan says '$t' defends a decision; no such test exists."
  exit 1
done || fail=1

[ "$fail" -eq 0 ] && echo "plan ok: $fenced fenced lines, every named test exists"
exit "$fail"
