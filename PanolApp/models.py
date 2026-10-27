import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

USER = settings.AUTH_USER_MODEL
POSITIVE = [MinValueValidator(1), MaxValueValidator(9999)]


class NombreActivo(models.Model):
    nombre = models.CharField("nombre", max_length=200)
    activo = models.BooleanField("habilitado", default=True)

    class Meta:
        abstract = True
        ordering = ["nombre", "pk"]

    def __str__(self):
        return self.nombre


class Categoria(NombreActivo):
    descripcion = models.TextField("descripción", max_length=2000, blank=True)

    class Meta(NombreActivo.Meta):
        constraints = [
            models.UniqueConstraint(Lower("nombre"), name="panol_categoria_nombre")
        ]


class Area(NombreActivo):
    class Meta(NombreActivo.Meta):
        constraints = [
            models.UniqueConstraint(Lower("nombre"), name="panol_area_nombre")
        ]


class Carrera(NombreActivo):
    codigo = models.CharField("código", max_length=50, unique=True)
    area = models.ForeignKey(Area, on_delete=models.PROTECT, verbose_name="área")


class Asignatura(NombreActivo):
    codigo = models.CharField("código", max_length=50, unique=True)
    carreras = models.ManyToManyField(Carrera, blank=True, verbose_name="carreras")


class Articulo(NombreActivo):
    codigo = models.CharField("código", max_length=50, unique=True)
    descripcion = models.TextField("descripción", max_length=2000, blank=True)
    categoria = models.ForeignKey(
        Categoria, on_delete=models.PROTECT, verbose_name="categoría"
    )
    minimo = models.PositiveIntegerField("mínimo", default=0)
    disponible = models.PositiveIntegerField(default=0, editable=False)
    no_disponible = models.PositiveIntegerField(default=0, editable=False)

    class Meta(NombreActivo.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(disponible__gte=0, no_disponible__gte=0),
                name="panol_saldos_positivos",
            )
        ]

    @property
    def bajo_minimo(self):
        return self.disponible < self.minimo


class Kit(NombreActivo):
    codigo = models.CharField("código", max_length=50, unique=True)
    descripcion = models.TextField("descripción", max_length=2000, blank=True)
    revision = models.PositiveIntegerField(default=1, editable=False)

    @property
    def disponibilidad(self):
        componentes = list(self.componentes.select_related("articulo__categoria"))
        if not self.activo or not componentes:
            return 0
        return min(
            (
                c.articulo.disponible // c.cantidad
                if c.articulo.activo and c.articulo.categoria.activo
                else 0
            )
            for c in componentes
        )

    def composicion(self):
        return [
            {
                "articulo": c.articulo_id,
                "nombre": c.articulo.nombre,
                "cantidad": c.cantidad,
            }
            for c in self.componentes.select_related("articulo").order_by("articulo_id")
        ]


class ComponenteKit(models.Model):
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name="componentes")
    articulo = models.ForeignKey(
        Articulo, on_delete=models.PROTECT, verbose_name="artículo"
    )
    cantidad = models.PositiveIntegerField(validators=POSITIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kit", "articulo"], name="panol_componente_unico"
            ),
            models.CheckConstraint(
                condition=Q(cantidad__gte=1, cantidad__lte=9999),
                name="panol_componente_cantidad",
            ),
        ]


class Carrito(models.Model):
    propietario = models.OneToOneField(USER, on_delete=models.CASCADE)
    version = models.PositiveIntegerField(default=0)


class PlantillaPedido(models.Model):
    propietario = models.ForeignKey(USER, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=120)

    class Meta:
        ordering = ["nombre", "pk"]
        constraints = [
            models.UniqueConstraint(
                Lower("nombre"), "propietario", name="panol_plantilla_nombre"
            )
        ]


