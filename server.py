#!/usr/bin/env python3
"""Local API server for the browser GUI.

This file adds no estimation logic of its own -- it is a thin HTTP wrapper
around estimator/engine.py, research.py and exporters.py, which are exactly
the modules the CLI (main.py) uses. Run the CLI or the GUI; both produce
identical numbers from identical inputs, because both call the same code.

Why a backend at all, when the ask was "no dependencies to install"?
The browser can do plain chat with Ollama on its own, but it cannot run the
COCOMO/ISBSG maths deterministically, generate real .xlsx/.pdf files, or call
Ollama with retry/parsing logic -- all of that is Python. So the frontend
stays genuinely zero-install (a static HTML file, open it or serve it as-is)
and talks only to this local server. This server is the only thing that ever
talks to Ollama, which conveniently sidesteps the browser CORS problems that
a direct browser-to-Ollama design would run into.

Usage:
    uv run server.py                  # binds 127.0.0.1:8420, loopback only
    uv run server.py --port 8500
    uv run server.py --reload         # dev mode
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from estimator import research
from estimator.engine import build_estimate
from estimator.exporters import to_excel, to_pdf
from estimator.schema import (
    DeliveryModel,
    EngagementType,
    Estimate,
    ProjectBrief,
    Purpose,
    Rating,
    TechProfile,
)

OUTPUT_DIR = Path("output")
OLLAMA_URL = "http://localhost:11434"

app = FastAPI(title="Effort Estimator API", version="0.1.0")

# The frontend is a plain static file, so it may be opened as file:// (origin
# "null") or served from any local port. This server only ever binds to
# 127.0.0.1, so the permissive CORS policy below does not expose it beyond
# this machine -- it just avoids fighting the browser over where the HTML
# happened to be opened from.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store. This is a single-user local tool with no auth story;
# a database would be solving a problem this tool doesn't have. Restarting
# the server clears the list, but files already written to output/ remain.
JOBS: dict[str, Estimate] = {}


# --------------------------------------------------------------------------
# Request/response shapes
# --------------------------------------------------------------------------

class EstimateOptions(BaseModel):
    use_web: bool = True
    use_llm: bool = True
    model: str = "qwen3:8b"


class EstimateRequest(BaseModel):
    brief: ProjectBrief
    options: EstimateOptions = EstimateOptions()


# --------------------------------------------------------------------------
# Meta / health
# --------------------------------------------------------------------------

LABEL_OVERRIDES = {
    "ptk": "PTK (Project Takeover)",
    "staff_aug": "Staff Augmentation",
    "package_cots": "Package / COTS",
    "low_code": "Low-Code Platform",
}


def _enum_options(enum_cls) -> list[dict]:
    return [
        {"value": e.value, "label": LABEL_OVERRIDES.get(e.value, e.value.replace("_", " ").title())}
        for e in enum_cls
    ]


@app.get("/api/meta")
def meta():
    """Enum choices for the intake form, sourced live from schema.py so the
    GUI can never drift out of sync with the data model."""
    return {
        "engagement_types": _enum_options(EngagementType),
        "purposes": _enum_options(Purpose),
        "tech_profiles": _enum_options(TechProfile),
        "delivery_models": _enum_options(DeliveryModel),
        "ratings": _enum_options(Rating),
    }


@app.get("/api/health")
def health():
    """Reachability of this server (trivially yes, you got a response) and
    of Ollama, plus whatever models are currently pulled."""
    ollama_ok = False
    models: list[str] = []
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            ollama_ok = True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        pass
    return {"backend": "ok", "ollama": ollama_ok, "models": models}


# --------------------------------------------------------------------------
# Estimation
# --------------------------------------------------------------------------

@app.post("/api/estimate")
def estimate(req: EstimateRequest):
    llm = None
    if req.options.use_web or req.options.use_llm:
        try:
            llm = research.get_llm(req.options.model)
        except Exception:  # noqa: BLE001
            llm = None  # degrade to published benchmarks, same as the CLI does

    try:
        calibration = research.calibrate(
            req.brief, llm=llm, use_web=req.options.use_web and llm is not None,
        )
        est = build_estimate(req.brief, calibration)
        if llm is not None and req.options.use_llm:
            est.narrative = research.write_narrative(est, llm=llm)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Estimation failed: {exc}") from exc

    job_id = uuid4().hex[:10]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        to_excel(est, job_dir / "estimate.xlsx")
        to_pdf(est, job_dir / "estimate.pdf")
        (job_dir / "estimate.json").write_text(est.model_dump_json(indent=2))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

    JOBS[job_id] = est
    return {"id": job_id, "estimate": json.loads(est.model_dump_json())}


@app.get("/api/estimate/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str):
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(status_code=400, detail="fmt must be xlsx or pdf")
    path = OUTPUT_DIR / job_id / f"estimate.{fmt}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Estimate not found")
    name = JOBS.get(job_id)
    slug = "".join(c if c.isalnum() else "_" for c in name.brief.project_name).strip("_").lower() \
        if name else job_id
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             if fmt == "xlsx" else "application/pdf")
    return FileResponse(path, filename=f"{slug}_estimate.{fmt}", media_type=media)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Effort estimator API server")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"\n  Effort Estimator API on http://127.0.0.1:{args.port}")
    print(f"  Open frontend/index.html in a browser, or serve it:")
    print(f"    cd frontend && python3 -m http.server 5500\n")

    # Loopback only -- deliberately not 0.0.0.0. This tool has no auth and
    # should never be reachable from anything but this machine.
    uvicorn.run("server:app", host="127.0.0.1", port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
