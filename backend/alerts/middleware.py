import base64

from django.conf import settings
from django.http import HttpResponse


class BasicAuthMiddleware:
    """Optional HTTP Basic Auth gate for the dashboard, enabled only when
    both DASHBOARD_BASIC_AUTH_USER and DASHBOARD_BASIC_AUTH_PASS are set."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = bool(settings.DASHBOARD_BASIC_AUTH_USER and settings.DASHBOARD_BASIC_AUTH_PASS)

    def __call__(self, request):
        if not self.enabled:
            return self.get_response(request)

        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                user, _, pw = decoded.partition(":")
            except Exception:
                user, pw = "", ""
            if user == settings.DASHBOARD_BASIC_AUTH_USER and pw == settings.DASHBOARD_BASIC_AUTH_PASS:
                return self.get_response(request)

        response = HttpResponse("Authentication required", status=401)
        response["WWW-Authenticate"] = 'Basic realm="Project Sentinel"'
        return response
