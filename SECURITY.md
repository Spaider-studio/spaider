# Security policy

## Reporting a vulnerability

Please **do not open a public GitHub issue** for security vulnerabilities.

Send a report to **security@spaider.studio** with:

- A description of the issue and its impact
- Steps to reproduce, including any required configuration
- The version of SpAIder you were running (commit SHA is best)
- Your name and any preferred attribution if we publish a fix

We will:

1. Acknowledge receipt within **3 working days**.
2. Investigate and confirm or reject the report within **14 days**.
3. If confirmed: develop and ship a fix on a non-public branch, coordinate disclosure with you, and publish a security advisory once the fix is available.
4. Credit you in the advisory unless you ask us not to.

## Disclosure window

We follow a **90-day coordinated disclosure** model. If we have not shipped a fix within 90 days of confirming the report, you are free to disclose publicly. We will do our best to be much faster than that.

## Scope

In scope:
- The SpAIder backend (`backend/`), worker, and MCP server
- The frontend application (`frontend/`)
- The benchmarks harness (`benchmarks/`)
- Build and release infrastructure under `.github/`

Out of scope:
- Vulnerabilities in third-party dependencies; please report those upstream. We will pin / patch promptly once upstream fixes land.
- Issues that require physical access to a developer machine
- Social-engineering reports against contributors

## Supported versions

Until we ship a 1.0 release, only `main` is supported. Security fixes are applied to `main` and the most recent tagged release.

Thank you for helping keep SpAIder safe.
