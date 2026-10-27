from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.deprecation import MiddlewareMixin


class UpdateLastActivityMiddleware(MiddlewareMixin):
    ACTIVITY_UPDATE_INTERVAL = timedelta(minutes=5)
    SESSION_ACTIVITY_KEY = "_last_activity_update"

    def process_view(self, request, view_func, view_args, view_kwargs):
        # No aplicar en el administrador.
        if getattr(request.resolver_match, "app_name", "") == "admin":
            return

        if not request.user.is_authenticated:
            return

        now = timezone.now()

        # Si ya se actualizó hace menos de 5 minutos, no consultar ni escribir en BD.
        last_update_raw = request.session.get(self.SESSION_ACTIVITY_KEY)

        if last_update_raw:
            last_update = parse_datetime(last_update_raw)

            if last_update and now - last_update < self.ACTIVITY_UPDATE_INTERVAL:
                return

        try:
            profile = request.user.profile
        except ObjectDoesNotExist:
            return

        # Actualiza el perfil solo cada 5 minutos.
        profile.update_last_activity()

        # Renueva la sesión solo cada 5 minutos.
        request.session[self.SESSION_ACTIVITY_KEY] = now.isoformat()
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)

        return
