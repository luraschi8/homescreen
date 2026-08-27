#!/usr/bin/env bash
# The plan claims each decision is defended by a named test. This checks the
# claim is true -- the mechanism that stops the document drifting from the code
# again. Run it before committing a plan change.
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

# Only the DECISION TABLE makes present-tense claims. Acceptance criteria below
# it describe work not yet done and may name tests that do not exist yet.
# Extracted with a process substitution, not a pipe: a `while` in a pipeline
# runs in a subshell, so its exit and its variables never reach the parent --
# which is why the first version of this script could not fail.
table=$(sed -n '/^## Decisions/,/^## Server prerequisites/p' "$PLAN")
while read -r t; do
  [ -z "$t" ] && continue
  if grep -rq "RUN_TEST($t)" firmware/test/ 2>/dev/null; then continue; fi
  if grep -rq "def $t" tests/ 2>/dev/null; then continue; fi
  echo "FAIL: the decision table says '$t' defends a decision; it does not exist."
  fail=1
done < <(printf '%s\n' "$table" | grep -oE '`test_[a-z0-9_]+`' | tr -d '`' | sort -u)

if [ "$fail" -eq 0 ]; then
  n=$(printf '%s\n' "$table" | grep -oE '`test_[a-z0-9_]+`' | tr -d '`' | sort -u | wc -l | tr -d ' ')
  echo "plan ok: $fenced fenced lines, $n named tests all present"
fi
exit "$fail"
