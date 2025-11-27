import importlib.util
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

FUNCTIONS_DIR = Path(__file__).parent / 'functions'
app = FastAPI()

class InvokeRequest(BaseModel):
    event: dict

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
        raise HTTPException(status_code=404, detail=str(e))

    context = {
        'request_id': str(uuid.uuid4())
    }

    try:
        result = handler(req.event, context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        'result':result,
        'request_id':context['request_id']
    }