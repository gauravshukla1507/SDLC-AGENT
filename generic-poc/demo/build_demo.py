#!/usr/bin/env python3
"""Build the self-contained client demo page from a run's trace.json.

    python demo/build_demo.py runs/demo/trace.json demo/agentic_sdlc_demo.html
"""
import json
import sys
from pathlib import Path

trace = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2] if len(sys.argv) > 2 else "demo/agentic_sdlc_demo.html")
template = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
payload = json.dumps(trace).replace("</", "<\\/")
out.write_text(template.replace("/*__TRACE__*/", payload), encoding="utf-8")
print(f"Demo written to {out}")
