#!/usr/bin/env bash
# Fills JWT_SECRET, CODE_PEPPER and TURN_SECRET in .env with fresh random values.
#
# No external service or API key is involved: these are just random strings this
# process generates locally (see docs/security.md, "Secrets and error handling").
# Existing non-empty values are left untouched, so this is safe to re-run.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -f .env ]]; then
    echo "No .env found; creating one from .env.example." >&2
    cp .env.example .env
fi

gen() {
    python3 -c "import secrets; print(secrets.token_urlsafe(32))"
}

set_secret() {
    local key="$1"
    local current
    current="$(grep -E "^${key}=" .env | head -n1 | cut -d= -f2-)"
    if [[ -n "$current" ]]; then
        echo "${key} already set, leaving it alone."
        return
    fi
    local value
    value="$(gen)"
    # BSD and GNU sed differ on -i; write to a temp file instead for portability.
    awk -v key="$key" -v val="$value" -F= '
        BEGIN { OFS = "=" }
        $1 == key { print key, val; next }
        { print }
    ' .env > .env.tmp && mv .env.tmp .env
    echo "${key} generated."
}

set_secret JWT_SECRET
set_secret CODE_PEPPER
set_secret TURN_SECRET

echo "Done. Secrets are in .env (git-ignored) — never commit this file."
