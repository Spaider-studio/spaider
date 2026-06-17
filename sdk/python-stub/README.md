# spaider (placeholder)

This package is a **reserved placeholder** on PyPI. The actual Spaider Memory Infrastructure Python SDK is published as [`spaider-client`](https://pypi.org/project/spaider-client/).

Why this exists: PyPI doesn't reserve names by metadata alone, so we publish a 50-line stub here to prevent third-party squatters from grabbing the bare `spaider` name and shipping a malicious package that someone might `pip install` by mistake.

## Install the real SDK

```bash
pip install spaider-client
```

```python
from spaider import Spaider

sp = Spaider(api_key="sk-...", agent_id="my-agent")
result = sp.ingest("Max arbeitet bei Google.")
answer = sp.query("Wo arbeitet Max?")
```

`import spaider` from **this** placeholder will raise `RuntimeError` with a redirect message; that's intentional.

Links:
- Real SDK: <https://pypi.org/project/spaider-client/>
- Source: <https://github.com/Spaider-studio/spaider>
