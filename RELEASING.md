# Releasing

How a change travels from `main` to PyPI, GHCR, and the Releases page.
Versioning policy (SemVer 2.0.0, pre-1.0 rules) lives in
[CONTRIBUTING.md](CONTRIBUTING.md#versioning-policy-semver); this file is the mechanics.

## The model in one paragraph

Merging to `main` never publishes anything; it only runs CI. A release is an
explicit act: a **tag** marks the exact commit being released, and the tag's
namespace routes it to the right pipeline. For the Python packages the tag is
created **automatically** when you merge a version bump (see below). For the
product/containers it is a deliberate manual step, because that is also the
moment you publish release notes.

| Artifact | Registry | Tag | How the tag happens |
|---|---|---|---|
| `spaider-cli` | PyPI | `cli/vX.Y.Z` | automatic, on merging a bump of `cli/pyproject.toml` |
| `spaider-client` | PyPI | `sdk-python/vX.Y.Z` | automatic, on merging a bump of `sdk/python/pyproject.toml` |
| `spaider` (name-squat stub) | PyPI | `sdk-python-stub/vX.Y.Z` | automatic, on merging a bump of `sdk/python-stub/pyproject.toml` |
| Container images (backend, worker, frontend) | GHCR | `vX.Y.Z` | manual, via the Releases UI |

## Releasing a Python package

1. Open a PR that bumps `version` in the package's `pyproject.toml` (no `v`
   prefix there; PEP 440 forbids it) and moves the relevant `[Unreleased]`
   bullets in the changelog under the new version.
2. Merge it. The **Release / Auto-tag** workflow notices the manifest change,
   creates the namespaced tag on the merge commit, and starts the package's
   release workflow with publishing enabled. Done.

Safety properties: the workflow only acts when the version in the manifest has
no existing tag, so touching a pyproject without bumping releases nothing, and
re-runs can never double-publish. Tags are immutable: the same version can
never silently point at different code.

## Releasing the product (containers + release notes)

1. Go to **Releases → Draft a new release**.
2. Type the new tag (`v0.2.0`), target `main`, click **Generate release
   notes**. Notes are assembled from every PR merged since the last `v*` tag,
   categorized by label (labels are applied automatically from conventional
   PR titles by the PR Labeler workflow; categories are defined in
   `.github/release.yml`).
3. Skim, edit the headline if you like, **Publish release**. Publishing
   creates the tag, which fires **Release / Containers**: three images pushed
   to GHCR tagged `X.Y.Z`, `X.Y`, and the commit SHA.

Tagging from the terminal works identically (`git tag v0.2.0 <sha> && git
push origin v0.2.0`); you just won't get the notes page until you draft one.

## Rehearsing

Every release workflow has a **Run workflow** button whose `dry_run` input
defaults to `true`: it builds and checks everything but skips the upload.
Type `false` only when you deliberately want the button to publish.

## Changelog discipline

- Every user-visible PR should add a bullet under `[Unreleased]` in
  `CHANGELOG.md` (or the SDK's own changelog for SDK changes).
- At release time the bump PR renames `[Unreleased]` to `[X.Y.Z] - date`.
- The Releases page is generated from PR titles; the CHANGELOG is the curated
  long-term history. They serve different readers; keep both.

## One-time setup before the first release

1. PyPI pending publishers (Owner `Spaider-studio`, repo `spaider`,
   environment `pypi`): `spaider-cli` ↔ `cli-release.yml`, `spaider-client` ↔
   `sdk-python-release.yml`, `spaider` ↔ `sdk-python-stub-release.yml`.
2. Create the `pypi` environment under Settings → Environments (optionally
   with a required reviewer to gate every publish behind a click).
3. Until both exist, PyPI publishes fail at the auth step; GHCR pushes work
   immediately (built-in `GITHUB_TOKEN`), so do not push `v*` tags early.

## Troubleshooting

- **Tag exists but nothing published** (e.g. the dispatch step failed after
  tagging): run the package's release workflow manually via **Run workflow**,
  ref = the tag, `dry_run=false`.
- **Wrong version tagged**: never re-point a published tag. Bump again
  (`X.Y.Z+1`) and release forward.
