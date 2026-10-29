from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from core.mixins import usuario_tiene_cargo


def puede_operar(user):
    return usuario_tiene_cargo(user, ("ADMIN", "MANAGER"))


def puede_solicitar(user):
    return usuario_tiene_cargo(user, ("ADMIN", "MANAGER", "RESTRICTED"))


def exigir_actor(user, *, operador=False):
    """Los servicios también comprueban actividad/cargo vigentes en la DB."""
    actor = get_user_model().objects.get(pk=user.pk)
    if not (puede_operar(actor) if operador else puede_solicitar(actor)):
        raise PermissionDenied
    return actor


def puede_academico(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and get_user_model()
        .objects.filter(
            pk=user.pk, is_active=True, profile__position_FK__permission_code="ADMIN"
        )
        .exists()
    )


def exigir_academico(user):
    if not puede_academico(user):
        raise PermissionDenied
    return get_user_model().objects.get(pk=user.pk)


def docentes_elegibles():
    """El cargo docente de este producto es RESTRICTED, sin excepción root."""
    return get_user_model().objects.filter(
        is_active=True, profile__position_FK__permission_code="RESTRICTED"
    )


def es_docente(user):
    return bool(
        user.is_authenticated
        and user.pk
        and docentes_elegibles().filter(pk=user.pk).exists()
    )
