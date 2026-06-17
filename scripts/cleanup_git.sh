#!/usr/bin/env bash
# scripts/cleanup_git.sh
#
# Delete local branches that have been merged into main or superseded by later work.
# Run from the repo root after pulling main:
#
#   git checkout main && git pull
#   bash scripts/cleanup_git.sh
#
# Branches are deleted with -d (safe delete) — git will refuse to delete any
# branch that has unmerged commits.  If you get an error, check with:
#   git log main..branch-name
# before escalating to -D.
# =============================================================================

set -euo pipefail

BRANCHES_TO_DELETE=(
  # Performance fix branches — all three landed via PRs
  "perf/fix-1-neo4j-indexes"
  "perf/fix-2-backend-pagination"
  "perf/fix-3-frontend-render-cap"

  # Feature branches superseded or absorbed by later work
  "feature/byov-vectors"           # superseded by feat/issue-40-byov-endpoints
  "feature/ml-synthesizer-dpo"
  "feature/swarm-observability-heartbeat"  # absorbed into feature/ui-swarm-heartbeat
  "feature/backend-sse-swarm-log"
)

CURRENT=$(git rev-parse --abbrev-ref HEAD)

for branch in "${BRANCHES_TO_DELETE[@]}"; do
  if [ "$branch" = "$CURRENT" ]; then
    echo "SKIP  $branch  (currently checked out)"
    continue
  fi

  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git branch -d "$branch" \
      && echo "DELETED  $branch" \
      || echo "SKIPPED  $branch  (unmerged commits — use git branch -D to force)"
  else
    echo "MISSING  $branch  (already deleted or never existed)"
  fi
done

echo ""
echo "Remaining local branches:"
git branch
