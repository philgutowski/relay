#!/usr/bin/env bash
# Relay prototype runner, preserved verbatim below the header.
#
# Origin: /Users/pgutowski/Documents/PhilAI/Integrel/support-workbench/plans/run-sweep.sh
# Copied: 2026-08-25
#
# This is the hand-built proof that the Relay design works. It ran one card
# (IW-83) on 2026-08-25 as a headless claude -p process under dontAsk with an
# mcp__atlassian__* allowlist, and the tracker writes went through with no
# prompt. The source directory is gitignored in support-workbench, so this copy
# is the only committed record. It is Relay's artifact, not support-workbench's.
#
# Everything project specific in it (card keys, model, effort, brief path, the
# main-to-master mirror rule) is exactly what the manifest replaces. Read it as
# evidence, not as the runner.
#
# Original file follows unchanged.

#!/usr/bin/env bash
# Unattended serial sweep of the build-ready IW cards.
#
# One `claude -p` process per card, so every card starts on an empty context
# window and the run never compacts. State between cards is durable: a merge
# commit on main and a Done card on the board.
#
# The chain halts rather than starting the next card if the previous one left
# the repo dirty or left origin behind. The active pre-push hook runs the full
# gate, so a card that breaks the suite cannot reach origin.
#
#   bash plans/run-sweep.sh                 first three cards (recommended)
#   bash plans/run-sweep.sh --with-109      all four, IW-109 included
#   bash plans/run-sweep.sh IW-141 IW-140   only the cards named
#
# IW-109 is excluded by default on purpose: its brief tells the session to stop
# and comment rather than guess a design question, and an unattended run under a
# completion condition has every incentive to push through instead.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

brief="$repo_root/plans/2026-08-25-build-ready-sweep.md"
log_dir="$repo_root/plans/logs"
mkdir -p "$log_dir"

[ -f "$brief" ] || { echo "FATAL: brief not found at $brief"; exit 1; }

# Every card this script knows how to run: key, model, effort.
known=(
  "IW-83:opus:high"
  "IW-141:opus:high"
  "IW-140:sonnet:medium"
  "IW-109:opus:high"
)

cards=()
if [ "${1:-}" = "--with-109" ]; then
  cards=("${known[@]}")
elif [ $# -gt 0 ]; then
  # Explicit card keys, run in the order given on the command line.
  for want in "$@"; do
    match=""
    for entry in "${known[@]}"; do
      [ "${entry%%:*}" = "$want" ] && match="$entry"
    done
    if [ -z "$match" ]; then
      echo "FATAL: unknown card '$want'. Known: IW-83 IW-141 IW-140 IW-109"
      exit 1
    fi
    cards+=("$match")
  done
else
  # Default: the three that are safe to run unattended, in dependency order.
  cards=("${known[@]:0:3}")
fi

# dontAsk denies anything not listed rather than prompting, which is what an
# unattended run needs. Deliberately NOT bypassPermissions: this is a real
# machine with live Freshdesk and Jira credentials, not a throwaway container.
allowed="Bash,Read,Edit,Write,Grep,Glob,Skill,Agent,Task,TodoWrite,mcp__atlassian__*"
disallowed="Bash(git push --force*),Bash(git push -f*),Bash(git reset --hard*),Bash(rm -rf *),Bash(git clean -fdx*)"

check_clean() {
  local label="$1"
  git -C "$repo_root" fetch origin --quiet 2>/dev/null
  if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
    echo "HALT after $label: working tree is dirty."
    git -C "$repo_root" status --short
    return 1
  fi
  local branch head_sha main_sha master_sha
  branch="$(git -C "$repo_root" rev-parse --abbrev-ref HEAD)"
  if [ "$branch" != "main" ]; then
    echo "HALT after $label: left on branch $branch, not main."
    return 1
  fi
  head_sha="$(git -C "$repo_root" rev-parse main)"
  main_sha="$(git -C "$repo_root" rev-parse origin/main 2>/dev/null || echo none)"
  master_sha="$(git -C "$repo_root" rev-parse origin/master 2>/dev/null || echo none)"
  if [ "$head_sha" != "$main_sha" ]; then
    echo "HALT after $label: local main $head_sha does not match origin/main $main_sha (unpushed work)."
    return 1
  fi
  if [ "$master_sha" != "none" ] && [ "$head_sha" != "$master_sha" ]; then
    echo "HALT after $label: origin/master $master_sha is behind main $head_sha. Run: git push origin main:master"
    return 1
  fi
  echo "verified after $label: clean, on main at $head_sha, origin and master in sync"
  return 0
}

echo "=== pre-flight ==="
check_clean "pre-flight" || { echo "Repo is not clean before the run started. Nothing attempted."; exit 1; }

start_sha="$(git -C "$repo_root" rev-parse main)"
echo ""
echo "starting sweep at $start_sha"
echo "cards: ${cards[*]}"
echo ""

completed=()
for entry in "${cards[@]}"; do
  card_key="${entry%%:*}"
  rest="${entry#*:}"
  card_model="${rest%%:*}"
  card_effort="${rest#*:}"
  run_log="$log_dir/${card_key}.json"
  before_sha="$(git -C "$repo_root" rev-parse main)"

  echo "================================================================"
  echo "CARD $card_key   model=$card_model effort=$card_effort"
  echo "log: $run_log"
  echo "================================================================"

  prompt="Read and follow ${brief} as your full brief before acting; it names the per-card workflow, the gate, the mirror push, and the hard rules. THIS RUN HANDLES ONE CARD ONLY: ${card_key}. Ignore every other card in the brief. Not done until ${card_key} is merged to main, pushed to origin, mirrored with git push origin main:master, and the Jira card moved to Done with a comment naming the merge commit. If ${card_key} turns out to be genuinely blocked, comment the blocker on the card, leave the repo clean on main, and stop; do not improvise around it. Run /ce-compound only if this card produced a learning a future session would get wrong without it, routed to this repo's docs/solutions/."

  claude -p "$prompt" \
    --model "$card_model" \
    --effort "$card_effort" \
    --permission-mode dontAsk \
    --allowedTools "$allowed" \
    --disallowedTools "$disallowed" \
    --output-format json > "$run_log" 2>&1
  rc=$?

  if [ $rc -ne 0 ]; then
    echo ""
    echo "HALT: $card_key exited non-zero ($rc). See $run_log"
    break
  fi

  after_sha="$(git -C "$repo_root" rev-parse main)"
  if [ "$before_sha" = "$after_sha" ]; then
    echo ""
    echo "NOTE: $card_key produced no new commit on main. It may have been blocked."
    echo "Check the card and $run_log before assuming it shipped."
  fi

  echo ""
  if ! check_clean "$card_key"; then
    echo "HALT: not starting the next card on an unclean repo."
    break
  fi
  completed+=("$card_key")
  echo ""
done

echo "================================================================"
echo "SWEEP SUMMARY"
echo "================================================================"
echo "started at: $start_sha"
echo "now at:     $(git -C "$repo_root" rev-parse main)"
echo "completed:  ${completed[*]:-none}"
echo ""
echo "commits landed this sweep:"
git -C "$repo_root" log --oneline "$start_sha"..main | cat
echo ""
echo "VERIFY BY HAND before trusting this run:"
echo "  1. Each card above reads Done on the IW board with a merge commit named."
echo "     A denied Jira write is silent here: the code can merge while the card"
echo "     stays in Backlog."
echo "  2. scripts/test.sh is green (takes about 75 seconds, not the 6 the"
echo "     script comment claims)."
echo "  3. Read plans/logs/*.json for what each run actually decided."
