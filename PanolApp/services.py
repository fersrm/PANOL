"""Operaciones del pañol: autorización vigente y escrituras atómicas."""

from collections import defaultdict
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import (
    Articulo,
    Carrito,
    Categoria,
    ComponenteKit,
    Devolucion,
    Kit,
    LineaCarrito,
    LineaDevolucion,
    LineaPlantilla,
    LineaPrestamo,
    LineaSolicitud,
    MovimientoInventario,
    PlantillaPedido,
    Prestamo,
    Solicitud,
)
from .permissions import exigir_actor


def entero(value, minimo=1, maximo=9999):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimo <= value <= maximo
    ):
        raise ValidationError(
            f"La cantidad debe ser un entero entre {minimo} y {maximo}."
        )
    return value


def texto(value, nombre="motivo", limite=2000):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limite:
        raise ValidationError(f"Indica {nombre} (hasta {limite} caracteres).")
    return value.strip()


def fecha_futura(value):
    if type(value) is not date or value < timezone.localdate():
        raise ValidationError("La fecha no puede ser anterior a hoy.")
    return value


def movimiento(
    actor,
    articulo,
    tipo,
    motivo,
    disponible=0,
    danado=0,
    perdido=0,
    prestamo=None,
    devolucion=None,
):
    return MovimientoInventario.objects.create(
        actor=actor,
        articulo=articulo,
        tipo=tipo,
        motivo=motivo,
        delta_disponible=disponible,
        delta_no_disponible=danado,
        perdida=perdido,
        prestamo=prestamo,
        devolucion=devolucion,
    )


@transaction.atomic
def ajustar_stock(actor, articulo_id, disponible, no_disponible, motivo):
    actor = exigir_actor(actor, operador=True)
    entero(disponible, -999999, 999999)
    entero(no_disponible, -999999, 999999)
    motivo = texto(motivo)
    if disponible == no_disponible == 0:
        raise ValidationError("Indica una variación de existencias.")
    articulo = get_object_or_404(Articulo, pk=articulo_id)
    updated = Articulo.objects.filter(
        pk=articulo.pk,
        disponible__gte=max(0, -disponible),
        no_disponible__gte=max(0, -no_disponible),
    ).update(
        disponible=F("disponible") + disponible,
        no_disponible=F("no_disponible") + no_disponible,
    )
    if not updated:
        raise ValidationError(
            "El ajuste dejaría existencias negativas. Revisa el saldo actual."
        )
    movimiento(actor, articulo, "ajuste", motivo, disponible, no_disponible)


@transaction.atomic
def guardar_articulo(actor, articulo, inicial=0):
    actor = exigir_actor(actor, operador=True)
    nuevo = articulo.pk is None
    entero(inicial, 0, 999999)
    articulo.codigo = articulo.codigo.strip().upper()
    articulo.nombre = texto(articulo.nombre, "un nombre", 200)
    articulo.full_clean()
    if nuevo:
        articulo.save()
        if inicial:
            ajustar_stock(actor, articulo.pk, inicial, 0, "Ingreso inicial")
    else:
        # No sobrescribir saldos leídos antes de una entrega concurrente.
        articulo.save(
            update_fields=[
                "codigo",
                "nombre",
                "descripcion",
                "categoria",
                "minimo",
                "activo",
            ]
        )
    return articulo


@transaction.atomic
def guardar_kit(actor, kit, componentes, version=None):
    exigir_actor(actor, operador=True)
    if not componentes or len(componentes) > 100:
        raise ValidationError("El kit debe contener entre 1 y 100 artículos.")
    ids = set()
    for articulo, cantidad in componentes:
        entero(cantidad)
        if articulo.pk in ids or not articulo.activo or not articulo.categoria.activo:
            raise ValidationError(
                "Los componentes deben ser distintos y estar habilitados."
            )
        ids.add(articulo.pk)
    kit.codigo = kit.codigo.strip().upper()
    kit.nombre = texto(kit.nombre, "un nombre", 200)
    kit.full_clean()
    if kit.pk:
        if not Kit.objects.filter(pk=kit.pk, revision=version).update(
            revision=F("revision") + 1,
            codigo=kit.codigo,
            nombre=kit.nombre,
            descripcion=kit.descripcion,
            activo=kit.activo,
        ):
            raise ValidationError(
                "Otro operador modificó este kit. Recarga antes de guardar."
            )
        kit.componentes.all().delete()
    else:
        kit.save()
    ComponenteKit.objects.bulk_create(
        [ComponenteKit(kit=kit, articulo=a, cantidad=c) for a, c in componentes]
    )
    return kit


