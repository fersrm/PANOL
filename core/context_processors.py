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
    from PanolApp.permissions import puede_operar, puede_solicitar

    secciones = {
        "inventario": (
            "inventario",
            "articulo",
            "articulo_nuevo",
            "articulo_editar",
            "ajustar",
        ),
        "kits": ("kits", "kit_nuevo", "kit_editar"),
        "carrito": ("carrito",),
        "plantillas": ("plantillas", "plantilla"),
        "solicitudes": ("solicitudes", "solicitud", "enviar"),
        "prestamos": ("prestamos", "prestamo", "directo", "devolver"),
        "movimientos": ("consultas",),
        "catalogos": ("catalogo", "catalogo_nuevo", "catalogo_editar"),
    }
    match = getattr(request, "resolver_match", None)
    seccion = (
        next(
            (nombre for nombre, rutas in secciones.items() if match.url_name in rutas),
            None,
        )
        if match and match.namespace == "panol"
        else None
    )

    return {
        "seccion_panol": seccion,
        "puede_usar_panol": puede_solicitar(request.user),
        "puede_operar_panol": puede_operar(request.user),
        "puede_registrar_usuarios": bool(cargos_asignables(request.user)),
        "puede_gestionar_usuarios": puede_gestionar,
    }
