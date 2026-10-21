"""Pytest bootstrap: put the backend dir on sys.path so tests can `import sdk`,
`import ingestion`, etc. exactly as the services do (CWD=backend convention).

`inferscope` is deliberately NOT on this path — it is a real installed package
(`pip install -e .` from the repo root). That is the point of T1."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# The service modules read DATABASE_URL at import time. Nothing here opens a
# connection — a placeholder is enough to import them. setdefault so a real
# environment still wins.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
# The collector fails closed without keys (T3a), so importing it needs one.
os.environ.setdefault("INFERSCOPE_API_KEYS", "test-key")
