import uuid
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from PanolApp import services
from PanolApp.academico_selectors import corte_inactividad, inactividad, seguimiento
from PanolApp.academico_services import cambiar_plan
from PanolApp.models import (
    Area,
    Articulo,
    Asignatura,
    Carrera,
    Docencia,
    EquipamientoAsignatura,
    PeriodoAcademico,
    PlanAsignatura,
    Solicitud,
)
from PanolApp.tests.test_panol import Base


class AcademicoTests(Base):
    def setUp(self):
        area = Area.objects.create(nombre="Informática")
        self.carrera = Carrera.objects.create(
            codigo="INF", nombre="Informática", area=area
        )
        self.asignatura = Asignatura.objects.create(codigo="HARD", nombre="Hardware")
        self.asignatura.carreras.add(self.carrera)
        self.periodo = PeriodoAcademico.objects.create(
            codigo="2026",
            nombre="Período",
            inicio=timezone.localdate() - timedelta(days=30),
            fin=timezone.localdate() + timedelta(days=90),
        )
        self.plan = PlanAsignatura.objects.create(
            carrera=self.carrera,
            asignatura=self.asignatura,
            periodo=self.periodo,
            actor=self.admin,
            configurada=True,
        )
        self.docencia = Docencia.objects.create(
            plan=self.plan, docente=self.persona, actor=self.admin
        )
        self.articulo = Articulo.objects.create(
            codigo="PC", nombre="PC", categoria=self.categoria, disponible=100
        )
        EquipamientoAsignatura.objects.create(plan=self.plan, articulo=self.articulo)

    def pedir(self, articulo=None, cantidad=2):
        from PanolApp.models import Carrito

        c, _ = Carrito.objects.get_or_create(propietario=self.persona)
        services.cambiar_carrito(
            self.persona,
            c.version,
            "agregar",
            articulo=articulo or self.articulo,
            cantidad=cantidad,
        )
        c.refresh_from_db()
        return services.crear_solicitud(
            self.persona,
            c.version,
            uuid.uuid4(),
            {
                "tipo": "docente",
                "motivo": "Clase",
                "fecha_necesaria": timezone.localdate(),
                "carrera": self.carrera,
                "asignatura": self.asignatura,
                "docencia": self.docencia,
            },
        )

    def test_snapshot_y_adicional_no_bloqueado(self):
        s = self.pedir(self.cable)
        self.assertEqual(s.contexto["articulos"], [self.articulo.pk])
        cambiar_plan(self.admin, self.plan.pk, 1, "agregar", self.cable)
        s.refresh_from_db()
        self.assertEqual(s.contexto["revision"], 1)
        filas, _ = seguimiento(self.admin, {}, True)
        self.assertEqual(filas[0]["veces"], 1)
        self.assertEqual(filas[0]["entregas"], 0)

    def test_frecuencia_documentos_y_entregas(self):
        for _ in range(3):
            self.pedir(cantidad=20)
        filas, _ = seguimiento(self.admin, {"veces": "3"})
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["unidades"], 60)
        self.assertEqual(filas[0]["dias"], 1)
        self.assertEqual(seguimiento(self.admin, {"veces": "2"})[0], [])
        s = Solicitud.objects.first()
        services.resolver_solicitud(self.admin, s.pk, True, "Aprobado")
        services.entregar_solicitud(
            self.admin, s.pk, timezone.localdate() + timedelta(days=2)
        )
        r = seguimiento(self.admin, {"veces": "3"})[0][0]
        self.assertEqual((r["entregas"], r["entregadas"]), (1, 20))

    def test_ceros_y_revocacion(self):
        self.assertEqual(seguimiento(self.admin, {"veces": "0"})[0][0]["veces"], 0)
        self.docencia.activo = False
        self.docencia.save()
        with self.assertRaises(ValidationError):
            self.pedir()
        self.assertEqual(Solicitud.objects.count(), 0)

    def test_matriz_y_paginas(self):
        rutas = [
            "academico",
            "academico_adicionales",
            "academico_inactividad",
            "academico_config",
        ]
        for user in [
            self.admin,
            self.manager,
            self.persona,
            self.root,
            self.sin_perfil,
            self.inactivo,
        ]:
            self.client.force_login(user)
            for ruta in rutas:
                respuesta = self.client.get(reverse("panol:" + ruta))
                self.assertEqual(
                    respuesta.status_code,
                    200
                    if user == self.admin
                    else (302 if user == self.inactivo else 403),
                )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                reverse("panol:academico_plan", args=[self.plan.pk])
            ).status_code,
            200,
        )

    def test_busqueda_carrito_y_paginacion(self):
        for n in range(25):
            Articulo.objects.create(
                codigo=f"N{n}", nombre=f"Notebook {n:02}", categoria=self.categoria
            )
        self.client.force_login(self.persona)
        r = self.client.get(
            reverse("panol:carrito"),
            {"q": "Notebook", "categoria": self.categoria.pk, "page": 2},
        )
        self.assertEqual(len(r.context["catalogo"]), 5)
        self.assertContains(r, "Notebook 24")
        self.assertNotContains(r, "Notebook 00")

    def test_calendario_y_alta_desconocida(self):
        self.assertEqual(
            corte_inactividad(1, "meses", date(2024, 3, 31)), date(2024, 2, 29)
        )
        Articulo.objects.filter(pk=self.articulo.pk).update(creada=None)
        filas, _ = inactividad(
            self.admin, {"numero": 1, "unidad": "meses", "modo": "prestamos"}
        )
        self.assertTrue(
            next(r for r in filas if r["articulo"].pk == self.articulo.pk)[
                "desconocida"
            ]
        )

    def test_revision_permisos_y_fecha_invalidos(self):
        with self.assertRaises(ValidationError):
            cambiar_plan(self.admin, self.plan.pk, 999, "confirmar")
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            cambiar_plan(self.manager, self.plan.pk, 1, "confirmar")
        self.periodo.fin = timezone.localdate() - timedelta(days=1)
        self.periodo.save()
        with self.assertRaises(ValidationError):
            self.pedir()

    def test_frecuencias_una_a_seis_y_cambio_revision(self):
        for n in range(1, 7):
            self.pedir()
            r = seguimiento(self.admin, {"veces": str(n) if n <= 5 else "mas"})[0]
            self.assertEqual(len(r), 1)
            self.assertEqual(r[0]["veces"], n)
            if n == 2:
                cambiar_plan(self.admin, self.plan.pk, 1, "agregar", self.cable)
        self.assertEqual(seguimiento(self.admin, {"q": "PC"})[0][0]["veces"], 6)

    def test_post_asociacion_ajena_y_csrf(self):
        from django.test import Client

        self.client.force_login(self.manager)
        url = reverse("panol:academico_plan", args=[self.plan.pk])
        self.assertEqual(
            self.client.post(url, {"accion": "confirmar", "revision": 1}).status_code,
            403,
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(
                url, {"accion": "agregar", "revision": 1, "articulo": "texto"}
            ).status_code,
            400,
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(
            client.post(url, {"accion": "confirmar", "revision": 1}).status_code, 403
        )
        self.admin.profile.position_FK = self.manager.profile.position_FK
        self.admin.profile.save()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_docente_ajeno_y_estudiante_separados(self):
        from PanolApp.academico_services import contexto_solicitud

        datos = {
            "tipo": "docente",
            "carrera": self.carrera,
            "asignatura": self.asignatura,
            "docencia": self.docencia,
            "fecha_necesaria": timezone.localdate(),
        }
        with self.assertRaises(ValidationError):
            contexto_solicitud(self.otra, datos)
        s = self.pedir()
        s.tipo = "estudiante"
        s.save()
        filas, _ = seguimiento(self.admin, {})
        self.assertEqual(sum(r["veces"] for r in filas if r["origen"] == "docente"), 0)
        self.assertEqual(
            sum(r["veces"] for r in filas if r["origen"] == "estudiante"), 1
        )


class MigracionAcademicaTests(TransactionTestCase):
    def test_legado_conserva_stock_y_alta_desconocida(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old = [("PanolApp", "0001_initial")]
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            cat = apps.get_model("PanolApp", "Categoria").objects.create(
                nombre="Legado"
            )
            pk = (
                apps.get_model("PanolApp", "Articulo")
                .objects.create(
                    nombre="PC antiguo", codigo="OLD", categoria=cat, disponible=7
                )
                .pk
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from PanolApp.models import Articulo

            articulo = Articulo.objects.get(pk=pk)
            self.assertIsNone(articulo.creada)
            self.assertEqual(articulo.disponible, 7)
            executor = MigrationExecutor(connection)
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            self.assertEqual(
                apps.get_model("PanolApp", "Articulo").objects.get(pk=pk).disponible, 7
            )
        finally:
            MigrationExecutor(connection).migrate(latest)
