from django.urls import path

from . import views

app_name = "panol"
urlpatterns = [
    path("inventario/", views.InventarioView.as_view(), name="inventario"),
    path("inventario/nuevo/", views.ArticuloEditView.as_view(), name="articulo_nuevo"),
    path("inventario/<int:pk>/", views.ArticuloDetailView.as_view(), name="articulo"),
    path(
        "inventario/<int:pk>/editar/",
        views.ArticuloEditView.as_view(),
        name="articulo_editar",
    ),
    path("inventario/<int:pk>/ajustar/", views.AjusteView.as_view(), name="ajustar"),
    path("kits/", views.KitsView.as_view(), name="kits"),
    path("kits/nuevo/", views.KitEditView.as_view(), name="kit_nuevo"),
    path("kits/<int:pk>/editar/", views.KitEditView.as_view(), name="kit_editar"),
    path("carrito/", views.CarritoView.as_view(), name="carrito"),
    path("mis-plantillas/", views.PlantillasView.as_view(), name="plantillas"),
    path(
        "mis-plantillas/<int:pk>/",
        views.PlantillaDetailView.as_view(),
        name="plantilla",
    ),
    path("solicitudes/", views.DocumentosView.as_view(), name="solicitudes"),
    path("solicitudes/nueva/", views.EnviarView.as_view(), name="enviar"),
    path(
        "solicitudes/<int:pk>/", views.SolicitudDetailView.as_view(), name="solicitud"
    ),
    path("prestamos/", views.PrestamosView.as_view(), name="prestamos"),
    path("prestamos/nuevo/", views.DirectoView.as_view(), name="directo"),
    path("prestamos/<int:pk>/", views.PrestamoDetailView.as_view(), name="prestamo"),
    path("prestamos/<int:pk>/devolver/", views.DevolverView.as_view(), name="devolver"),
    path("catalogos/<slug:tipo>/", views.CatalogoView.as_view(), name="catalogo"),
    path(
        "catalogos/<slug:tipo>/nuevo/",
        views.CatalogoEditView.as_view(),
        name="catalogo_nuevo",
    ),
    path(
        "catalogos/<slug:tipo>/<int:pk>/editar/",
        views.CatalogoEditView.as_view(),
        name="catalogo_editar",
    ),
    path("consultas/", views.ConsultasView.as_view(), name="consultas"),
]
