"""Permisos por destinatario para gestión de cuentas (spec 004)."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied

from .models import Profile


def cargo_usuario(user):
    try:
        position = user.profile.position_FK
    except Profile.DoesNotExist:
        return None
    return position.permission_code if position else None


def cargos_asignables(actor):
    """Registro: cargo requerido incluso para root; códigos, nunca IDs."""
    if not actor.is_authenticated or not actor.is_active:
        return ()
    cargo = cargo_usuario(actor)
    if cargo == "ADMIN":
        return (
            ("ADMIN", "MANAGER", "RESTRICTED")
            if actor.is_superuser
            else ("MANAGER", "RESTRICTED")
        )
    return ("RESTRICTED",) if cargo == "MANAGER" else ()


class UserRegistrationMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.registration_actor = User.objects.select_related(
            "profile__position_FK"
        ).get(pk=request.user.pk)
        if not cargos_asignables(self.registration_actor):
            # Conserva el retorno de Registro para usuarios sin permiso.
            from django.shortcuts import redirect

            return redirect("Home")
        return super().dispatch(request, *args, **kwargs)


def puede_ejecutar_accion(actor, action):
    # is_superuser no sustituye al cargo en estas acciones.
    if not actor.is_authenticated or not actor.is_active:
        return False
    cargos = ("ADMIN",) if action == "reset" else ("ADMIN", "MANAGER")
    return (
        action in ("activate", "deactivate", "reset") and cargo_usuario(actor) in cargos
    )


def puede_gestionar_cuenta(actor, target, action):
    if not puede_ejecutar_accion(actor, action):
        return False
    if actor.pk == target.pk or target.is_superuser:
        return False
    inferiores = {"ADMIN": ("MANAGER", "RESTRICTED"), "MANAGER": ("RESTRICTED",)}
    return cargo_usuario(target) in inferiores[cargo_usuario(actor)]


class AccountManagementMixin(LoginRequiredMixin):
    account_action = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        # Una instancia nueva evita relaciones/cargos almacenados en caché.
        self.account_actor = User.objects.select_related("profile__position_FK").get(
            pk=request.user.pk
        )
        if not puede_ejecutar_accion(self.account_actor, self.account_action):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
