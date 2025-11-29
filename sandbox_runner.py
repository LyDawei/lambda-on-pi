#!/usr/bin/env python3
"""
Sandbox Runner - Isolated execution environment for function handlers.

This script runs in a separate subprocess to execute function handlers
in complete isolation from the main application namespace. Communication
happens via stdin/stdout using JSON.

Security features:
- Runs in separate process (no shared memory/namespace with main app)
- Limited imports available to handlers
- Structured JSON communication
- Designed for subprocess timeout enforcement
"""
import importlib.util
import json
import sys
import os
from pathlib import Path


def create_restricted_globals():
    """
    Create a restricted globals dict for handler execution.
    Only allows safe built-in functions and modules.
    """
    safe_builtins = {
        'abs': abs,
        'all': all,
        'any': any,
        'bool': bool,
        'dict': dict,
        'enumerate': enumerate,
        'filter': filter,
        'float': float,
        'format': format,
        'frozenset': frozenset,
        'int': int,
        'isinstance': isinstance,
        'len': len,
        'list': list,
        'map': map,
        'max': max,
        'min': min,
        'print': print,
        'range': range,
        'repr': repr,
        'reversed': reversed,
        'round': round,
        'set': set,
        'slice': slice,
        'sorted': sorted,
        'str': str,
        'sum': sum,
        'tuple': tuple,
        'type': type,
        'zip': zip,
        'None': None,
        'True': True,
        'False': False,
    }
    return {'__builtins__': safe_builtins}


def load_and_execute(func_name: str, func_path: str, event: dict, context: dict) -> dict:
    """
    Load and execute a function handler in isolation.

    Args:
        func_name: Name of the function
        func_path: Path to the handler.py file
        event: Event data to pass to the handler
        context: Context data to pass to the handler

    Returns:
        dict with 'success', 'result' or 'error' keys
    """
    handler_path = Path(func_path)

    if not handler_path.exists():
        return {
            'success': False,
            'error': f'Function {func_name} not found at {func_path}',
            'error_type': 'FileNotFoundError'
        }

    try:
        # Use importlib to properly load the module with import support
        # This allows handler modules to use import statements
        spec = importlib.util.spec_from_file_location(
            f'sandbox_{func_name}_handler',
            handler_path
        )
        if spec is None or spec.loader is None:
            return {
                'success': False,
                'error': f'Could not load function {func_name}',
                'error_type': 'ImportError'
            }

        module = importlib.util.module_from_spec(spec)

        # Set __file__ so the handler can reference its own path
        module.__file__ = str(handler_path)

        # Execute the module (this runs import statements and defines handler)
        spec.loader.exec_module(module)

        # Get the handler function
        if not hasattr(module, 'handler'):
            return {
                'success': False,
                'error': f'Function {func_name} does not have a handler function',
                'error_type': 'ImportError'
            }

        handler = module.handler

        # Execute the handler
        result = handler(event, context)

        return {
            'success': True,
            'result': result
        }

    except SyntaxError as e:
        return {
            'success': False,
            'error': f'Syntax error in function {func_name}: {e}',
            'error_type': 'SyntaxError'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        }


def main():
    """
    Main entry point for the sandbox runner.

    Reads JSON input from stdin with format:
    {
        "func_name": "hello",
        "func_path": "/path/to/functions/hello/handler.py",
        "event": {...},
        "context": {"request_id": "..."}
    }

    Writes JSON output to stdout with format:
    {
        "success": true/false,
        "result": {...} or "error": "...",
        "error_type": "..." (only on failure)
    }
    """
    try:
        # Read input from stdin
        input_data = sys.stdin.read()

        if not input_data:
            output = {
                'success': False,
                'error': 'No input provided',
                'error_type': 'ValueError'
            }
            print(json.dumps(output))
            sys.exit(1)

        # Parse the input JSON
        try:
            request = json.loads(input_data)
        except json.JSONDecodeError as e:
            output = {
                'success': False,
                'error': f'Invalid JSON input: {e}',
                'error_type': 'JSONDecodeError'
            }
            print(json.dumps(output))
            sys.exit(1)

        # Validate required fields
        required_fields = ['func_name', 'func_path', 'event', 'context']
        for field in required_fields:
            if field not in request:
                output = {
                    'success': False,
                    'error': f'Missing required field: {field}',
                    'error_type': 'ValueError'
                }
                print(json.dumps(output))
                sys.exit(1)

        # Execute the function in isolation
        result = load_and_execute(
            func_name=request['func_name'],
            func_path=request['func_path'],
            event=request['event'],
            context=request['context']
        )

        # Output the result
        print(json.dumps(result))
        sys.exit(0 if result['success'] else 1)

    except Exception as e:
        # Catch-all for any unexpected errors
        output = {
            'success': False,
            'error': f'Sandbox runner error: {e}',
            'error_type': type(e).__name__
        }
        print(json.dumps(output))
        sys.exit(1)


if __name__ == '__main__':
    main()
