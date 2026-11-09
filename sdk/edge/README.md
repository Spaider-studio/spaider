# spaider-edge

Memory that runs on the robot. Episodes are stored in SQLite with an R-tree for
spatial lookups and hash embeddings for text similarity, so `remember` and
`recall` work with no network and no model. When a connection is available,
`sync` pushes episodes to a SpAIder server. Pushes are idempotent, so a dropped
link only means a retry.

```python
from spaider_edge import EdgeMemory, HttpTransport

memory = EdgeMemory("robot.db")
memory.remember("charger is behind the blue door", location={"x": 4.2, "y": 1.0})
memory.recall("where is the charger")
memory.recall(near={"x": 4.0, "y": 1.0}, radius=2.0)

memory.sync(HttpTransport("https://spaider.example", api_key="ss-..."), agent_id="robot-kb")
```

Hash embeddings capture shared words and spelling, not meaning, so "car" and
"automobile" do not match. They are identical to the vectors the server computes
for episodes.

## LeRobot datasets

`spaider_edge.lerobot` reads and writes the metadata of a LeRobot v2 dataset
(`meta/info.json`, `meta/episodes.jsonl`, `meta/tasks.jsonl`). Each dataset episode
becomes one memory episode describing its task. Frames and video are not imported.

This has been tested only against generated fixtures that follow the published
layout, not against a dataset produced by LeRobot itself.
