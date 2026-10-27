import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import OperationalError, close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from PanolApp import services
from PanolApp.models import Articulo, Categoria, Devolucion, Prestamo


class ConcurrenciaTests(TransactionTestCase):
    """Competencia real con conexiones independientes, no un mock del descuento."""

    def setUp(self):
        self.operadores = [
            User.objects.create_user(f"root_{n}", is_superuser=True) for n in range(2)
        ]
        self.categoria = Categoria.objects.create(nombre="Pruebas")
        self.articulo = services.guardar_articulo(
            self.operadores[0],
            Articulo(codigo="ULTIMO", nombre="Último equipo", categoria=self.categoria),
            1,
        )

    def competir(self, operacion):
        barrera = Barrier(2)

        def worker(indice):
            close_old_connections()
            barrera.wait(timeout=5)
            try:
                for intento in range(20):
                    try:
                        return operacion(indice)
                    except OperationalError as error:
                        if "locked" not in str(error).lower():
                            raise
                        # Simula un reintento del mismo formulario, con su clave original.
                        time.sleep(0.01 * (intento + 1))
                raise AssertionError("La contención SQLite no se resolvió")
            except ValidationError:
                return "conflicto"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, (0, 1)))

    def test_dos_entregas_compiten_por_ultima_unidad(self):
        claves = [uuid.uuid4(), uuid.uuid4()]
        for actor in self.operadores:
            services.cambiar_carrito(
                actor, 0, "agregar", articulo=self.articulo, cantidad=1
            )

        def operacion(indice):
            actor = self.operadores[indice]
            return services.prestamo_directo(
                actor,
                1,
                claves[indice],
                actor,
                timezone.localdate(),
                "Prueba de competencia",
            ).pk

        resultados = self.competir(operacion)
        self.assertEqual(resultados.count("conflicto"), 1)
        self.assertEqual(Prestamo.objects.count(), 1)
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.disponible, 0)

    def test_dos_recepciones_no_resuelven_la_misma_unidad(self):
        actor = self.operadores[0]
        services.cambiar_carrito(
            actor, 0, "agregar", articulo=self.articulo, cantidad=1
        )
        prestamo = services.prestamo_directo(
            actor, 1, uuid.uuid4(), actor, timezone.localdate(), "Prueba"
        )
        linea = prestamo.lineas.get()
        claves = [uuid.uuid4(), uuid.uuid4()]

        def operacion(indice):
            return services.devolver(
                self.operadores[indice],
                prestamo.pk,
                0,
                claves[indice],
                [(linea.pk, 1, 0, 0)],
                "Recepción simultánea",
            ).pk

        resultados = self.competir(operacion)
        self.assertEqual(resultados.count("conflicto"), 1)
        self.assertEqual(Devolucion.objects.count(), 1)
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.disponible, 1)


class MigracionPanolTests(TransactionTestCase):
    def test_apply_reverse_solo_tablas_del_modulo(self):
        # La DB creada por el runner es ficticia; nunca apunta a db.sqlite3.
        root = User.objects.create_user("conservar_usuario")
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([("PanolApp", None)])
            self.assertNotIn(
                "PanolApp_articulo", connection.introspection.table_names()
            )
            self.assertTrue(User.objects.filter(pk=root.pk).exists())
            executor = MigrationExecutor(connection)
            executor.migrate([("PanolApp", "0001_initial")])
            self.assertIn("PanolApp_articulo", connection.introspection.table_names())
            self.assertTrue(User.objects.filter(pk=root.pk).exists())
        finally:
            MigrationExecutor(connection).migrate([("PanolApp", "0001_initial")])
