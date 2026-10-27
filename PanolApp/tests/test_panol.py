import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from PanolApp import services
from PanolApp.models import (
    Articulo,
    Carrito,
    Categoria,
    Devolucion,
    Kit,
    MovimientoInventario,
    PlantillaPedido,
    Prestamo,
    Solicitud,
)
from PanolApp.selectors import dashboard
from PanolApp.views import token
from UsuarioApp.models import Position, Profile, UserVisualPreference


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = cls.usuario("operador_admin", "ADMIN")
        cls.manager = cls.usuario("operador_manager", "MANAGER")
        cls.persona = cls.usuario("solicitante_a", "RESTRICTED")
        cls.otra = cls.usuario("solicitante_b", "RESTRICTED")
        cls.sin_perfil = cls.usuario("sin_perfil")
        cls.root = cls.usuario("root", superuser=True)
        cls.inactivo = cls.usuario("inactivo", "ADMIN", active=False)
        cls.sin_cargo = cls.usuario("sin_cargo")
        Profile.objects.create(user_FK=cls.sin_cargo)
        cls.desconocido = cls.usuario("desconocido", "DESCONOCIDO")
        cls.categoria = Categoria.objects.create(nombre="Redes")
        cls.cable = services.guardar_articulo(
            cls.admin,
            Articulo(
                codigo="cab-01",
                nombre="Cable de red",
                categoria=cls.categoria,
                minimo=3,
            ),
            10,
        )
        cls.adaptador = services.guardar_articulo(
            cls.admin,
            Articulo(codigo="ada-01", nombre="Adaptador", categoria=cls.categoria),
            8,
        )
        cls.kit = services.guardar_kit(
            cls.admin,
            Kit(codigo="RED", nombre="Kit de redes"),
            [(cls.cable, 2), (cls.adaptador, 1)],
        )

    @classmethod
    def usuario(cls, nombre, cargo=None, superuser=False, active=True):
        user = User.objects.create_user(
            username=nombre,
            password="Prueba-Local-2026!",
            is_superuser=superuser,
            is_active=active,
        )
        if cargo:
            position, _ = Position.objects.get_or_create(
                permission_code=cargo, defaults={"user_position": cargo}
            )
            Profile.objects.create(user_FK=user, position_FK=position)
        return user

    def version(self, actor):
        return (
            Carrito.objects.filter(propietario=actor)
            .values_list("version", flat=True)
            .first()
            or 0
        )

    def agregar(self, actor=None, **kwargs):
        actor = actor or self.persona
        if not kwargs:
            kwargs = {"articulo": self.cable, "cantidad": 2}
        return services.cambiar_carrito(actor, self.version(actor), "agregar", **kwargs)

    def solicitar(self, actor=None, cantidad=2, clave=None):
        actor = actor or self.persona
        self.agregar(actor, articulo=self.cable, cantidad=cantidad)
        return services.crear_solicitud(
            actor,
            self.version(actor),
            clave or uuid.uuid4(),
            {
                "tipo": "docente",
                "motivo": "Práctica de redes",
                "fecha_necesaria": timezone.localdate(),
            },
        )

    def prestar(self, cantidad=2, actor=None):
        solicitud = self.solicitar(actor, cantidad)
        services.resolver_solicitud(
            self.admin, solicitud.pk, True, "Materiales autorizados"
        )
        return services.entregar_solicitud(
            self.manager, solicitud.pk, timezone.localdate() + timedelta(days=2)
        )

    def saldo(self):
        self.cable.refresh_from_db()
        return self.cable.disponible, self.cable.no_disponible


