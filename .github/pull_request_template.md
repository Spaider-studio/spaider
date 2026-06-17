<!-- See CLAUDE.md at the repo root for the full protocol. -->

## Summary

<!-- 1–3 bullets explaining what changed and why. Link the issue this closes. -->

- 

Closes #

## Pre-PR checklist (don't open the PR until every box is honestly checked)

- [ ] Rebased on `origin/main` with `git rebase` (not `git merge`)
- [ ] Issue claim posted on the linked issue (if applicable) before coding started
- [ ] No other open PR is touching the same hot files (see `CLAUDE.md` §3)
- [ ] `make lint test` green locally
- [ ] `docker compose up` smoke covers the new behaviour (UI screenshots if frontend changed)
- [ ] No duplicate response/request classes: `grep -rnE "^class [A-Z][A-Za-z]+(Request\|Response)\b" backend/app | sort | uniq -d` is empty
- [ ] Every new `async` helper is `await`ed at every call site
- [ ] If this PR adds a connector: subclasses `app.connectors.BaseConnector`, registered in `_connector_registry` in `backend/app/api/v1/ingest.py`, and the new `/ingest/<connector_id>` endpoint mirrors `/ingest/url` shape
- [ ] Tests exist for the happy path AND one error/edge path

## Test plan

<!-- A bulleted list of what you actually ran. -->

- [ ] 

## Screenshots / recording (UI changes only)

<!-- Drop screenshots or short Loom links here. -->

## Notes for the reviewer

<!-- Edge cases you considered, alternatives you ruled out, follow-up issues you'll file. -->
