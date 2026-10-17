class ScriptNameMiddleware:
    """
    Middleware to set SCRIPT_NAME from X-Script-Name header.
    This allows the application to work correctly behind a reverse proxy
    that strips a path prefix.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        script_name = request.META.get('HTTP_X_SCRIPT_NAME', '')
        if script_name:
            request.META['SCRIPT_NAME'] = script_name
        return self.get_response(request)
