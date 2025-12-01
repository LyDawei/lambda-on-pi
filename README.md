
# lambda-on-pi

Minimal FastAPI-based function runner that lets you invoke small Python "lambda" style handlers via HTTP. Designed to be simple enough to run on a Raspberry Pi or any small machine.

## Project layout

- **main.py** – FastAPI app with a generic `/invoke/{func_name}` endpoint.
- **sandbox_runner.py** – Isolated execution environment for function handlers.
- **functions/** – Individual function directories, each with a `handler.py`.
  - `functions/hello/handler.py` – Returns a greeting.
  - `functions/adder/handler.py` – Adds two numbers.
  - `functions/note/handler.py` – Logs a note and returns a confirmation.
- **logs/** – Log output directory.
  - `output.log` – Application-level logs from `main.py` (invocations, errors).
  - `notes.log` – Notes appended by the `note` function.
- **run.sh** – Convenience script to start the server with uvicorn.
- **pyproject.toml / uv.lock** – Python dependencies (managed by uv/uvicorn + FastAPI).

## Requirements

- Python (matching the version in `.python-version`).
- `uv` or `pip` to install dependencies.
- **bubblewrap** (`bwrap`) for secure sandboxed execution.

### Installing bubblewrap

On Debian/Ubuntu/Raspberry Pi OS:

```bash
sudo apt install bubblewrap
```

Verify installation:

```bash
bwrap --version
```

> **Note:** The server will refuse to execute functions if bubblewrap is not installed.

## Installation

From the project root:

```bash
# Using uv (recommended if you already use it)
uv sync

# Or using pip (inside a virtualenv)
pip install -r <generated requirements>  # or: pip install fastapi uvicorn pydantic
```

If you’re already using `uv`, the included `uv.lock` and `pyproject.toml` should be enough:

```bash
uv sync
```

## Running the server

Make the run script executable once:

```bash
chmod +x run.sh
```

Then start the server from the project root:

```bash
./run.sh
```

By default this starts uvicorn at:

- Host: `0.0.0.0`
- Port: `8000`

So the base URL is typically:

- `http://localhost:8000`
- or `http://<your-hostname>:8000`

### Running in the background (optional)

If you want the server to keep running after you close the terminal:

```bash
nohup ./run.sh > logs/uvicorn.out 2>&1 &
```

## Invoking functions

The API exposes a single endpoint:

```http
POST /invoke/{func_name}
Content-Type: application/json

{
  "event": { ... }
}
```

`func_name` must match a subdirectory of `functions/` that contains a `handler.py` with a `handler(event, context)` function.

### Example: hello

```bash
curl -X POST "http://localhost:8000/invoke/hello" \
  -H "Content-Type: application/json" \
  -d '{"event": {"name": "from HTTP"}}'
```

Expected response:

```json
{
  "result": {
    "message": "Hello, from HTTP!",
    "request_id": "..."
  },
  "request_id": "..."
}
```

### Example: adder

```bash
curl -X POST "http://localhost:8000/invoke/adder" \
  -H "Content-Type: application/json" \
  -d '{"event": {"a": 1, "b": 2}}'
```

Expected result includes the sum in the JSON payload.

### Example: note (with notes.log)

```bash
curl -X POST "http://localhost:8000/invoke/note" \
  -H "Content-Type: application/json" \
  -d '{"event": {"note": "Lynns thanksgiving lunch is tomorrow at 1PM"}}'
``+

Each call appends a line to `logs/notes.log`:

```text
2025-11-27T02:13:45.123456 - Lynns thanksgiving lunch is tomorrow at 1PM
```

The HTTP response looks like:

```json
{
  "result": {
    "message": "Note received: Lynns thanksgiving lunch is tomorrow at 1PM",
    "request_id": "..."
  },
  "request_id": "..."
}
```

## Logging

- `main.py` writes invocation and error logs to `logs/output.log` using Python’s `logging` module.
- The `note` function appends timestamped notes to `logs/notes.log`.

You can inspect them with:

```bash
cat logs/output.log
cat logs/notes.log
```

## Security

Function handlers run in a **bubblewrap sandbox** that provides strong isolation:

| Protection | Description |
|------------|-------------|
| **Process isolation** | Handlers run in a separate subprocess |
| **Filesystem** | Read-only access; only `logs/` is writable |
| **Network** | Outbound API calls allowed; sniffing blocked (no `CAP_NET_RAW`) |
| **Namespaces** | Isolated user, PID, IPC, UTS, and cgroup namespaces |
| **Timeout** | 30-second execution limit |

### What handlers CAN do

- Make outbound HTTP/API requests
- Read their own handler code
- Write to the `logs/` directory
- Use standard Python libraries

### What handlers CANNOT do

- Read/write files outside their directory and `logs/`
- Access other processes or system resources
- Sniff network traffic or open raw sockets
- Run indefinitely (30s timeout enforced)

## Adding new functions

1. Create a new directory under `functions/`, e.g. `functions/myfunc/`.
2. Add a `handler.py` file with:

```python
def handler(event, context):
    # your logic here
    return {"message": "ok", "request_id": context["request_id"]}
```

You can then invoke it with:

```bash
curl -X POST "http://localhost:8000/invoke/myfunc" \
  -H "Content-Type: application/json" \
  -d '{"event": {}}'
```

