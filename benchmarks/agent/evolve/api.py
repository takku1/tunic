def handle_legacy(request):
    return "legacy:" + request


def handle_modern(request):
    return "modern:" + request


def route(request):
    return handle_modern(request)