def tomar_carrito(actor, version):
    entero(version, 0, 2147483647)
    carrito, _ = Carrito.objects.get_or_create(propietario=actor)
    if not Carrito.objects.filter(pk=carrito.pk, version=version).update(
        version=F("version") + 1
    ):
        raise ValidationError(
            "Tu carrito cambió en otra pestaña. Recarga para continuar."
        )
    carrito.refresh_from_db()
    return carrito


def seleccion_valida(linea):
    if linea.articulo_id:
        articulo = linea.articulo
        if not articulo.activo or not articulo.categoria.activo:
            raise ValidationError(
                f"{articulo.nombre} ya no está disponible en el catálogo. Quítalo del carrito."
            )
        return [(articulo, linea.cantidad)]
    kit = linea.kit
    if not kit.activo:
        raise ValidationError(
            f"El kit {kit.nombre} está deshabilitado. Quítalo del carrito."
        )
    if linea.cambio:
        raise ValidationError(
            f"Cambió la composición de {kit.nombre}. Revisa y actualiza el carrito."
        )
    # Expandir la composición que la persona revisó, no volver a leer una
    # receta diferente si un operador la edita entre estas consultas.
    componentes = linea.composicion
    if not componentes:
        raise ValidationError("El kit está vacío.")
    articulos = {
        a.pk: a
        for a in Articulo.objects.filter(
            pk__in=[c["articulo"] for c in componentes]
        ).select_related("categoria")
    }
    if any(
        c["articulo"] not in articulos
        or not articulos[c["articulo"]].activo
        or not articulos[c["articulo"]].categoria.activo
        for c in componentes
    ):
        raise ValidationError(f"El kit {kit.nombre} tiene componentes deshabilitados.")
    return [
        (articulos[c["articulo"]], c["cantidad"] * linea.cantidad) for c in componentes
    ]


def consolidar(lineas):
    totales = defaultdict(int)
    articulos = {}
    origen = []
    for linea in lineas:
        entero(linea.cantidad)
        for articulo, cantidad in seleccion_valida(linea):
            totales[articulo.pk] += cantidad
            articulos[articulo.pk] = articulo
        origen.append(
            {
                "nombre": linea.nombre,
                "cantidad": linea.cantidad,
                "kit": linea.kit_id,
                "composicion": linea.composicion,
            }
        )
    if not totales or len(totales) > 100:
        raise ValidationError(
            "El pedido debe contener entre 1 y 100 artículos distintos."
        )
    for cantidad in totales.values():
        entero(cantidad)
    return [(articulos[pk], totales[pk]) for pk in sorted(totales)], origen


@transaction.atomic
def cambiar_carrito(
    actor, version, accion, *, articulo=None, kit=None, cantidad=1, linea_id=None
):
    actor = exigir_actor(actor)
    carrito = tomar_carrito(actor, version)
    if accion == "vaciar":
        carrito.lineas.all().delete()
    elif accion == "revisar":
        for linea in carrito.lineas.select_related("kit"):
            if linea.kit_id:
                linea.composicion = linea.kit.composicion()
                linea.revision = linea.kit.revision
                linea.save(update_fields=["composicion", "revision"])
    elif accion in ("quitar", "cantidad"):
        linea = get_object_or_404(carrito.lineas, pk=linea_id)
        if accion == "quitar":
            linea.delete()
        else:
            linea.cantidad = entero(cantidad)
            linea.save(update_fields=["cantidad"])
    elif accion == "agregar":
        entero(cantidad)
        if (articulo is None) == (kit is None):
            raise ValidationError("Selecciona un artículo o un kit.")
        if articulo:
            articulo = get_object_or_404(
                Articulo, pk=articulo.pk, activo=True, categoria__activo=True
            )
        if kit:
            kit = get_object_or_404(Kit, pk=kit.pk, activo=True)
        linea, creada = carrito.lineas.get_or_create(
            articulo=articulo,
            kit=kit,
            defaults={
                "cantidad": cantidad,
                "composicion": kit.composicion() if kit else [],
                "revision": kit.revision if kit else 0,
            },
        )
        if not creada:
            linea.cantidad = entero(linea.cantidad + cantidad)
            linea.save(update_fields=["cantidad"])
        if carrito.lineas.count() > 100:
            raise ValidationError("El carrito admite hasta 100 líneas.")
    else:
        raise ValidationError("Acción de carrito inválida.")
    return carrito


