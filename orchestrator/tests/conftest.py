import os

# The recorded end-to-end fixtures under tests/fixtures/<scenario> were made with the classic playbook; the
# spec-driven tests choose their playbook explicitly.
os.environ.setdefault("AGENTIC_PLAYBOOK", "universal")
