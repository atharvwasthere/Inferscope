"""Pytest bootstrap: put the backend dir on sys.path so tests can `import sdk`,
`import ingestion`, etc. exactly as the services do (CWD=backend convention).

`inferscope` is deliberately NOT on this path — it is a real installed package
(`pip install -e .` from the repo root). That is the point of T1."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