@transaction.atomic
def guardar_plantilla(actor, version, nombre, reemplazar=False):
    actor = exigir_actor(actor)
    carrito = tomar_carrito(actor, version)
    # Una plantilla solo se guarda con catálogo y composición revisados.
    consolidar(carrito.lineas.select_related("articulo__categoria", "kit"))
    nombre = texto(nombre, "un nombre para la plantilla", 120)
    existente = PlantillaPedido.objects.filter(
        propietario=actor, nombre__iexact=nombre
    ).first()
    if existente and not reemplazar:
        raise ValidationError(
            "Ya tienes una plantilla con ese nombre. Confirma su actualización o usa otro nombre."
        )
    plantilla = existente or PlantillaPedido(propietario=actor, nombre=nombre)
    plantilla.nombre = nombre
    plantilla.full_clean()
    plantilla.save()
    plantilla.lineas.all().delete()
    LineaPlantilla.objects.bulk_create(
        [
            LineaPlantilla(
                plantilla=plantilla,
                articulo_id=l.articulo_id,
                kit_id=l.kit_id,
                cantidad=l.cantidad,
                composicion=l.composicion,
                revision=l.revision,
            )
            for l in carrito.lineas.all()
        ]
    )
    return plantilla


@transaction.atomic
def cargar_plantilla(actor, version, plantilla_id):
    actor = exigir_actor(actor)
    plantilla = get_object_or_404(PlantillaPedido, pk=plantilla_id, propietario=actor)
    carrito = tomar_carrito(actor, version)
    carrito.lineas.all().delete()
    LineaCarrito.objects.bulk_create(
        [
            LineaCarrito(
                carrito=carrito,
                articulo_id=l.articulo_id,
                kit_id=l.kit_id,
                cantidad=l.cantidad,
                composicion=l.composicion,
                revision=l.revision,
            )
            for l in plantilla.lineas.all()
        ]
    )
    return carrito


@transaction.atomic
def crear_solicitud(actor, version, clave, datos):
    actor = exigir_actor(actor)
    existente = Solicitud.objects.filter(clave=clave).first()
    if existente:
        if existente.propietario_id != actor.pk:
            raise PermissionDenied
        return existente
    carrito = tomar_carrito(actor, version)
    lineas, origen = consolidar(
        carrito.lineas.select_related("articulo__categoria", "kit")
    )
    if datos["tipo"] not in Solicitud.Tipo.values:
        raise ValidationError("Tipo de solicitante inválido.")
    from .academico_services import contexto_solicitud

    docencia, contexto = contexto_solicitud(actor, datos)
    solicitud = Solicitud(
        docencia=docencia,
        contexto=contexto,
        propietario=actor,
        clave=clave,
        tipo=datos["tipo"],
        motivo=texto(datos["motivo"]),
        fecha_necesaria=fecha_futura(datos["fecha_necesaria"]),
        procedencia=origen,
        carrera=datos.get("carrera"),
        asignatura=datos.get("asignatura"),
        profesor=contexto.get("docente", ""),
    )
    solicitud.full_clean()
    solicitud.save()
    LineaSolicitud.objects.bulk_create(
        [
            LineaSolicitud(solicitud=solicitud, articulo=a, nombre=a.nombre, cantidad=c)
            for a, c in lineas
        ]
    )
    carrito.lineas.all().delete()
    return solicitud


@transaction.atomic
def resolver_solicitud(actor, solicitud_id, aprobar, motivo):
    actor = exigir_actor(actor, operador=True)
    motivo = texto(motivo)
    estado = Solicitud.Estado.APROBADA if aprobar else Solicitud.Estado.RECHAZADA
    if not Solicitud.objects.filter(
        pk=solicitud_id, estado=Solicitud.Estado.PENDIENTE
    ).update(
        estado=estado,
        resolucion=motivo,
        resuelta_por=actor,
        resuelta_en=timezone.now(),
    ):
        raise ValidationError(
            "La solicitud ya fue resuelta. Recarga para consultar su estado."
        )


def descontar(actor, prestamo, lineas):
    for articulo, cantidad in sorted(lineas, key=lambda pair: pair[0].pk):
        entero(cantidad)
        if not Articulo.objects.filter(
            pk=articulo.pk,
            activo=True,
            # Evita un JOIN que movería el saldo a una subconsulta de IDs:
            # PostgreSQL debe revalidarlo al adquirir el bloqueo del UPDATE.
            categoria_id__in=Categoria.objects.filter(activo=True).values("pk"),
            disponible__gte=cantidad,
        ).update(disponible=F("disponible") - cantidad):
            raise ValidationError(
                f"Stock insuficiente o artículo deshabilitado: {articulo.nombre}. No se entregó el pedido."
            )
        LineaPrestamo.objects.create(
            prestamo=prestamo,
            articulo=articulo,
            nombre=articulo.nombre,
            cantidad=cantidad,
        )
        movimiento(
            actor, articulo, "entrega", prestamo.motivo, -cantidad, prestamo=prestamo
        )