class Seleccion(models.Model):
    articulo = models.ForeignKey(
        Articulo,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name="artículo",
    )
    kit = models.ForeignKey(Kit, null=True, blank=True, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField(validators=POSITIVE)
    composicion = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True

    @property
    def nombre(self):
        return str(self.kit or self.articulo)

    @property
    def cambio(self):
        return bool(
            self.kit_id
            and (
                self.kit.revision != self.revision
                or self.kit.composicion() != self.composicion
            )
        )


def restricciones_seleccion(cabecera, prefijo):
    return [
        models.CheckConstraint(
            condition=(
                Q(articulo__isnull=False, kit__isnull=True)
                | Q(articulo__isnull=True, kit__isnull=False)
            ),
            name=f"{prefijo}_destino",
        ),
        models.CheckConstraint(
            condition=Q(cantidad__gte=1, cantidad__lte=9999), name=f"{prefijo}_cantidad"
        ),
        models.UniqueConstraint(
            fields=[cabecera, "articulo"], name=f"{prefijo}_articulo"
        ),
        models.UniqueConstraint(fields=[cabecera, "kit"], name=f"{prefijo}_kit"),
    ]


class LineaCarrito(Seleccion):
    carrito = models.ForeignKey(
        Carrito, on_delete=models.CASCADE, related_name="lineas"
    )

    class Meta:
        ordering = ["pk"]
        constraints = restricciones_seleccion("carrito", "panol_carrito")


class LineaPlantilla(Seleccion):
    plantilla = models.ForeignKey(
        PlantillaPedido, on_delete=models.CASCADE, related_name="lineas"
    )

    class Meta:
        ordering = ["pk"]
        constraints = restricciones_seleccion("plantilla", "panol_plantilla")


class Solicitud(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de revisión"
        APROBADA = "aprobada", "Aprobada · por entregar"
        RECHAZADA = "rechazada", "Rechazada"
        ENTREGADA = "entregada", "Entregada"

    class Tipo(models.TextChoices):
        DOCENTE = "docente", "Docente"
        ESTUDIANTE = "estudiante", "Estudiante"

    propietario = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="solicitudes_panol"
    )
    folio = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    clave = models.UUIDField(unique=True, editable=False)
    tipo = models.CharField("tipo de solicitante", max_length=20, choices=Tipo.choices)
    motivo = models.TextField(max_length=2000)
    fecha_necesaria = models.DateField("fecha necesaria")
    creada = models.DateTimeField(default=timezone.now, editable=False)
    estado = models.CharField(
        max_length=20, choices=Estado.choices, default=Estado.PENDIENTE
    )
    carrera = models.ForeignKey(
        Carrera, null=True, blank=True, on_delete=models.PROTECT
    )
    asignatura = models.ForeignKey(
        Asignatura, null=True, blank=True, on_delete=models.PROTECT
    )
    profesor = models.CharField(max_length=200, blank=True)
    procedencia = models.JSONField(default=list)
    resolucion = models.TextField(max_length=2000, blank=True)
    resuelta_por = models.ForeignKey(
        USER,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="resoluciones_panol",
    )
    resuelta_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creada", "-pk"]
        indexes = [models.Index(fields=["propietario", "estado"])]

    def __str__(self):
        return f"SOL-{self.pk:06d}" if self.pk else "Solicitud nueva"


class LineaSolicitud(models.Model):
    solicitud = models.ForeignKey(
        Solicitud, on_delete=models.CASCADE, related_name="lineas"
    )
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    nombre = models.CharField(max_length=200)
    cantidad = models.PositiveIntegerField(validators=POSITIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["solicitud", "articulo"], name="panol_solicitud_articulo"
            ),
            models.CheckConstraint(
                condition=Q(cantidad__gte=1, cantidad__lte=9999),
                name="panol_solicitud_cantidad",
            ),
        ]


class Prestamo(models.Model):
    beneficiario = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="prestamos_panol"
    )
    solicitud = models.OneToOneField(
        Solicitud,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prestamo",
    )
    operador = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="entregas_panol"
    )
    clave = models.UUIDField(unique=True, editable=False)
    entregado = models.DateTimeField(default=timezone.now, editable=False)
    fecha_limite = models.DateField("fecha límite de devolución")
    motivo = models.TextField(max_length=2000)
    cerrado = models.BooleanField(default=False, editable=False)
    version = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["-entregado", "-pk"]
        indexes = [models.Index(fields=["beneficiario", "cerrado", "fecha_limite"])]

    def __str__(self):
        return f"PRE-{self.pk:06d}"

    @property
    def vencido(self):
        return not self.cerrado and self.fecha_limite < timezone.localdate()

    @property
    def estado(self):
        if self.cerrado:
            return "Cerrado"
        if any(l.resuelto for l in self.lineas.all()):
            return "Parcialmente devuelto"
        return "Abierto"


class LineaPrestamo(models.Model):
    prestamo = models.ForeignKey(
        Prestamo, on_delete=models.CASCADE, related_name="lineas"
    )
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    nombre = models.CharField(max_length=200)
    cantidad = models.PositiveIntegerField(validators=POSITIVE)
    resuelto = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["prestamo", "articulo"], name="panol_prestamo_articulo"
            ),
            models.CheckConstraint(
                condition=Q(
                    cantidad__gte=1, cantidad__lte=9999, resuelto__lte=F("cantidad")
                ),
                name="panol_prestamo_pendiente",
            ),
        ]

    @property
    def pendiente(self):
        return self.cantidad - self.resuelto


class Devolucion(models.Model):
    prestamo = models.ForeignKey(
        Prestamo, on_delete=models.PROTECT, related_name="devoluciones"
    )
    operador = models.ForeignKey(USER, on_delete=models.PROTECT)
    fecha = models.DateTimeField(default=timezone.now)
    clave = models.UUIDField(unique=True, editable=False)
    notas = models.TextField("observaciones / motivo de pérdida", max_length=2000)

    class Meta:
        ordering = ["-fecha", "-pk"]


class LineaDevolucion(models.Model):
    devolucion = models.ForeignKey(
        Devolucion, on_delete=models.CASCADE, related_name="lineas"
    )
    linea = models.ForeignKey(LineaPrestamo, on_delete=models.PROTECT)
    utilizables = models.PositiveIntegerField(default=0)
    danados = models.PositiveIntegerField(default=0)
    perdidos = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["devolucion", "linea"], name="panol_recepcion_linea"
            ),
            models.CheckConstraint(
                condition=Q(utilizables__gt=0) | Q(danados__gt=0) | Q(perdidos__gt=0),
                name="panol_recepcion_no_vacia",
            ),
        ]


class MovimientoInventario(models.Model):
    articulo = models.ForeignKey(
        Articulo, on_delete=models.PROTECT, related_name="movimientos"
    )
    actor = models.ForeignKey(USER, on_delete=models.PROTECT)
    fecha = models.DateTimeField(default=timezone.now)
    tipo = models.CharField(max_length=30)
    delta_disponible = models.IntegerField(default=0)
    delta_no_disponible = models.IntegerField(default=0)
    perdida = models.PositiveIntegerField(default=0)
    motivo = models.TextField(max_length=2000)
    prestamo = models.ForeignKey(Prestamo, null=True, on_delete=models.PROTECT)
    devolucion = models.ForeignKey(Devolucion, null=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-fecha", "-pk"]
