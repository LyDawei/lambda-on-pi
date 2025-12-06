import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
FUNCTIONS_DIR = BASE_DIR / 'functions'
LOGS_DIR = BASE_DIR / 'logs'
LOG_FILE = LOGS_DIR / 'output.log'
SANDBOX_RUNNER = BASE_DIR / 'sandbox_runner.py'

# Security: Maximum execution time for handlers (in seconds)
HANDLER_TIMEOUT = 30

# Security: API key for deploy endpoint (set via environment variable)
DEPLOY_API_KEY = os.environ.get('LAMBDA_DEPLOY_API_KEY')

# Security: Valid function name pattern (alphanumeric, underscore, hyphen only)
VALID_FUNC_NAME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]*$')

# Check if bubblewrap is available for Linux sandboxing
BWRAP_PATH = shutil.which('bwrap')
USE_BWRAP = BWRAP_PATH is not None


def validate_func_name(func_name: str) -> None:
    """
    Validate function name to prevent path traversal attacks.

    Args:
        func_name: The function name to validate

    Raises:
        ValueError: If the function name is invalid
    """
    if not func_name:
        raise ValueError("Function name cannot be empty")
    if len(func_name) > 64:
        raise ValueError("Function name too long (max 64 characters)")
    if not VALID_FUNC_NAME_PATTERN.match(func_name):
        raise ValueError(
            "Invalid function name. Must start with a letter and contain only "
            "letters, numbers, underscores, and hyphens"
        )


def sanitize_for_log(value: any, max_length: int = 200) -> str:
    """
    Sanitize a value for safe logging.

    Removes newlines, control characters, and truncates long values.
    """
    s = str(value)
    # Remove control characters and newlines
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', s)
    # Truncate if too long
    if len(s) > max_length:
        s = s[:max_length] + '...[truncated]'
    return s

LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("lambda_on_pi")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(LOG_FILE)
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

app = FastAPI()

class InvokeRequest(BaseModel):
    event: dict

class DeployRequest(BaseModel):
    func_name: str
    func_body: str

def _build_bwrap_command(func_path: Path) -> list:
    """
    Build the bubblewrap command for sandboxed execution.

    Creates a minimal, isolated environment with:
    - Read-only access to Python and system libraries
    - Read-only access to the function handler
    - Read-write access only to logs directory
    - Outbound network allowed (for API calls)
    - No network sniffing (CAP_NET_RAW/CAP_NET_ADMIN stripped via user namespace)
    - Isolated PID/IPC/UTS namespaces
    - Memory limit enforced
    - Minimal /etc exposure (only DNS and SSL certs)
    - Temporary /tmp filesystem
    """
    python_path = sys.executable
    func_dir = func_path.parent

    # Build list of /etc files to expose (minimal set for network/SSL)
    etc_binds = []
    etc_files = [
        '/etc/resolv.conf',      # DNS resolution
        '/etc/hosts',            # Local hostname resolution
        '/etc/ssl',              # SSL certificates
        '/etc/ca-certificates',  # CA certificates
        '/etc/localtime',        # Timezone
        '/etc/mime.types',       # MIME types for HTTP
    ]
    for etc_file in etc_files:
        if Path(etc_file).exists():
            etc_binds.extend(['--ro-bind', etc_file, etc_file])

    cmd = [
        BWRAP_PATH,
        # Namespace isolation (user namespace strips dangerous capabilities like CAP_NET_RAW)
        '--unshare-user',
        '--unshare-pid',
        '--unshare-ipc',
        '--unshare-uts',
        '--unshare-cgroup',
        # Network: Allow outbound connections for API calls
        # Sniffing is prevented by --unshare-user which strips CAP_NET_RAW/CAP_NET_ADMIN
        # Die when parent process dies
        '--die-with-parent',
        # New session to prevent terminal access
        '--new-session',
        # Read-only bind mounts for Python and system libraries
        '--ro-bind', '/usr', '/usr',
        '--ro-bind', '/lib', '/lib',
        '--ro-bind', '/bin', '/bin',
        # Symlink for /lib64 if it exists (common on 64-bit systems)
        *(('--ro-bind', '/lib64', '/lib64') if Path('/lib64').exists() else ()),
        # Minimal /etc exposure - only what's needed for DNS and SSL
        *etc_binds,
        # Python executable (in case it's not in /usr)
        '--ro-bind', python_path, python_path,
        # Sandbox runner script - read-only
        '--ro-bind', str(SANDBOX_RUNNER), str(SANDBOX_RUNNER),
        # Function directory - read-only
        '--ro-bind', str(func_dir), str(func_dir),
        # Logs directory - read-write for functions that need to write logs
        '--bind', str(LOGS_DIR), str(LOGS_DIR),
        # Minimal /dev
        '--dev', '/dev',
        # Temporary filesystem
        '--tmpfs', '/tmp',
        # Set working directory
        '--chdir', '/tmp',
        # The actual command to run
        python_path, str(SANDBOX_RUNNER),
    ]

    return cmd


