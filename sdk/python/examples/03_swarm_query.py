"""Cross-agent swarm queries.

Each agent has an isolated graph by default — agent-hr can't see agent-sales'
data. The swarm primitive lets you opt into a directed connection from one
agent to another, then query across both graphs as if they were one.

This is how Spaider models "departments share some context but stay
namespaced by default" rather than the everyone-sees-everything pattern
that single-database memory layers fall into.

    export SPAIDER_API_KEY=sk-...
    export SPAIDER_BASE_URL=http://localhost:8080
    python 03_swarm_query.py
"""

import os

from spaider import Spaider


def main() -> None:
    api_key = os.environ["SPAIDER_API_KEY"]
    base_url = os.environ.get("SPAIDER_BASE_URL", "http://localhost:8080")

    # Two separate agents.
    hr = Spaider(api_key=api_key, agent_id="examples-03-hr", base_url=base_url)
    sales = Spaider(api_key=api_key, agent_id="examples-03-sales", base_url=base_url)

    try:
        # HR knows the people side.
        hr.ingest("Carol Reed manages the customer-success team.")
        hr.ingest("Carol Reed reports to the VP of Operations.")

        # Sales knows the account side.
        sales.ingest("Acme Corp is our largest client by ARR.")
        sales.ingest("Acme Corp's account manager is Carol Reed.")

        # Connect HR → Sales so HR's queries can reach Sales' graph.
        hr.create_swarm_connection(target_agent="examples-03-sales")

        # Query across both. The answer should weave Carol's HR role with her
        # account ownership from the Sales graph.
        result = hr.swarm_query(
            "Who manages our largest client, and who do they report to?",
            target_agents=["examples-03-sales"],
            top_k=5,
        )

        print("Swarm query: 'Who manages our largest client, and who do they report to?'")
        print(f"\nAnswer:\n  {result.text}\n")
        print(f"Agents consulted: {', '.join(result.agents_queried)}")
    finally:
        hr.close()
        sales.close()


if __name__ == "__main__":
    main()
