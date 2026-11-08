"""
Create the first organization and owner, for operators who prefer not to set
SPAIDER_BOOTSTRAP_TOKEN and call POST /api/v1/auth/bootstrap.

    python -m scripts.bootstrap_admin --org "Acme" --owner "Ops"

Works once per database. The owner's API key is printed exactly once.
"""
from __future__ import annotations

import argparse
import asyncio

from app.core.schema import FOUNDATION_SCHEMA
from app.services.graph_service import get_graph_service
from app.services.identity_service import AlreadyBootstrapped, IdentityService


async def _bootstrap(org: str, owner: str, email: str | None) -> int:
    graph = get_graph_service()
    try:
        async with graph._driver.session() as session:
            for statement in FOUNDATION_SCHEMA:
                await session.run(statement)
        created = await IdentityService(graph._driver).bootstrap(org, owner, email)
    except AlreadyBootstrapped as exc:
        print(exc)
        return 1
    finally:
        await graph.close()
    print(f"Organization: {created['org_id']}")
    print(f"Owner:        {created['user_id']}")
    print(f"API key:      {created['api_key']}")
    print("Store this key now. It will not be shown again.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--org", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--email")
    args = parser.parse_args()
    return asyncio.run(_bootstrap(args.org, args.owner, args.email))


if __name__ == "__main__":
    raise SystemExit(main())
