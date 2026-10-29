from calendar import monthrange
from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import F, Max, Q
from django.utils import timezone

from .models import Articulo, Categoria, Docencia, Kit, Solicitud
from .permissions import exigir_academico


def catalogo(data):
    q = data.get("q", "").strip()[:200]
    categoria = data.get("categoria", "")
    articulos = Articulo.objects.filter(
        activo=True, categoria__activo=True
    ).select_related("categoria")
    if q:
        articulos = articulos.filter(Q(nombre__icontains=q) | Q(codigo__icontains=q))
    if categoria:
        articulos = (
            articulos.filter(categoria_id=int(categoria))
            if categoria.isascii() and categoria.isdecimal() and len(categoria) < 18
            else articulos.none()
        )
    kits = Kit.objects.filter(activo=True)
    kq = data.get("kit_q", "").strip()[:200]
    if kq:
        kits = kits.filter(Q(nombre__icontains=kq) | Q(codigo__icontains=kq))
    return {
        "catalogo": Paginator(articulos, 20).get_page(data.get("page")),
        "categorias": Categoria.objects.filter(activo=True),
        "buscar": q,
        "categoria_elegida": categoria,
        "kits_catalogo": Paginator(kits, 10).get_page(data.get("kit_page")),
        "kit_buscar": kq,
    }


def seguimiento(actor, data, adicionales=False):
    exigir_academico(actor)
    docentes = Docencia.objects.select_related(
        "docente", "plan__asignatura", "plan__carrera", "plan__periodo"
    ).prefetch_related("plan__equipamiento__articulo")
    for campo in ("periodo", "carrera", "asignatura"):
        if data.get(campo):
            docentes = docentes.filter(**{f"plan__{campo}": data[campo]})
    if data.get("docente"):
        docentes = docentes.filter(docente=data["docente"])
    docs = list(docentes)
    solicitudes = (
        Solicitud.objects.filter(docencia__in=docs)
        .select_related("prestamo")
        .prefetch_related("lineas__articulo", "prestamo__lineas")
    )
    if not data.get("rechazadas"):
        solicitudes = solicitudes.exclude(estado="rechazada")
    if data.get("desde"):
        solicitudes = solicitudes.filter(fecha_necesaria__gte=data["desde"])
    if data.get("hasta"):
        solicitudes = solicitudes.filter(fecha_necesaria__lte=data["hasta"])
    grupos = {}
    for d in docs:
        if not adicionales:
            for equipo in d.plan.equipamiento.all():
                grupos[(d.pk, equipo.articulo_id, "docente", 0)] = [
                    d,
                    equipo.articulo,
                    [],
                    [],
                    set(),
                    "Pauta actual" if d.plan.configurada else "Pauta no configurada",
                    set(),
                ]
    for s in solicitudes:
        if not s.contexto.get("configurada"):
            continue
        entregadas = (
            {l.articulo_id: l.cantidad for l in s.prestamo.lineas.all()}
            if hasattr(s, "prestamo")
            else {}
        )
        for linea in s.lineas.all():
            previsto = linea.articulo_id in s.contexto.get("articulos", [])
            if adicionales == previsto:
                continue
            key = (s.docencia_id, linea.articulo_id, s.tipo, 0)
            if key not in grupos:
                d = next(d for d in docs if d.pk == s.docencia_id)
                grupos[key] = [
                    d,
                    linea.articulo,
                    [],
                    [],
                    set(),
                    "Pauta histórica",
                    set(),
                ]
            g = grupos[key]
            g[6].add(s.contexto["revision"])
            g[2].append((s.pk, linea.cantidad, s.creada, s.estado))
            if linea.articulo_id in entregadas:
                g[3].append((entregadas[linea.articulo_id], s.prestamo.entregado))
            g[4].add(s.fecha_necesaria)
    filas = []
    for (_, _, origen, revision), (
        d,
        articulo,
        pedidos,
        entregas,
        dias,
        pauta,
        revisiones,
    ) in grupos.items():
        if data.get("categoria") and articulo.categoria_id != data["categoria"].pk:
            continue
        if (
            data.get("q")
            and data["q"].casefold()
            not in (articulo.nombre + " " + articulo.codigo).casefold()
        ):
            continue
        veces = len(pedidos)
        filtro = data.get("veces")
        if filtro and not (veces > 5 if filtro == "mas" else veces == int(filtro)):
            continue
        filas.append(
            {
                "docencia": d,
                "articulo": articulo,
                "origen": origen,
                "revision": ", ".join(str(r) for r in sorted(revisiones))
                or str(d.plan.revision),
                "pauta": pauta,
                "veces": veces,
                "unidades": sum(p[1] for p in pedidos),
                "entregas": len(entregas),
                "entregadas": sum(e[0] for e in entregas),
                "dias": len(dias),
                "ultima": max((p[2] for p in pedidos), default=None),
                "ultima_entrega": max((e[1] for e in entregas), default=None),
                "pedidos": pedidos,
            }
        )
    filas.sort(key=lambda r: (-r["veces"], r["docencia"].pk, r["articulo"].nombre))
    historicas = Solicitud.objects.all()
    for campo in ("carrera", "asignatura"):
        if data.get(campo):
            historicas = historicas.filter(**{campo: data[campo]})
    if data.get("periodo"):
        historicas = historicas.filter(docencia__plan__periodo=data["periodo"])
    if data.get("docente"):
        historicas = historicas.filter(docencia__docente=data["docente"])
    if data.get("desde"):
        historicas = historicas.filter(fecha_necesaria__gte=data["desde"])
    if data.get("hasta"):
        historicas = historicas.filter(fecha_necesaria__lte=data["hasta"])
    if not data.get("rechazadas"):
        historicas = historicas.exclude(estado="rechazada")
    if data.get("categoria"):
        historicas = historicas.filter(lineas__articulo__categoria=data["categoria"])
    if data.get("q"):
        historicas = historicas.filter(
            Q(lineas__articulo__nombre__icontains=data["q"])
            | Q(lineas__articulo__codigo__icontains=data["q"])
        )
    desconocidos = (
        historicas.filter(
            Q(docencia__isnull=True) | Q(contexto__configurada=False) | Q(contexto={})
        )
        .distinct()
        .count()
    )
    return filas, desconocidos


