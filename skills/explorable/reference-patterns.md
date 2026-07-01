# Reference patterns

Proven code patterns from a battle-tested explorable (an LSM-tree visualiser: memtable + WAL + sorted segments, animated read path, live compaction). Port these; don't reinvent. Python/FastAPI shown, but the shapes are framework-agnostic.

## 1. Traced reads — animations replay real execution

The single most important pattern. The backend's read path returns a step-by-step trace of which sources it consulted; the frontend animates *that*, so the animation can never drift from the actual algorithm.

```python
TraceStep = dict[str, str]  # {"source": "memtable", "result": "value" | "tombstone" | "miss"}

class LSMTree:
    def get(self, key: str) -> str | None:
        value, _ = self._read(key)
        return value

    def get_with_trace(self, key: str) -> tuple[str | None, list[TraceStep]]:
        return self._read(key)

    def _read(self, key: str) -> tuple[str | None, list[TraceStep]]:
        trace: list[TraceStep] = []
        for source_name, source in self._read_sources():  # newest first
            record = source.get(key)
            if record is None:
                trace.append({"source": source_name, "result": "miss"})
                continue
            trace.append({"source": source_name, "result": "value"})
            return record.value, trace
        return None, trace
```

`get` and `get_with_trace` share `_read`, so the two paths cannot diverge. Mutations that restructure state (e.g. a B-tree split) similarly return event objects (`SplitEvent(node_id, median_key, …)`) the frontend replays.

## 2. State-snapshot API — mutations return the new state inline

Every mutating endpoint returns `{message, state}` where `state` is a full snapshot. The frontend re-renders from each response; no polling, no cache invalidation.

```python
def _state_snapshot() -> dict:
    return {"memtable": [...], "wal": [...], "segments": [...], "config": {...}}

@app.post("/api/put")
def put(req: PutRequest) -> dict:
    db.put(req.key, req.value)
    return {"message": "WAL append → memtable insert", "state": _state_snapshot()}
```

The `message` is a human-readable narration of what just happened — it feeds the event log (principle: two channels, picture + words).

## 3. Reverse-proxy-ready serving (the root_path trap)

Behind a path-stripping proxy (e.g. serving at `/myapp/`), requests arrive at the app *without* the prefix. Setting `root_path` on `FastAPI(...)` changes route *matching* and breaks this. Set it on the ASGI server instead, and inject a `<base>` tag so the frontend's relative URLs resolve:

```python
ROOT_PATH = os.environ.get("APP_ROOT_PATH", "")  # e.g. "/myapp"

app = FastAPI()  # deliberately NO root_path here

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    root = request.scope.get("root_path", "") or ROOT_PATH
    base_href = (root.rstrip("/") + "/") if root else "/"
    html = (WEB_DIR / "index.html").read_text()
    return HTMLResponse(html.replace("<head>", f'<head>\n<base href="{base_href}">', 1))

uvicorn.run(app, host="127.0.0.1", port=PORT, root_path=ROOT_PATH)  # scope hint only
```

Frontend: every fetch and asset URL is **relative** (`fetch("api/state")`, `<script src="static/app.js">`) so the same code runs at `/` and at any subpath.

## 4. Cycling defaults — zero-typing interaction

```js
const DEFAULT_KEYS = ["apple", "banana", "cherry", /* … alphabetical, 24 items */];
let putIndex = 0;

function fillPutDefaults() {
  $("#put-key").value = defaultKey(putIndex);    // wraps with suffix: apple2, apple3…
  $("#put-value").value = `v${String(putIndex + 1).padStart(2, "0")}`;
}

// After every successful write:
putIndex++; fillPutDefaults();
$("#get-key").value = justWrittenKey;      // read fields pre-fill too
$("#delete-key").value = justWrittenKey;
// After seed: pre-fill lookup with a MID-sequence key (demonstrates a full descent)
// After reset: putIndex = 0; fillPutDefaults(); clear read fields
```

For numeric keys use a hand-shuffled deterministic spread (`[42, 17, 88, 5, 63, …]`) so structural events (splits, rebalances) arrive at a pleasant rhythm — sorted is boring, random is confusing.

## 5. Trace animation + event log

CSS state classes on the component cards, applied stepwise from the backend trace:

```css
.tier.checking { border-color: var(--checking); box-shadow: 0 0 0 2px rgba(240,160,112,.25); }
.tier.miss     { opacity: 0.45; }
.tier.found    { border-color: var(--found); box-shadow: 0 0 0 2px rgba(86,211,100,.35); }
```

```js
async function animateTrace(trace) {
  for (const step of trace) {
    const el = findSourceEl(step.source);   // data-source attributes on cards
    el.classList.add("checking");
    await sleep(600);
    el.classList.remove("checking");
    el.classList.add(step.result === "miss" ? "miss" : "found");
  }
  await sleep(1200);  // let the result land
  clearTraceClasses();
}
```

Event log: append-only `<ul>` with `flex-direction: column-reverse` (newest visually on top, append to DOM end), one color-coded entry per operation, capped (~80 entries). For irreversible structural animations, controls are pause / step (advance one frame while paused) / skip — backward scrub only if steps replay cheaply.

## 6. Layout skeleton

CSS grid with named areas — the "triptych" is conceptual, proportions follow the subject:

```css
main {
  display: grid;
  grid-template-columns: 1fr 360px;
  grid-template-areas: "ops log" "state log";
}
```

Color-code each component tier with a left border stripe + matching heading color (e.g. blue = in-RAM, amber = durability, green = on-disk) and put a capacity/threshold meter in each card header (`2/4`) so pressure toward the next state transition is always visible.
