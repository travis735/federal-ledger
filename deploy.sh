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
set -euo pipefail
cd "$(dirname "$0")"
npx wrangler pages deploy . --project-name=federal-ledger --branch=main --commit-dirty=true
