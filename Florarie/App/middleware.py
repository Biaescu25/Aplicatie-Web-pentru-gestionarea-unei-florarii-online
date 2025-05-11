# middleware.py
from .models import VisitorLog


class TrackVisitorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not request.user.is_staff and not request.path.startswith('/admin'):
            VisitorLog.objects.create(
                ip=request.META.get('REMOTE_ADDR'),
                path=request.path,
            )

        return response
