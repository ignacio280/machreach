#!/usr/bin/env bash
# `npm audit` fails for two very different reasons and reports both as exit 1:
# a package with a known advisory, and npm's own registry refusing to answer.
# Only the first is a reason to stop a deploy, and with
# `autoDeployTrigger: checksPass` in render.yaml, stopping this job stops the
# deploy -- so a registry hiccup was taking production changes down with it.
#
# Retry the transport failures. Never retry a finding: if npm answered and the
# answer was "there is an advisory", that result stands and the job fails.
set -uo pipefail

attempts=${NPM_AUDIT_ATTEMPTS:-3}

transport_failure() {
  grep -qiE 'audit endpoint returned an error|ENOTFOUND|ECONNRESET|ETIMEDOUT|EAI_AGAIN|socket hang up|Bad Request|50[0-9] |429 ' <<<"$1"
}

for attempt in $(seq 1 "$attempts"); do
  output=$(npm audit --audit-level=moderate 2>&1)
  status=$?
  printf '%s\n' "$output"

  if [ "$status" -eq 0 ]; then
    exit 0
  fi

  if ! transport_failure "$output"; then
    echo "npm audit reported findings. That is a real result, not a flake." >&2
    exit "$status"
  fi

  if [ "$attempt" -lt "$attempts" ]; then
    echo "::warning::npm audit could not reach the registry (attempt ${attempt}/${attempts}); retrying."
    sleep $(( attempt * 20 ))
  fi
done

echo "npm audit never reached the registry in ${attempts} attempts. This is npm," >&2
echo "not this repository -- but it is also not evidence that the tree is clean," >&2
echo "so the job fails rather than pretend it audited anything." >&2
exit 1
