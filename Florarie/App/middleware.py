# middleware.py
from .models import VisitorLog
from django.core.cache import cache
from django.utils import timezone


class TrackVisitorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _get_client_ip(self, request):
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            # first IP in list
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR') or '0.0.0.0'

    def __call__(self, request):
        response = self.get_response(request)

        if request.user.is_staff or request.path.startswith('/admin'):
            return response

        if not request.session.session_key:
            request.session.save()
        session_key = request.session.session_key

        ip = self._get_client_ip(request)
        today = timezone.now().date()

        # Log only first visit per session per path per day
        exists = VisitorLog.objects.filter(
            session_key=session_key,
            path=request.path,
            timestamp__date=today
        ).exists()

        if not exists:
            try:
                VisitorLog.objects.create(ip=ip, path=request.path, session_key=session_key)
            except Exception:
                pass

        return response
