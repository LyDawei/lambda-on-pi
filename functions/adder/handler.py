def handler(event, context):

    val1 = event.get('val1', 0)
    val2 = event.get('val2', 0)

    return {
        'message': f'{val1+val2}',
        'request_id': context['request_id']
    }