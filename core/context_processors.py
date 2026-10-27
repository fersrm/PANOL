from django.db import OperationalError

from core.mixins import PermitsPositionMixin, usuario_tiene_cargo
from UsuarioApp.models import UserVisualPreference
from UsuarioApp.permissions import cargos_asignables


def preferencias_visuales(request):
    """Solo lee la cuenta propia; una visita nunca crea preferencias/perfiles."""
    user = getattr(request, "user", None)
    puede_cambiar = bool(user and user.is_authenticated and user.is_active)
    mode = UserVisualPreference.Mode.LIGHT
    if puede_cambiar:
        try:
            saved_mode = (
                UserVisualPreference.objects.filter(user=user)
                .values_list("mode", flat=True)
                .first()
            )
        except OperationalError:
            # Si falla la lectura de esta preferencia, usar un modo seguro.
            saved_mode = None
        if saved_mode in UserVisualPreference.Mode.values:
            mode = saved_mode
    return {"modo_visual": mode, "puede_cambiar_modo": puede_cambiar}


def permisos_navegacion(request):
    """Expone al menú la misma política que protege el registro interno."""
    puede_gestionar = usuario_tiene_cargo(request.user, PermitsPositionMixin.permisos)
    return {
        "puede_registrar_usuarios": bool(cargos_asignables(request.user)),
        "puede_gestionar_usuarios": puede_gestionar,
    }