class InventarioKitsTests(Base):
    def test_ingreso_codigo_normalizado_y_movimiento(self):
        self.assertEqual(self.cable.codigo, "CAB-01")
        movimiento = self.cable.movimientos.get()
        self.assertEqual(
            (movimiento.delta_disponible, movimiento.actor_id), (10, self.admin.pk)
        )
        self.assertEqual(self.kit.disponibilidad, 5)

    def test_ajuste_no_puede_dejar_saldo_negativo(self):
        with self.assertRaises(ValidationError):
            services.ajustar_stock(self.manager, self.cable.pk, -11, 0, "Conteo")
        self.assertEqual(self.saldo(), (10, 0))
        self.assertEqual(self.cable.movimientos.count(), 1)

    def test_ajuste_rehabilita_danados_sin_inventar_unidades(self):
        services.ajustar_stock(self.admin, self.cable.pk, -2, 2, "Revisión técnica")
        services.ajustar_stock(
            self.admin, self.cable.pk, 1, -1, "Reparación comprobada"
        )
        self.assertEqual(self.saldo(), (9, 1))

    def test_editar_descripcion_no_sobrescribe_saldo_concurrente(self):
        antiguo = Articulo.objects.get(pk=self.cable.pk)
        services.ajustar_stock(self.admin, self.cable.pk, -3, 0, "Conteo")
        antiguo.descripcion = "Nuevo texto"
        services.guardar_articulo(self.admin, antiguo)
        self.assertEqual(self.saldo(), (7, 0))

    def test_constraint_saldo_y_componente_invalido(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Articulo.objects.filter(pk=self.cable.pk).update(disponible=-1)
        with self.assertRaises(ValidationError):
            services.guardar_kit(
                self.admin, Kit(codigo="X", nombre="Inválido"), [(self.cable, 0)]
            )
        with self.assertRaises(ValidationError):
            services.guardar_kit(
                self.admin,
                Kit(codigo="X", nombre="Inválido"),
                [(self.cable, 1), (self.cable, 1)],
            )

    def test_articulo_duplicado_no_crea_ingreso(self):
        with self.assertRaises(ValidationError):
            services.guardar_articulo(
                self.admin,
                Articulo(
                    codigo=" cab-01 ", nombre="Duplicado", categoria=self.categoria
                ),
                5,
            )
        self.assertEqual(Articulo.objects.count(), 2)

    def test_cantidades_estrictas_y_limites_expandidos(self):
        for cantidad in (0, -1, 1.5, True, 10000):
            with self.subTest(cantidad=cantidad), self.assertRaises(ValidationError):
                self.agregar(cantidad=cantidad, articulo=self.cable)
        self.agregar(kit=self.kit, cantidad=5000)
        with self.assertRaises(ValidationError):
            services.consolidar(
                Carrito.objects.get(propietario=self.persona).lineas.all()
            )

    def test_kit_modificado_exige_revision_y_conserva_solicitud_anterior(self):
        self.agregar(kit=self.kit, cantidad=1)
        plantilla = services.guardar_plantilla(
            self.persona, self.version(self.persona), "Mi clase"
        )
        solicitud = services.crear_solicitud(
            self.persona,
            self.version(self.persona),
            uuid.uuid4(),
            {
                "tipo": "docente",
                "motivo": "Clase",
                "fecha_necesaria": timezone.localdate(),
            },
        )
        services.guardar_kit(self.admin, self.kit, [(self.cable, 3)], self.kit.revision)
        services.cargar_plantilla(
            self.persona, self.version(self.persona), plantilla.pk
        )
        with self.assertRaises(ValidationError):
            services.consolidar(
                Carrito.objects.get(propietario=self.persona).lineas.select_related(
                    "kit"
                )
            )
        services.cambiar_carrito(self.persona, self.version(self.persona), "revisar")
        detalle, _ = services.consolidar(
            Carrito.objects.get(propietario=self.persona).lineas.select_related("kit")
        )
        self.assertEqual([(a.pk, c) for a, c in detalle], [(self.cable.pk, 3)])
        self.assertEqual(solicitud.lineas.get(articulo=self.cable).cantidad, 2)

    def test_revision_concurrente_de_kit_no_borra_componentes(self):
        services.guardar_kit(self.admin, self.kit, [(self.cable, 3)], 1)
        with self.assertRaises(ValidationError):
            services.guardar_kit(self.manager, self.kit, [(self.cable, 9)], 1)
        self.assertEqual(self.kit.componentes.get().cantidad, 3)


class CarritoSolicitudesTests(Base):
    def test_guardar_y_cargar_privado_sin_mover_stock(self):
        self.agregar(kit=self.kit, cantidad=2)
        plantilla = services.guardar_plantilla(
            self.persona, self.version(self.persona), "Clase habitual"
        )
        services.cambiar_carrito(self.persona, self.version(self.persona), "vaciar")
        services.cargar_plantilla(
            self.persona, self.version(self.persona), plantilla.pk
        )
        self.assertEqual(
            Carrito.objects.get(propietario=self.persona).lineas.get().cantidad, 2
        )
        self.assertEqual(self.saldo(), (10, 0))
        self.client.force_login(self.otra)
        self.assertEqual(
            self.client.get(
                reverse("panol:plantilla", args=[plantilla.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("panol:plantilla", args=[plantilla.pk]),
                {"accion": "eliminar", "confirmar": "si"},
            ).status_code,
            404,
        )
        self.assertTrue(PlantillaPedido.objects.filter(pk=plantilla.pk).exists())

    def test_version_evitar_pisado_entre_pestanas(self):
        self.agregar()
        with self.assertRaises(ValidationError):
            services.cambiar_carrito(self.persona, 0, "vaciar")
        self.assertEqual(
            Carrito.objects.get(propietario=self.persona).lineas.count(), 1
        )

    def test_get_no_crea_carrito_ni_documentos(self):
        self.client.force_login(self.persona)
        for url in ("panol:carrito", "panol:enviar", "panol:plantillas", "panol:kits"):
            self.assertEqual(self.client.get(reverse(url)).status_code, 200)
        self.assertFalse(Carrito.objects.exists())
        self.assertFalse(Solicitud.objects.exists())

    def test_plantilla_nombre_unico_por_propietario(self):
        self.agregar()
        services.guardar_plantilla(self.persona, self.version(self.persona), "Mi clase")
        with self.assertRaises(ValidationError):
            services.guardar_plantilla(
                self.persona, self.version(self.persona), "mi CLASE"
            )
        self.agregar(self.otra)
        services.guardar_plantilla(self.otra, self.version(self.otra), "Mi clase")
        self.assertEqual(PlantillaPedido.objects.count(), 2)

    def test_solicitud_consolida_kits_y_articulos_sin_descontar(self):
        self.agregar(kit=self.kit, cantidad=2)
        self.agregar(articulo=self.cable, cantidad=1)
        solicitud = services.crear_solicitud(
            self.persona,
            self.version(self.persona),
            uuid.uuid4(),
            {
                "tipo": "estudiante",
                "motivo": "Práctica",
                "fecha_necesaria": timezone.localdate(),
            },
        )
        self.assertEqual(solicitud.lineas.get(articulo=self.cable).cantidad, 5)
        self.assertEqual(solicitud.lineas.get(articulo=self.adaptador).cantidad, 2)
        self.assertEqual(self.saldo(), (10, 0))
        self.assertEqual(solicitud.propietario_id, self.persona.pk)

    def test_solicitud_idempotente_permite_pedido_nuevo_intencional(self):
        operacion = uuid.uuid4()
        primero = self.solicitar(clave=operacion)
        repetido = services.crear_solicitud(self.persona, 0, operacion, {})
        nuevo = self.solicitar()
        self.assertEqual(primero.pk, repetido.pk)
        self.assertNotEqual(primero.pk, nuevo.pk)

    def test_fecha_pasada_y_carrito_vacio_no_envian(self):
        with self.assertRaises(ValidationError):
            services.crear_solicitud(self.persona, 0, uuid.uuid4(), {})
        self.agregar()
        with self.assertRaises(ValidationError):
            services.crear_solicitud(
                self.persona,
                self.version(self.persona),
                uuid.uuid4(),
                {
                    "tipo": "docente",
                    "motivo": "Clase",
                    "fecha_necesaria": timezone.localdate() - timedelta(days=1),
                },
            )
        self.assertFalse(Solicitud.objects.exists())
        self.assertEqual(
            Carrito.objects.get(propietario=self.persona).lineas.count(), 1
        )

    def test_articulo_desactivado_se_ve_en_carrito_pero_bloquea_envio(self):
        self.agregar()
        Articulo.objects.filter(pk=self.cable.pk).update(activo=False)
        self.client.force_login(self.persona)
        self.assertContains(
            self.client.get(reverse("panol:carrito")), "Artículo deshabilitado"
        )
        with self.assertRaises(ValidationError):
            services.crear_solicitud(
                self.persona,
                self.version(self.persona),
                uuid.uuid4(),
                {
                    "tipo": "docente",
                    "motivo": "Clase",
                    "fecha_necesaria": timezone.localdate(),
                },
            )


class PrestamosDevolucionesTests(Base):
    def test_aprobar_no_descontar_entregar_una_vez(self):
        solicitud = self.solicitar()
        services.resolver_solicitud(self.admin, solicitud.pk, True, "Autorizado")
        self.assertEqual(self.saldo(), (10, 0))
        uno = services.entregar_solicitud(
            self.admin, solicitud.pk, timezone.localdate()
        )
        dos = services.entregar_solicitud(
            self.manager, solicitud.pk, timezone.localdate()
        )
        self.assertEqual(uno.pk, dos.pk)
        self.assertEqual(self.saldo(), (8, 0))
        self.assertEqual(Prestamo.objects.count(), 1)

    def test_rechazo_requiere_motivo_no_admite_entrega(self):
        solicitud = self.solicitar()
        with self.assertRaises(ValidationError):
            services.resolver_solicitud(self.manager, solicitud.pk, False, "")
        services.resolver_solicitud(
            self.manager, solicitud.pk, False, "Sin disponibilidad en esa fecha"
        )
        with self.assertRaises(ValidationError):
            services.entregar_solicitud(
                self.manager, solicitud.pk, timezone.localdate()
            )
        self.assertEqual(self.saldo(), (10, 0))

    def test_solicitud_sin_entrega_no_crea_recepcion(self):
        solicitud = self.solicitar()
        services.resolver_solicitud(self.admin, solicitud.pk, True, "Aprobado")
        self.assertFalse(Prestamo.objects.exists())
        self.assertFalse(Devolucion.objects.exists())
        self.assertEqual(self.saldo(), (10, 0))

    def test_stock_escaso_revierte_segunda_linea_cabecera_y_movimientos(self):
        self.agregar(kit=self.kit, cantidad=1)
        solicitud = services.crear_solicitud(
            self.persona,
            self.version(self.persona),
            uuid.uuid4(),
            {
                "tipo": "docente",
                "motivo": "Clase",
                "fecha_necesaria": timezone.localdate(),
            },
        )
        services.resolver_solicitud(self.admin, solicitud.pk, True, "Aprobado")
        Articulo.objects.filter(pk=self.adaptador.pk).update(disponible=0)
        with self.assertRaises(ValidationError):
            services.entregar_solicitud(self.admin, solicitud.pk, timezone.localdate())
        self.assertEqual(self.saldo(), (10, 0))
        self.assertFalse(Prestamo.objects.exists())
        self.assertFalse(MovimientoInventario.objects.filter(tipo="entrega").exists())
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, "aprobada")

    def test_fallo_despues_de_escritura_real_revierte_todo(self):
        solicitud = self.solicitar()
        services.resolver_solicitud(self.admin, solicitud.pk, True, "Aprobado")
        with patch(
            "PanolApp.services.movimiento", side_effect=RuntimeError("Fallo sintético")
        ), self.assertRaises(RuntimeError):
            services.entregar_solicitud(self.admin, solicitud.pk, timezone.localdate())
        self.assertFalse(Prestamo.objects.exists())
        self.assertEqual(self.saldo(), (10, 0))

    def test_competencia_por_ultimas_unidades(self):
        primero = self.solicitar(cantidad=7)
        segundo = self.solicitar(self.otra, cantidad=7)
        for solicitud in (primero, segundo):
            services.resolver_solicitud(self.admin, solicitud.pk, True, "Aprobado")
        services.entregar_solicitud(self.admin, primero.pk, timezone.localdate())
        with self.assertRaises(ValidationError):
            services.entregar_solicitud(self.manager, segundo.pk, timezone.localdate())
        self.assertEqual(self.saldo(), (3, 0))

    def test_devolucion_parcial_dano_perdida_y_reintento(self):
        prestamo = self.prestar(cantidad=5)
        linea = prestamo.lineas.get()
        operacion = uuid.uuid4()
        devolucion = services.devolver(
            self.manager,
            prestamo.pk,
            0,
            operacion,
            [(linea.pk, 2, 1, 0)],
            "Recepción parcial con daño",
        )
        services.devolver(
            self.manager, prestamo.pk, 0, operacion, [(linea.pk, 2, 1, 0)], "Reintento"
        )
        self.assertEqual(self.saldo(), (7, 1))
        prestamo.refresh_from_db()
        self.assertFalse(prestamo.cerrado)
        self.assertEqual(prestamo.estado, "Parcialmente devuelto")
        services.devolver(
            self.admin,
            prestamo.pk,
            prestamo.version,
            uuid.uuid4(),
            [(linea.pk, 1, 0, 1)],
            "Pérdida constatada por operador",
        )
        self.assertEqual(self.saldo(), (8, 1))
        prestamo.refresh_from_db()
        self.assertTrue(prestamo.cerrado)
        self.assertEqual(Devolucion.objects.count(), 2)
        self.assertEqual(devolucion.lineas.get().danados, 1)

    def test_recepcion_excesiva_repetida_y_de_otro_prestamo(self):
        prestamo = self.prestar()
        linea = prestamo.lineas.get()
        with self.assertRaises(ValidationError):
            services.devolver(
                self.admin,
                prestamo.pk,
                0,
                uuid.uuid4(),
                [(linea.pk, 3, 0, 0)],
                "Recepción",
            )
        self.assertFalse(Devolucion.objects.exists())
        services.devolver(
            self.admin,
            prestamo.pk,
            0,
            uuid.uuid4(),
            [(linea.pk, 1, 0, 0)],
            "Recepción parcial",
        )
        with self.assertRaises(ValidationError):
            services.devolver(
                self.admin,
                prestamo.pk,
                0,
                uuid.uuid4(),
                [(linea.pk, 1, 0, 0)],
                "Formulario viejo",
            )
        self.assertEqual(self.saldo(), (9, 0))

    def test_desactivar_articulo_no_impide_devolver(self):
        prestamo = self.prestar()
        Articulo.objects.filter(pk=self.cable.pk).update(activo=False)
        services.devolver(
            self.admin,
            prestamo.pk,
            0,
            uuid.uuid4(),
            [(prestamo.lineas.get().pk, 2, 0, 0)],
            "Devuelto",
        )
        self.assertEqual(self.saldo(), (10, 0))

    def test_directo_desde_carrito_es_idempotente(self):
        self.agregar(self.admin, kit=self.kit, cantidad=1)
        operacion = uuid.uuid4()
        uno = services.prestamo_directo(
            self.admin,
            self.version(self.admin),
            operacion,
            self.persona,
            timezone.localdate(),
            "Soporte",
        )
        dos = services.prestamo_directo(
            self.admin, 0, operacion, self.persona, timezone.localdate(), "Soporte"
        )
        self.assertEqual(uno.pk, dos.pk)
        self.assertEqual(self.saldo(), (8, 0))


class InterfazPermisosTests(Base):
    def test_menu_destaca_seccion_tambien_en_detalles_y_movimientos(self):
        prestamo = self.prestar()
        self.agregar(self.admin)
        plantilla = services.guardar_plantilla(
            self.admin, self.version(self.admin), "Prueba de menú"
        )
        self.client.force_login(self.admin)
        casos = [
            ("Home", [], "Home", []),
            ("panol:articulo_editar", [self.cable.pk], "panol:inventario", []),
            ("panol:ajustar", [self.cable.pk], "panol:inventario", []),
            ("panol:kit_editar", [self.kit.pk], "panol:kits", []),
            ("panol:plantilla", [plantilla.pk], "panol:plantillas", []),
            ("panol:enviar", [], "panol:solicitudes", []),
            ("panol:solicitud", [prestamo.solicitud_id], "panol:solicitudes", []),
            ("panol:directo", [], "panol:prestamos", []),
            ("panol:devolver", [prestamo.pk], "panol:prestamos", []),
            ("panol:consultas", [], "panol:consultas", []),
            ("panol:catalogo_nuevo", ["areas"], "panol:catalogo", ["categorias"]),
            (
                "panol:catalogo_editar",
                ["categorias", self.categoria.pk],
                "panol:catalogo",
                ["categorias"],
            ),
        ]
        for ruta, args, destino, destino_args in casos:
            with self.subTest(ruta=ruta):
                response = self.client.get(reverse(ruta, args=args))
                self.assertEqual(response.status_code, 200)
                actuales = []
                for atributos, contenido in re.findall(
                    r"<a\b([^>]*)>(.*?)</a>", response.content.decode(), re.DOTALL
                ):
                    if 'class="nav-link"' not in atributos:
                        continue
                    href = re.search(r'href="([^"]+)"', atributos).group(1)
                    if href.startswith("/panol/"):
                        self.assertIn("<svg", contenido)
                        self.assertIn('aria-hidden="true"', contenido)
                    if 'aria-current="page"' in atributos:
                        actuales.append(href)
                # El mismo menú se renderiza para escritorio y para el dialog móvil.
                self.assertEqual(actuales, [reverse(destino, args=destino_args)] * 2)

    def test_creacion_edicion_kit_por_formulario(self):
        self.client.force_login(self.manager)
        data = {
            "codigo": "nuevo-kit",
            "nombre": "Kit nuevo",
            "activo": "on",
            "revision": 1,
            "componentes-TOTAL_FORMS": 1,
            "componentes-INITIAL_FORMS": 0,
            "componentes-MIN_NUM_FORMS": 1,
            "componentes-MAX_NUM_FORMS": 100,
            "componentes-0-articulo": self.cable.pk,
            "componentes-0-cantidad": 3,
        }
        response = self.client.post(reverse("panol:kit_nuevo"), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        kit = Kit.objects.get(codigo="NUEVO-KIT")
        data.update(
            {
                "componentes-INITIAL_FORMS": 1,
                "componentes-0-id": kit.componentes.get().pk,
                "componentes-0-kit": kit.pk,
                "componentes-0-cantidad": 4,
            }
        )
        response = self.client.post(reverse("panol:kit_editar", args=[kit.pk]), data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(kit.componentes.get().cantidad, 4)

    def test_recepcion_por_formulario_y_reenvio(self):
        prestamo = self.prestar(cantidad=4)
        self.client.force_login(self.manager)
        url = reverse("panol:devolver", args=[prestamo.pk])
        page = self.client.get(url)
        data = {
            "version": 0,
            "token": page.context["form"]["token"].value(),
            "notas": "Tres recibidos; uno dañado",
            "form-TOTAL_FORMS": 1,
            "form-INITIAL_FORMS": 1,
            "form-MIN_NUM_FORMS": 0,
            "form-MAX_NUM_FORMS": 100,
            "form-0-linea": prestamo.lineas.get().pk,
            "form-0-utilizables": 2,
            "form-0-danados": 1,
            "form-0-perdidos": 0,
        }
        for _ in range(2):
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(self.saldo(), (8, 1))
        self.assertEqual(Devolucion.objects.count(), 1)

    def test_plantilla_se_actualiza_solo_con_confirmacion(self):
        self.agregar()
        plantilla = services.guardar_plantilla(
            self.persona, self.version(self.persona), "Clase"
        )
        self.agregar(articulo=self.adaptador, cantidad=1)
        services.guardar_plantilla(
            self.persona, self.version(self.persona), "Clase", reemplazar=True
        )
        self.assertEqual(PlantillaPedido.objects.count(), 1)
        self.assertEqual(plantilla.lineas.count(), 2)

    def test_id_de_linea_malformado_y_ajeno_no_mutan_carrito(self):
        self.agregar()
        otro = self.agregar(self.otra)
        self.client.force_login(self.persona)
        for idlinea, esperado in (("abc", 400), (str(otro.lineas.get().pk), 404)):
            response = self.client.post(
                reverse("panol:carrito"),
                {
                    "accion": "quitar",
                    "linea": idlinea,
                    "version": self.version(self.persona),
                },
            )
            self.assertEqual(response.status_code, esperado)
        self.assertEqual(
            Carrito.objects.get(propietario=self.persona).lineas.count(), 1
        )

    def test_matriz_catalogo_y_administracion_por_url(self):
        for actor, uso, operacion in (
            (self.admin, True, True),
            (self.manager, True, True),
            (self.persona, True, False),
            (self.root, True, True),
            (self.sin_perfil, False, False),
            (self.sin_cargo, False, False),
            (self.desconocido, False, False),
        ):
            self.client.force_login(actor)
            with self.subTest(actor=actor.username):
                self.assertEqual(
                    self.client.get(reverse("panol:inventario")).status_code,
                    200 if uso else 403,
                )
                self.assertEqual(
                    self.client.get(reverse("panol:articulo_nuevo")).status_code,
                    200 if operacion else 403,
                )
                self.assertEqual(
                    self.client.post(
                        reverse("panol:ajustar", args=[self.cable.pk]),
                        {"disponible": 1, "no_disponible": 0, "motivo": "Conteo"},
                    ).status_code,
                    302 if operacion else 403,
                )
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panol:inventario")).status_code, 302)
        self.client.force_login(self.inactivo)
        self.assertEqual(self.client.get(reverse("panol:inventario")).status_code, 302)

    def test_propiedad_de_documentos_y_denegacion_post(self):
        prestamo = self.prestar()
        self.client.force_login(self.otra)
        for ruta, pk in (
            ("panol:solicitud", prestamo.solicitud_id),
            ("panol:prestamo", prestamo.pk),
        ):
            self.assertEqual(self.client.get(reverse(ruta, args=[pk])).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("panol:solicitud", args=[prestamo.solicitud_id]),
                {"accion": "entregar"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("panol:devolver", args=[prestamo.pk])).status_code,
            403,
        )
        self.assertNotContains(
            self.client.get(reverse("panol:prestamos")), str(prestamo)
        )

    def test_revocacion_entre_get_y_post_y_en_servicio(self):
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse("panol:articulo_nuevo")).status_code, 200
        )
        Profile.objects.filter(user_FK=self.manager).update(
            position_FK=self.persona.profile.position_FK
        )
        response = self.client.post(
            reverse("panol:ajustar", args=[self.cable.pk]),
            {"disponible": 1, "no_disponible": 0, "motivo": "Prueba"},
        )
        self.assertEqual(response.status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.ajustar_stock(self.manager, self.cable.pk, 1, 0, "Prueba")

    def test_csrf_y_metodo_incorrecto(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(
            client.post(
                reverse("panol:carrito"), {"accion": "vaciar", "version": 0}
            ).status_code,
            403,
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.delete(reverse("panol:prestamos")).status_code, 405
        )

    def test_todas_las_pantallas_y_modos_renderizan(self):
        prestamo = self.prestar()
        self.agregar(self.admin)
        plantilla = services.guardar_plantilla(
            self.admin, self.version(self.admin), "Repetir"
        )
        rutas = [
            ("Home", []),
            ("panol:inventario", []),
            ("panol:articulo", [self.cable.pk]),
            ("panol:articulo_editar", [self.cable.pk]),
            ("panol:ajustar", [self.cable.pk]),
            ("panol:kits", []),
            ("panol:kit_nuevo", []),
            ("panol:kit_editar", [self.kit.pk]),
            ("panol:carrito", []),
            ("panol:plantillas", []),
            ("panol:plantilla", [plantilla.pk]),
            ("panol:enviar", []),
            ("panol:directo", []),
            ("panol:solicitudes", []),
            ("panol:solicitud", [prestamo.solicitud_id]),
            ("panol:prestamos", []),
            ("panol:prestamo", [prestamo.pk]),
            ("panol:devolver", [prestamo.pk]),
            ("panol:consultas", []),
        ]
        rutas += [
            ("panol:catalogo", [tipo])
            for tipo in ("categorias", "areas", "carreras", "asignaturas")
        ]
        for modo in ("light", "dark"):
            UserVisualPreference.objects.update_or_create(
                user=self.admin, defaults={"mode": modo}
            )
            self.client.force_login(self.admin)
            for ruta, args in rutas:
                with self.subTest(ruta=ruta, modo=modo):
                    response = self.client.get(reverse(ruta, args=args))
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, f'data-mode="{modo}"')
                    html = response.content.decode()
                    self.assertLess(
                        html.index(">Inicio</a>"), html.index(">Inventario</a>")
                    )

    def test_flujo_http_solicitud_envio_repetido_y_entrega(self):
        self.client.force_login(self.persona)
        self.assertEqual(
            self.client.post(
                reverse("panol:carrito"),
                {
                    "accion": "agregar",
                    "articulo": self.cable.pk,
                    "cantidad": 3,
                    "version": 0,
                },
            ).status_code,
            302,
        )
        get = self.client.get(reverse("panol:enviar"))
        data = {
            "version": self.version(self.persona),
            "token": get.context["form"]["token"].value(),
            "tipo": "estudiante",
            "motivo": "Laboratorio",
            "fecha_necesaria": str(timezone.localdate()),
            "propietario": self.otra.pk,
        }
        for _ in range(2):
            self.assertEqual(
                self.client.post(reverse("panol:enviar"), data).status_code, 302
            )
        solicitud = Solicitud.objects.get()
        self.assertEqual(solicitud.propietario_id, self.persona.pk)
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.post(
                reverse("panol:solicitud", args=[solicitud.pk]),
                {"accion": "resolver", "decision": "aprobar", "motivo": "Aprobado"},
            ).status_code,
            302,
        )
        self.assertEqual(self.saldo(), (10, 0))
        self.assertEqual(
            self.client.post(
                reverse("panol:solicitud", args=[solicitud.pk]),
                {"accion": "entregar", "fecha_limite": str(timezone.localdate())},
            ).status_code,
            302,
        )
        self.assertEqual(self.saldo(), (7, 0))

    def test_token_de_otro_actor_o_accion_no_envia(self):
        self.agregar()
        self.client.force_login(self.persona)
        for operacion in (
            token(self.otra, "solicitud"),
            token(self.persona, "directo"),
            "inválido",
        ):
            response = self.client.post(
                reverse("panol:enviar"),
                {
                    "token": operacion,
                    "version": self.version(self.persona),
                    "tipo": "docente",
                    "motivo": "Clase",
                    "fecha_necesaria": str(timezone.localdate()),
                },
            )
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Solicitud.objects.exists())

    def test_filtros_invalidos_no_muestran_resultados_enganosos(self):
        self.prestar()
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("panol:prestamos"), {"desde": "2026-10-20", "hasta": "2026-10-01"}
        )
        self.assertContains(response, "no puede ser posterior")
        self.assertEqual(len(response.context["objetos"]), 0)

    def test_dashboard_cifras_reales_ambito_y_recepciones_fisicas(self):
        prestamo = self.prestar(cantidad=3)
        self.prestar(cantidad=1, actor=self.otra)
        services.devolver(
            self.admin,
            prestamo.pk,
            0,
            uuid.uuid4(),
            [(prestamo.lineas.get().pk, 0, 0, 1)],
            "Pérdida",
        )
        propio = dashboard(self.persona)
        globales = dashboard(self.admin)
        self.assertEqual(sum(propio["panol_grafico"]["entregas"]), 1)
        self.assertEqual(sum(globales["panol_grafico"]["entregas"]), 2)
        self.assertEqual(sum(globales["panol_grafico"]["devoluciones"]), 0)
        self.assertEqual(len(globales["panol_grafico"]["dias"]), 30)
        self.assertNotIn(
            "Artículos activos", [c["etiqueta"] for c in propio["panol_tarjetas"]]
        )
        self.assertFalse(dashboard(self.sin_perfil)["panol_habilitado"])

    def test_fallo_dashboard_no_se_presenta_como_cero(self):
        from django.db import OperationalError

        self.client.force_login(self.admin)
        with patch(
            "homeApp.views.dashboard", side_effect=OperationalError("Fallo sintético")
        ):
            response = self.client.get(reverse("Home"))
        self.assertContains(response, "Resumen del pañol no disponible")
        self.assertContains(response, "Últimos usuarios que iniciaron sesión")
        self.assertNotContains(response, 'id="panol-grafico"')
