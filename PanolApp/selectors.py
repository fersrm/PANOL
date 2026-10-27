from datetime import timedelta

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Articulo, Devolucion, Prestamo, Solicitud
from .permissions import puede_operar, puede_solicitar


def solicitudes_visibles(user):
    qs = Solicitud.objects.select_related(
        "propietario", "carrera", "asignatura", "resuelta_por"
    )
    return qs if puede_operar(user) else qs.filter(propietario=user)


def prestamos_visibles(user):
    qs = Prestamo.objects.select_related(
        "beneficiario", "operador", "solicitud"
    ).prefetch_related("lineas")
    return qs if puede_operar(user) else qs.filter(beneficiario=user)


def dashboard(user):
    if not puede_solicitar(user):
        return {"panol_habilitado": False}
    globales = puede_operar(user)
    hoy = timezone.localdate()
    inicio = hoy - timedelta(days=29)
    solicitudes = solicitudes_visibles(user)
    prestamos = prestamos_visibles(user)
    recepciones = Devolucion.objects.filter(
        Q(lineas__utilizables__gt=0) | Q(lineas__danados__gt=0)
    )
    if not globales:
        recepciones = recepciones.filter(prestamo__beneficiario=user)
    tarjetas = [
        {
            "etiqueta": "Préstamos abiertos",
            "valor": prestamos.filter(cerrado=False).count(),
        },
        {
            "etiqueta": "Préstamos vencidos",
            "valor": prestamos.filter(cerrado=False, fecha_limite__lt=hoy).count(),
        },
        {
            "etiqueta": "Solicitudes pendientes",
            "valor": solicitudes.filter(estado="pendiente").count(),
        },
        {
            "etiqueta": "Aprobadas por entregar",
            "valor": solicitudes.filter(estado="aprobada").count(),
        },
    ]
    if globales:
        articulos = Articulo.objects.filter(activo=True, categoria__activo=True)
        tarjetas += [
            {"etiqueta": "Artículos activos", "valor": articulos.count()},
            {
                "etiqueta": "Unidades disponibles",
                "valor": articulos.aggregate(n=Sum("disponible"))["n"] or 0,
            },
            {
                "etiqueta": "Artículos bajo mínimo",
                "valor": articulos.filter(disponible__lt=F("minimo")).count(),
            },
        ]
    entregas = {
        row["dia"]: row["n"]
        for row in prestamos.filter(entregado__date__range=(inicio, hoy))
        .order_by()
        .annotate(dia=TruncDate("entregado"))
        .values("dia")
        .annotate(n=Count("pk"))
    }
    devueltos = {
        row["dia"]: row["n"]
        for row in recepciones.filter(fecha__date__range=(inicio, hoy))
        .order_by()
        .annotate(dia=TruncDate("fecha"))
        .values("dia")
        .annotate(n=Count("pk", distinct=True))
    }
    dias = [inicio + timedelta(days=i) for i in range(30)]
    return {
        "panol_habilitado": True,
        "panol_global": globales,
        "panol_tarjetas": tarjetas,
        "panol_recientes": list(prestamos[:5]),
        "panol_solicitudes": list(solicitudes[:5]),
        "panol_grafico": {
            "dias": [d.strftime("%d/%m") for d in dias],
            "entregas": [entregas.get(d, 0) for d in dias],
            "devoluciones": [devueltos.get(d, 0) for d in dias],
        },
        "panol_total_entregas": sum(entregas.values()),
        "panol_total_recepciones": sum(devueltos.values()),
    }
