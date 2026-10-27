from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy

from UsuarioApp.models import Profile


def usuario_tiene_cargo(user, cargos=("ADMIN", "MANAGER")):
    """Una misma regla para las vistas y los enlaces del menú."""
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    try:
        perfil = user.profile
    except Profile.DoesNotExist:
        return False
    return bool(perfil.position_FK and perfil.position_FK.permission_code in cargos)


class PermitsPositionMixin(LoginRequiredMixin):
    """
    Permite acceso al módulo solo a usuarios activos
    con cargo ADMIN o MANAGER.
    """

    login_url = "account_login"
    redirect_url = reverse_lazy("Home")

    permisos = ("ADMIN", "MANAGER")

    def dispatch(self, request, *args, **kwargs):
        user = request.user

        if not user.is_authenticated:
            return self.handle_no_permission()

        if not usuario_tiene_cargo(user, self.permisos):
            return redirect(self.redirect_url)

        return super().dispatch(request, *args, **kwargs)
