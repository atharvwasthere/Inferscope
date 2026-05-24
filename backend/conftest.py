"""Pytest bootstrap: put the backend dir on sys.path so tests can `import sdk`,
`import ingestion`, etc. exactly as the services do (CWD=backend convention)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
