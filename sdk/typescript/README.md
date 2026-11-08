# @spaider/client

```ts
import { SpaiderClient } from "@spaider/client";

const memory = new SpaiderClient({ baseUrl: "http://localhost:8000", apiKey: "ss-...", agentId: "kb" });
await memory.remember("the charger is in bay four");
const { results } = await memory.recall("charger");
const answer = await memory.query("Where is the charger?");
await memory.feedback(answer.query_id!, true);
```

No dependencies. Needs a global `fetch` (Node 18 or later, or a browser).