def corte_inactividad(numero, unidad, hoy=None):
    hoy = hoy or timezone.localdate()
    if unidad == "semanas":
        return hoy - timedelta(weeks=numero)
    meses = numero * (12 if unidad == "anos" else 1)
    indice = hoy.year * 12 + hoy.month - 1 - meses
    ano, mes = divmod(indice, 12)
    mes += 1
    return hoy.replace(year=ano, month=mes, day=min(hoy.day, monthrange(ano, mes)[1]))


def inactividad(actor, data):
    exigir_academico(actor)
    corte = corte_inactividad(data["numero"], data["unidad"])
    qs = Articulo.objects.select_related("categoria").annotate(
        ultimo_prestamo=Max("lineaprestamo__prestamo__entregado"),
        ultimo_movimiento=Max("movimientos__fecha"),
    )
    if not data.get("inactivos"):
        qs = qs.filter(activo=True)
    if data.get("categoria"):
        qs = qs.filter(categoria=data["categoria"])
    if data.get("q"):
        qs = qs.filter(Q(nombre__icontains=data["q"]) | Q(codigo__icontains=data["q"]))
    abiertos = set(
        Articulo.objects.filter(
            lineaprestamo__prestamo__cerrado=False,
            lineaprestamo__resuelto__lt=F("lineaprestamo__cantidad"),
        ).values_list("pk", flat=True)
    )
    filas = []
    for a in qs:
        ultima = (
            a.ultimo_movimiento if data["modo"] == "movimientos" else a.ultimo_prestamo
        )
        fecha = timezone.localtime(ultima).date() if ultima else None
        alta = timezone.localtime(a.creada).date() if a.creada else None
        if (fecha and fecha <= corte) or (
            not fecha and (alta is None or alta <= corte)
        ):
            filas.append(
                {
                    "articulo": a,
                    "ultima": ultima,
                    "desconocida": not fecha and alta is None,
                    "abierto": a.pk in abiertos,
                }
            )
    return filas, corte
