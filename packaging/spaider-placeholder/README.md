# spaider (reserved name)

This PyPI distribution reserves the short name `spaider`. It ships **no code** —
it simply depends on [`spaider-client`](https://pypi.org/project/spaider-client/),
the actual SpAIder SDK, which provides the importable `spaider` package:

```bash
pip install spaider          # pulls in spaider-client
# or, equivalently:
pip install spaider-client
```

```python
from spaider import Spaider
```

Because this package ships no module of its own, installing it alongside
`spaider-client` (in any order) never shadows the SDK.

See the main project: https://github.com/Spaider-studio/spaider
