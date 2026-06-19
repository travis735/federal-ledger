#!/usr/bin/env bash
#
# Publish The Ledger to Cloudflare Pages.
#
# ⚠️  READ THIS: federal-ledger is a DIRECT-UPLOAD Pages project.
#     `git push` updates the SOURCE on GitHub but does NOT deploy the live site.
#     The site at https://federal-ledger.pages.dev only changes when you run THIS.
#
# Usage:  ./deploy.sh        (after editing index.html / data.json, etc.)
#
# If it says "not authenticated", run `npx wrangler login` once (opens a browser),
# then run ./deploy.sh again. The login is global and lasts a while.
#
set -euo pipefail
cd "$(dirname "$0")"

# Stage ONLY the site assets into a temp dir, so README / LICENSE / scripts / the
# demo video can never end up on the live site no matter what's in the repo root.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp index.html data.json og.png "$STAGE"/

npx wrangler pages deploy "$STAGE" --project-name=federal-ledger --branch=main --commit-dirty=true
echo "✓ Deployed → https://federal-ledger.pages.dev/"
