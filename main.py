import importlib.util
import logging
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
FUNCTIONS_DIR = BASE_DIR / 'functions'
LOGS_DIR = BASE_DIR / 'logs'
LOG_FILE = LOGS_DIR / 'output.log'

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

def load_function(func_name:str):
    func_path = FUNCTIONS_DIR / func_name / 'handler.py'
    if not func_path.exists():
        raise FileNotFoundError(f'Function {func_name} not found')
    
    spec = importlib.util.spec_from_file_location(f'{func_name}_handler', func_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, 'handler'):
        raise ImportError(f'Function {func_name} does not have a handler')

    return module.handler
    

@app.post('/invoke/{func_name}')
async def invoke(func_name: str, req: InvokeRequest):
    try:
        handler = load_function(func_name)
    except (FileNotFoundError, AttributeError) as e:
        logger.error(f"Function load error for {func_name}: {e}")
        raise HTTPException(status_code=404, detail=str(e))

    context = {
        'request_id': str(uuid.uuid4())
    }

    try:
        logger.info(f"Invoking function={func_name} request_id={context['request_id']} event={req.event}")
        result = handler(req.event, context)
        logger.info(f"Function={func_name} request_id={context['request_id']} result={result}")
    except Exception as e:
        logger.exception(f"Error while executing function={func_name} request_id={context['request_id']}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        'result':result,
        'request_id':context['request_id']
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