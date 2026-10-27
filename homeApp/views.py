from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import OperationalError, ProgrammingError
from django.utils import timezone
from django.views.generic import ListView

from PanolApp.selectors import dashboard
from UsuarioApp.models import Profile

User = get_user_model()


class HomeView(LoginRequiredMixin, ListView):
    model = User
    template_name = "pages/index.html"
    context_object_name = "usuarios_recientes"

    ACTIVIDAD_MINUTOS = 10

    def get_queryset(self):
        return (
            User.objects.filter(
                is_active=True,
                last_login__isnull=False,
            )
            .select_related("profile__position_FK")
            .order_by("-last_login", "-pk")[:5]
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        ahora = timezone.now()
        limite_actividad = ahora - timedelta(
            minutes=self.ACTIVIDAD_MINUTOS,
        )

        active_users = set(
            Profile.objects.filter(
                user_FK__is_active=True,
                last_activity__gte=limite_actividad,
                last_activity__lte=ahora,
            ).values_list("user_FK_id", flat=True)
        )

        context.update(
            active_users=active_users,
            usuarios_con_actividad=len(active_users),
            actividad_minutos=self.ACTIVIDAD_MINUTOS,
            actualizado_en=ahora,
        )

        try:
            context.update(dashboard(self.request.user))
        except (OperationalError, ProgrammingError):
            # El módulo puede no tener su esquema instalado todavía.
            # Informar indisponibilidad sin presentarla como cero real.
            context.update(panol_error=True)
        return context
