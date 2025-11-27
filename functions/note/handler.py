
from datetime import datetime
from pathlib import Path


def handler(event, context):
    note = event.get('note', '')

    base_dir = Path(__file__).resolve().parents[2]
    logs_dir = base_dir / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / 'notes.log'

    timestamp = datetime.now().isoformat()
    with log_file.open('a', encoding='utf-8') as f:
        f.write(f"{timestamp} - {note}\n")

    return {
        'message': f'Note received: {note}',
        'request_id': context['request_id']
    }

def handler(event, context):
    note = event.get('note', '')

    return {
        'message': f'Note received: {note}',
        'request_id': context['request_id']
    }