"""Configuración académica y evidencia inmutable de solicitudes."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Docencia, EquipamientoAsignatura, PlanAsignatura
from .permissions import es_docente, exigir_academico


def contexto_solicitud(actor, datos):
    docencia = datos.get("docencia")
    carrera, asignatura = datos.get("carrera"), datos.get("asignatura")
    if not any((docencia, carrera, asignatura)):
        return None, {}
    if not all((docencia, carrera, asignatura)):
        raise ValidationError(
            "Para un pedido académico selecciona carrera, asignatura y docente/período."
        )
    plan = (
        PlanAsignatura.objects.select_for_update()
        .select_related("periodo", "carrera__area", "asignatura")
        .get(pk=docencia.plan_id)
    )
    d = (
        Docencia.objects.select_for_update()
        .select_related("docente")
        .get(pk=docencia.pk)
    )
    if not (
        d.activo
        and es_docente(d.docente)
        and plan.activo
        and plan.periodo.activo
        and plan.carrera.activo
        and plan.carrera.area.activo
        and plan.asignatura.activo
    ):
        raise ValidationError("La asociación académica ya no está habilitada.")
    if (
        plan.carrera_id != carrera.pk
        or plan.asignatura_id != asignatura.pk
        or not asignatura.carreras.filter(pk=carrera.pk).exists()
    ):
        raise ValidationError(
            "El docente/período no corresponde a la carrera y asignatura."
        )
    if datos["tipo"] == "docente" and d.docente_id != actor.pk:
        raise ValidationError(
            "Una solicitud docente debe corresponder a tu propia cuenta."
        )
    if not plan.periodo.inicio <= datos["fecha_necesaria"] <= plan.periodo.fin:
        raise ValidationError("La fecha necesaria está fuera del período seleccionado.")
    return d, {
        "docente": str(d.docente.get_full_name() or d.docente.username),
        "usuario": d.docente.username,
        "carrera": str(plan.carrera),
        "asignatura": str(plan.asignatura),
        "periodo": str(plan.periodo),
        "inicio": plan.periodo.inicio.isoformat(),
        "fin": plan.periodo.fin.isoformat(),
        "plan": plan.pk,
        "revision": plan.revision,
        "configurada": plan.configurada,
        "articulos": list(plan.equipamiento.values_list("articulo_id", flat=True)),
        "nombres_articulos": dict(
            plan.equipamiento.values_list("articulo_id", "articulo__nombre")
        ),
    }


@transaction.atomic
def cambiar_plan(actor, pk, revision, accion, objeto=None):
    actor = exigir_academico(actor)
    plan = PlanAsignatura.objects.select_for_update().get(pk=pk)
    if revision != plan.revision:
        raise ValidationError("La pauta cambió. Recarga y revisa antes de guardar.")
    if accion == "agregar":
        if not objeto.activo or not objeto.categoria.activo:
            raise ValidationError("Artículo deshabilitado.")
        EquipamientoAsignatura.objects.get_or_create(plan=plan, articulo=objeto)
        plan.configurada = True
    elif accion == "quitar":
        plan.equipamiento.filter(articulo=objeto).delete()
        plan.configurada = True
    elif accion == "confirmar":
        plan.configurada = True
    elif accion == "docente":
        if not es_docente(objeto):
            raise ValidationError(
                "Selecciona una cuenta docente activa con cargo RESTRICTED."
            )
        d, _ = Docencia.objects.get_or_create(
            plan=plan, docente=objeto, defaults={"actor": actor}
        )
        d.activo, d.actor = True, actor
        d.save()
    elif accion == "desvincular":
        d = plan.docencias.get(pk=objeto)
        d.activo, d.actor = False, actor
        d.save()
    else:
        raise ValidationError("Acción no válida.")
    plan.revision += 1
    plan.actor = actor
    plan.save()
    return plan
