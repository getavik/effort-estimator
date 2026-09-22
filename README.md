# Project Effort Estimation Agent

Generates a defensible effort estimate, resource loading plan, and milestone
schedule from a short project brief, and exports it as Excel, PDF, and JSON.

## Design principle

The LLM does not produce the number.

```
intake  ──►  research (LLM + web, bounded)  ──►  engine (pure maths)  ──►  exporters
              proposes a delivery rate            COCOMO II + ISBSG
              clamped to ±40% of published        fully deterministic
```

A model asked to "estimate this project" will give you a different, confident,
unsourced answer every run. Instead:

- **The engine is deterministic.** Same brief in, same numbers out, always.
- **The LLM's only quantitative input** is a single delivery-rate figure, and it
  is hard-clamped to within ±40% of the published ISBSG median. If it proposes
  something outside that band, the clamp fires and says so in the output.
- **Everything is logged.** The assumptions sheet shows both estimation paths,
  the multipliers applied, and every adjustment with its source URL.

## Models used

| Model | Role | Key constants |
|---|---|---|
| NESMA indicative FP | Early-stage sizing from module/interface counts | 35 FP per data store, 15 per interface |
| ISBSG Project Delivery Rate | Primary effort path | Traditional median 8.3 h/FP; low-code 1.8 h/FP |
| COCOMO II Post-Architecture | Cross-check + schedule | A=2.94, B=0.91, C=3.67, D=0.28, 152 h/PM |
| COCOMO II SCED | Schedule-compression penalty | 75% compression → 1.43× effort; below 75% infeasible |

Support and staff-augmentation engagements bypass the parametric path entirely —
they are capacity problems (ticket volume × handling time, or FTE × months),
and estimating them with COCOMO would be a category error.

## Setup

```bash
cd ~/Developer/ai/effort-estimator   # wherever you placed this folder
uv sync                              # installs everything from pyproject.toml

ollama pull qwen3:8b      # any tool-capable model works
```

## Run — command line

```bash
uv run main.py                 # interactive intake
uv run main.py --demo          # canned brief, no questions
uv run main.py --no-web        # skip live research, use published benchmarks
uv run main.py --no-llm        # numbers only, no commentary
uv run main.py --refresh       # bypass the 7-day research cache
uv run main.py --model llama3.1:8b
uv run main.py --brief saved.json
```

Outputs land in `output/`:

- `<project>_<date>.xlsx` — 7 sheets: Summary, Effort by Phase, Resource Loading
  (month-by-month FTE grid with a staffing curve chart), Milestones, Gantt,
  Assumptions & Risks, Sizing Detail
- `<project>_<date>.pdf` — 4-page client-facing pack
- `<project>_<date>.json` — the full estimate object; feed it back in with `--brief`

```bash
open output    # reveals in Finder
```

## Run — browser GUI

```bash
./start.sh
```

Opens `http://localhost:5500` and starts the API on `:8420` in one go. Ctrl-C
stops both. Or run the two pieces yourself:

```bash
uv run server.py                        # API on http://127.0.0.1:8420
cd frontend && python3 -m http.server 5500   # in a second terminal
```

Then open `http://localhost:5500`, or just double-click `frontend/index.html`
— it works from `file://` too, since the frontend is a single static file
with no build step.

### How the GUI is put together

```
frontend/index.html  (React + Babel, loaded as plain <script> tags)
        │  fetch()
        ▼
server.py             (FastAPI, binds 127.0.0.1 only)
        │  imports directly — no duplicated logic
        ▼
estimator/engine.py, research.py, exporters.py   ← same code the CLI uses
        │
        ▼
Ollama on localhost:11434
```

The frontend never talks to Ollama directly. That's deliberate: a browser tab
calling `localhost:11434` from a page served anywhere but `localhost` runs
into CORS and mixed-content restrictions, and even from localhost it still
can't run the COCOMO/ISBSG maths, generate real `.xlsx`/`.pdf` files, or
parse an LLM's JSON reliably — all of that is Python. So `server.py` is the
only thing that talks to Ollama, and it's the same `research.py` the CLI
uses, so GUI and CLI runs of the same brief produce identical numbers.

`frontend/index.html` has no `npm install`, no bundler, no `node_modules` —
React, ReactDOM and Babel's JSX transform load from a CDN as plain `<script>`
tags, and Babel transforms the JSX in the browser on page load. That's the
entire "zero dependencies" story on the frontend; `server.py` still needs
`uv sync` once, same as the CLI always has.

## Tuning it to your organisation

This is the part that matters. The defaults are public industry medians; your
own delivery actuals will beat them every time. Everything tunable lives in
`estimator/benchmarks.py`:

| Constant | What it controls |
|---|---|
| `PDR_BY_TECH` | Delivery rate percentiles per technology. **Replace first.** |
| `PDR_BLEND_WEIGHT` | Trust in the ISBSG path vs COCOMO (default 0.65) |
| `FP_PER_MODULE` etc. | How coarse scope counts convert to function points |
| `ENGAGEMENT_EQUIVALENCE` | How much of a greenfield build each engagement type represents |
| `PURPOSE_MULTIPLIER` | Modernization overhead, migration discount, and so on |
| `PHASE_PROFILES` | Effort split across phases per delivery model |
| `ROLE_MIX` | Role composition per phase — drives the resource plan |
| `COVERAGE_FTE_FACTOR` | Shift rota headcount for support engagements |

Calibration loop: run the estimator against three finished projects, compare to
actuals, adjust `PDR_BY_TECH` until the error is under 20%, then trust it.

## Reading the output critically

Two flags are worth taking seriously:

**Path divergence.** If the assumptions sheet says the two estimation paths
disagree by more than 2.5×, the point estimate is weakly supported — quote the
range instead. This usually means the FP-to-SLOC gearing factor in
`SLOC_PER_FP` is wrong for the stack.

**Schedule infeasibility.** COCOMO II treats compression below 75% of the
nominal duration as unachievable at any staffing level — past that point adding
people lengthens the schedule. When the tool says INFEASIBLE it is not being
conservative; it is telling you the only lever left is scope.

## Structure

```
effort-estimator/
├── main.py                   # CLI: intake, orchestration, export
├── server.py                 # FastAPI: exposes the same pipeline over HTTP
├── start.sh                  # one command: API + static server + browser
├── frontend/
│   └── index.html            # React GUI, zero build step, talks to server.py
└── estimator/
    ├── schema.py              # Pydantic models — the contract between layers
    ├── benchmarks.py          # all tunable constants
    ├── engine.py              # deterministic maths, no LLM
    ├── research.py            # LLM + web, bounded and clamped
    └── exporters.py           # Excel and PDF
```

## Extending it

- **Monte Carlo.** Sample PDR from the percentile distribution 10k times to get
  a real P50/P80 rather than a three-point estimate. ~30 lines in `engine.py`.
- **Rayleigh staffing curve.** `build_resource_plan` spreads effort evenly
  within a phase; real curves ramp and taper.
- **Historical calibration.** Load past projects from CSV and auto-fit
  `PDR_BY_TECH` by peer group.
- **LangGraph orchestration.** The current flow is a linear pipeline. Turn it
  into a graph with a critic node that challenges the estimate and loops back.
- **Better search.** Swap DuckDuckGo for Tavily in `research.web_search` — the
  return shape is the only contract.

## Caveats

Indicative estimate, not a fixed-price commitment. Uses public benchmark medians
until you replace them with your own data. The web research step depends on
DuckDuckGo, which rate-limits; the tool degrades to published defaults when it
fails, and says so.
