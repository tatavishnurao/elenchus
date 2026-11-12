#!/usr/bin/env bash
# Rejects text that carries tool or assistant attribution.
#
# Usage:
#   check_attribution.sh msg FILE        check one commit message file (comment lines ignored)
#   check_attribution.sh range BASE HEAD check every commit message in BASE..HEAD
#   check_attribution.sh text            check stdin (for PR titles and bodies)
#   check_attribution.sh tree            check every non-ignored file in the working tree
#
# Each term below has one character in a bracket so this file does not match
# its own pattern when the repo is searched for the same terms.
set -euo pipefail

PATTERN='co-authored-b[y]|generated wit[h]|cl[a]ude|anthropi[c]|cl[a]ude\.ai/code'

fail() {
  echo "attribution check failed: $1" >&2
  echo "$2" >&2
  exit 1
}

mode="${1:-}"
case "$mode" in
  msg)
    file="${2:?message file required}"
    hits=$(grep -v '^#' "$file" | grep -Ei -- "$PATTERN" || true)
    [ -z "$hits" ] || fail "commit message" "$hits"
    ;;
  range)
    base="${2:?base required}"
    head="${3:?head required}"
    bad=0
    for sha in $(git rev-list "$base..$head"); do
      hits=$(git log -1 --format=%B "$sha" | grep -Ei -- "$PATTERN" || true)
      if [ -n "$hits" ]; then
        echo "commit $sha:" >&2
        echo "$hits" >&2
        bad=1
      fi
    done
    [ "$bad" -eq 0 ] || fail "commits in $base..$head" "see above"
    ;;
  text)
    hits=$(grep -Ei -- "$PATTERN" || true)
    [ -z "$hits" ] || fail "text" "$hits"
    ;;
  tree)
    hits=$(git ls-files -co --exclude-standard -z | xargs -0 grep -IEin -- "$PATTERN" || true)
    [ -z "$hits" ] || fail "working tree" "$hits"
    ;;
  *)
    echo "usage: $0 {msg FILE | range BASE HEAD | text | tree}" >&2
    exit 2
    ;;
esac
