import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
FUNCTIONS_DIR = BASE_DIR / 'functions'
LOGS_DIR = BASE_DIR / 'logs'
LOG_FILE = LOGS_DIR / 'output.log'
SANDBOX_RUNNER = BASE_DIR / 'sandbox_runner.py'

# Security: Maximum execution time for handlers (in seconds)
HANDLER_TIMEOUT = 30

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

def execute_handler_isolated(func_name: str, event: dict, context: dict) -> dict:
    """
    Execute a function handler in an isolated subprocess.

    This provides security isolation by running the handler in a separate
    Python process with no access to the main application's namespace.

    Args:
        func_name: Name of the function to execute
        event: Event data to pass to the handler
        context: Context data to pass to the handler

    Returns:
        dict containing the handler result

    Raises:
        FileNotFoundError: If the function doesn't exist
        TimeoutError: If the handler exceeds the timeout limit
        RuntimeError: If the handler execution fails
    """
    func_path = FUNCTIONS_DIR / func_name / 'handler.py'
    if not func_path.exists():
        raise FileNotFoundError(f'Function {func_name} not found')

    # Prepare the input for the sandbox runner
    sandbox_input = json.dumps({
        'func_name': func_name,
        'func_path': str(func_path),
        'event': event,
        'context': context
    })

    try:
        # Execute the handler in an isolated subprocess
        result = subprocess.run(
            [sys.executable, str(SANDBOX_RUNNER)],
            input=sandbox_input,
            capture_output=True,
            text=True,
            timeout=HANDLER_TIMEOUT,
            # Security: Don't inherit environment variables that might leak secrets
            env={
                'PATH': '/usr/bin:/bin',
                'PYTHONPATH': '',
                'HOME': '/tmp',
            }
        )

        # Parse the output from the sandbox
        if result.stdout:
            try:
                output = json.loads(result.stdout)
            except json.JSONDecodeError:
                raise RuntimeError(f'Invalid response from sandbox: {result.stdout}')
        else:
            error_msg = result.stderr if result.stderr else 'No output from sandbox'
            raise RuntimeError(f'Sandbox execution failed: {error_msg}')

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

    try:
        logger.info(f"Invoking function={func_name} request_id={context['request_id']} event={req.event}")

        # Execute the handler in an isolated subprocess for security
        result = execute_handler_isolated(func_name, req.event, context)

        logger.info(f"Function={func_name} request_id={context['request_id']} result={result}")
    except FileNotFoundError as e:
        logger.error(f"Function not found: {func_name} request_id={context['request_id']}: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except TimeoutError as e:
        logger.error(f"Function timeout: {func_name} request_id={context['request_id']}: {e}")
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.exception(f"Error executing function={func_name} request_id={context['request_id']}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        'result': result,
        'request_id': context['request_id']
    }

@app.post('/deploy')
async def deploy(req: DeployRequest):
    request_id = str(uuid.uuid4())
    func_name = req.func_name
    func_body = req.func_body

    func_dir = FUNCTIONS_DIR / func_name
    handler_path = func_dir / 'handler.py'

    try:
        logger.info(f"Deploying function={func_name} request_id={request_id}")
        func_dir.mkdir(parents=True, exist_ok=True)
        handler_path.write_text(func_body)
        logger.info(f"Function={func_name} deployed successfully request_id={request_id}")
    except Exception as e:
        logger.exception(f"Error deploying function={func_name} request_id={request_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        'message': f'Function {func_name} deployed successfully',
        'request_id': request_id
    }