@transaction.atomic
def entregar_solicitud(actor, solicitud_id, fecha_limite):
    actor = exigir_actor(actor, operador=True)
    solicitud = get_object_or_404(Solicitud, pk=solicitud_id)
    existente = Prestamo.objects.filter(solicitud=solicitud).first()
    if existente:
        return existente
    exigir_actor(solicitud.propietario)
    if not Solicitud.objects.filter(
        pk=solicitud.pk, estado=Solicitud.Estado.APROBADA
    ).update(estado=Solicitud.Estado.ENTREGADA):
        raise ValidationError("Solo puedes entregar solicitudes aprobadas.")
    prestamo = Prestamo.objects.create(
        solicitud=solicitud,
        beneficiario=solicitud.propietario,
        operador=actor,
        clave=solicitud.folio,
        fecha_limite=fecha_futura(fecha_limite),
        motivo=solicitud.motivo,
    )
    lineas = [
        (l.articulo, l.cantidad) for l in solicitud.lineas.select_related("articulo")
    ]
    if not lineas:
        raise ValidationError("El pedido no tiene artículos.")
    descontar(actor, prestamo, lineas)
    return prestamo


@transaction.atomic
def prestamo_directo(actor, version, clave, beneficiario, fecha_limite, motivo):
    actor = exigir_actor(actor, operador=True)
    existente = Prestamo.objects.filter(clave=clave).first()
    if existente:
        if existente.operador_id != actor.pk:
            raise PermissionDenied
        return existente
    beneficiario = exigir_actor(beneficiario)
    carrito = tomar_carrito(actor, version)
    lineas, _ = consolidar(carrito.lineas.select_related("articulo__categoria", "kit"))
    prestamo = Prestamo.objects.create(
        beneficiario=beneficiario,
        operador=actor,
        clave=clave,
        fecha_limite=fecha_futura(fecha_limite),
        motivo=texto(motivo),
    )
    descontar(actor, prestamo, lineas)
    carrito.lineas.all().delete()
    return prestamo


@transaction.atomic
def devolver(actor, prestamo_id, version, clave, recepciones, notas):
    actor = exigir_actor(actor, operador=True)
    existente = Devolucion.objects.filter(clave=clave).first()
    if existente:
        if existente.operador_id != actor.pk or existente.prestamo_id != prestamo_id:
            raise PermissionDenied
        return existente
    prestamo = get_object_or_404(Prestamo, pk=prestamo_id)
    entero(version, 0, 2147483647)
    if not Prestamo.objects.filter(
        pk=prestamo.pk, version=version, cerrado=False
    ).update(version=F("version") + 1):
        raise ValidationError(
            "El préstamo cambió o ya está cerrado. Recarga para revisar lo pendiente."
        )
    notas = texto(notas, "observaciones y motivo de cualquier pérdida")
    if not recepciones:
        raise ValidationError("Indica al menos una cantidad recibida o perdida.")
    devolucion = Devolucion.objects.create(
        prestamo=prestamo, operador=actor, clave=clave, notas=notas
    )
    vistos = set()
    for linea_id, utilizables, danados, perdidos in recepciones:
        for cantidad in (utilizables, danados, perdidos):
            entero(cantidad, 0)
        total = utilizables + danados + perdidos
        if total == 0:
            continue
        if linea_id in vistos:
            raise ValidationError("Hay una línea repetida en la devolución.")
        vistos.add(linea_id)
        linea = get_object_or_404(LineaPrestamo, pk=linea_id, prestamo=prestamo)
        if not LineaPrestamo.objects.filter(
            pk=linea.pk, resuelto__lte=F("cantidad") - total
        ).update(resuelto=F("resuelto") + total):
            raise ValidationError(
                f"La recepción supera la cantidad pendiente de {linea.nombre}."
            )
        Articulo.objects.filter(pk=linea.articulo_id).update(
            disponible=F("disponible") + utilizables,
            no_disponible=F("no_disponible") + danados,
        )
        LineaDevolucion.objects.create(
            devolucion=devolucion,
            linea=linea,
            utilizables=utilizables,
            danados=danados,
            perdidos=perdidos,
        )
        movimiento(
            actor,
            linea.articulo,
            "recepcion" if utilizables + danados else "perdida",
            notas,
            utilizables,
            danados,
            perdidos,
            prestamo,
            devolucion,
        )
    if not vistos:
        raise ValidationError("Indica al menos una cantidad recibida o perdida.")
    prestamo.cerrado = not prestamo.lineas.filter(resuelto__lt=F("cantidad")).exists()
    prestamo.save(update_fields=["cerrado"])
    return devolucion