def execute_handler_isolated(func_name: str, event: dict, context: dict) -> dict:
    """
    Execute a function handler in an isolated sandbox.

    Requires bubblewrap (bwrap) on Linux for strong isolation:
    - Filesystem: Read-only except for logs directory
    - Network: Outbound allowed, sniffing blocked (no CAP_NET_RAW)
    - Namespaces: Isolated PID, IPC, UTS, user, cgroup
    - Resources: Timeout and memory protection

    Args:
        func_name: Name of the function to execute
        event: Event data to pass to the handler
        context: Context data to pass to the handler

    Returns:
        dict containing the handler result

    Raises:
        ValueError: If the function name is invalid
        FileNotFoundError: If the function doesn't exist
        TimeoutError: If the handler exceeds the timeout limit
        RuntimeError: If the handler execution fails or sandbox unavailable
    """
    # Validate function name to prevent path traversal
    validate_func_name(func_name)

    if not USE_BWRAP:
        raise RuntimeError(
            "Sandbox unavailable: bubblewrap (bwrap) is required for secure execution. "
            "Install with: sudo apt install bubblewrap"
        )

    func_path = FUNCTIONS_DIR / func_name / 'handler.py'
    if not func_path.exists():
        raise FileNotFoundError(f'Function not found')

    # Prepare the input for the sandbox runner
    sandbox_input = json.dumps({
        'func_name': func_name,
        'func_path': str(func_path),
        'event': event,
        'context': context
    })

    try:
        # Use bubblewrap for strong Linux sandboxing
        cmd = _build_bwrap_command(func_path)
        result = subprocess.run(
            cmd,
            input=sandbox_input,
            capture_output=True,
            text=True,
            timeout=HANDLER_TIMEOUT,
            env={
                'PATH': '/usr/bin:/bin',
                'HOME': '/tmp',
            }
        )

        # Parse the output from the sandbox
        if result.stdout:
            try:
                output = json.loads(result.stdout)
            except json.JSONDecodeError:
                # Don't leak sandbox output in error message
                raise RuntimeError('Invalid response from sandbox')
        else:
            # Don't leak stderr details to client
            raise RuntimeError('Sandbox execution failed')

        # Check if execution was successful
        if not output.get('success', False):
            error_type = output.get('error_type', 'RuntimeError')
            error_msg = output.get('error', 'Unknown error')

            if error_type == 'FileNotFoundError':
                raise FileNotFoundError(error_msg)
            else:
                raise RuntimeError(error_msg)

        return output['result']

    except subprocess.TimeoutExpired:
        raise TimeoutError(f'Function {func_name} exceeded timeout of {HANDLER_TIMEOUT} seconds')
    

@app.post('/invoke/{func_name}')
async def invoke(func_name: str, req: InvokeRequest):
    context = {
        'request_id': str(uuid.uuid4())
    }

    # Sanitize for logging
    safe_func_name = sanitize_for_log(func_name)
    safe_event = sanitize_for_log(req.event)

    try:
        logger.info(f"Invoking function={safe_func_name} request_id={context['request_id']} event={safe_event}")

        # Execute the handler in an isolated subprocess for security
        result = execute_handler_isolated(func_name, req.event, context)

        safe_result = sanitize_for_log(result)
        logger.info(f"Function={safe_func_name} request_id={context['request_id']} result={safe_result}")
    except ValueError as e:
        logger.warning(f"Invalid function name: request_id={context['request_id']}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        logger.error(f"Function not found: {safe_func_name} request_id={context['request_id']}")
        raise HTTPException(status_code=404, detail="Function not found")
    except TimeoutError:
        logger.error(f"Function timeout: {safe_func_name} request_id={context['request_id']}")
        raise HTTPException(status_code=504, detail="Function execution timed out")
    except Exception as e:
        logger.exception(f"Error executing function={safe_func_name} request_id={context['request_id']}")
        raise HTTPException(status_code=500, detail="Internal server error")

    return {
        'result': result,
        'request_id': context['request_id']
    }

@app.post('/deploy')
async def deploy(
    req: DeployRequest,
    x_api_key: str = Header(None, alias="X-API-Key")
):
    request_id = str(uuid.uuid4())

    # Security: Require API key for deployment
    if not DEPLOY_API_KEY:
        logger.error(f"Deploy attempted but LAMBDA_DEPLOY_API_KEY not set request_id={request_id}")
        raise HTTPException(
            status_code=503,
            detail="Deployment disabled: API key not configured"
        )

    if not x_api_key or not secrets.compare_digest(x_api_key, DEPLOY_API_KEY):
        logger.warning(f"Deploy unauthorized attempt request_id={request_id}")
        raise HTTPException(status_code=401, detail="Unauthorized")

    func_name = req.func_name
    func_body = req.func_body

    # Validate function name to prevent path traversal
    try:
        validate_func_name(func_name)
    except ValueError as e:
        logger.warning(f"Invalid function name in deploy request_id={request_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    # Sanitize for logging
    safe_func_name = sanitize_for_log(func_name)

    func_dir = FUNCTIONS_DIR / func_name
    handler_path = func_dir / 'handler.py'

    try:
        logger.info(f"Deploying function={safe_func_name} request_id={request_id}")
        func_dir.mkdir(parents=True, exist_ok=True)
        handler_path.write_text(func_body)
        logger.info(f"Function={safe_func_name} deployed successfully request_id={request_id}")
    except Exception:
        logger.exception(f"Error deploying function={safe_func_name} request_id={request_id}")
        raise HTTPException(status_code=500, detail="Deployment failed")

    return {
        'message': f'Function {func_name} deployed successfully',
        'request_id': request_id
    }