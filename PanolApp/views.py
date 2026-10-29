import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from . import forms, services
from .academico_selectors import catalogo
from .models import (
    Area,
    Articulo,
    Asignatura,
    Carrera,
    Carrito,
    Categoria,
    Kit,
    MovimientoInventario,
    PlantillaPedido,
    Prestamo,
    Solicitud,
)
from .permissions import puede_operar, puede_solicitar
from .selectors import prestamos_visibles, solicitudes_visibles


def token(user, accion):
    return signing.dumps(
        {"user": user.pk, "accion": accion, "clave": str(uuid.uuid4())},
        salt="panol-operacion",
    )


def clave(user, accion, valor):
    try:
        data = signing.loads(valor, salt="panol-operacion", max_age=86400)
        if data["user"] != user.pk or data["accion"] != accion:
            raise signing.BadSignature
        return uuid.UUID(data["clave"])
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise ValidationError(
            "El formulario venció o no es válido. Recarga antes de continuar."
        )


class PanolView(LoginRequiredMixin, View):
    operador = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (
            puede_operar(request.user)
            if self.operador
            else puede_solicitar(request.user)
        ):
            raise PermissionDenied
        try:
            return super().dispatch(request, *args, **kwargs)
        except ValidationError as error:
            if request.method != "POST":
                raise
            return self.invalid(errors=error.messages)
        except IntegrityError:
            if request.method != "POST":
                raise
            return self.invalid(
                errors=[
                    "El registro ya existe o cambió durante la operación. Recarga y revisa los datos."
                ]
            )
        except OperationalError as error:
            if request.method != "POST" or "locked" not in str(error).lower():
                raise
            return self.invalid(
                errors=[
                    "Otra operación está actualizando el pañol. Recarga e inténtalo nuevamente."
                ]
            )

    def page(self, template, **context):
        context.update(
            operador=puede_operar(self.request.user),
            problemas=getattr(self, "problemas", []),
        )
        return render(self.request, f"PanolApp/{template}.html", context)

    def invalid(self, form=None, errors=None):
        if form is not None:
            self.bound_form = form
        self.problemas = errors or []
        response = self.get(self.request, **self.kwargs)
        response.status_code = 400
        return response

    def form(self, cls, **kwargs):
        return getattr(self, "bound_form", None) or cls(**kwargs)

    def paginar(self, qs):
        return Paginator(qs, 20).get_page(self.request.GET.get("page"))


class InventarioView(PanolView):
    def get(self, request):
        qs = Articulo.objects.select_related("categoria")
        if not puede_operar(request.user):
            qs = qs.filter(activo=True, categoria__activo=True)
        buscar = request.GET.get("q", "").strip()[:200]
        if buscar:
            qs = qs.filter(
                Q(nombre__icontains=buscar)
                | Q(codigo__icontains=buscar)
                | Q(categoria__nombre__icontains=buscar)
            )
        if request.GET.get("bajo") == "1":
            qs = qs.filter(disponible__lt=F("minimo"))
        return self.page(
            "inventario", titulo="Inventario", objetos=self.paginar(qs), buscar=buscar
        )


class ArticuloEditView(PanolView):
    operador = True

    def instancia(self, pk=None):
        return get_object_or_404(Articulo, pk=pk) if pk else Articulo()

    def get(self, request, pk=None):
        return self.page(
            "formulario",
            titulo="Editar artículo" if pk else "Registrar artículo",
            form=self.form(forms.ArticuloForm, instance=self.instancia(pk)),
            volver=reverse("panol:inventario"),
        )

    def post(self, request, pk=None):
        self.bound_form = forms.ArticuloForm(request.POST, instance=self.instancia(pk))
        if not self.bound_form.is_valid():
            return self.invalid()
        articulo = services.guardar_articulo(
            request.user,
            self.bound_form.save(commit=False),
            self.bound_form.cleaned_data.get("inicial") or 0,
        )
        messages.success(request, "Artículo guardado.")
        return redirect("panol:articulo", pk=articulo.pk)


class ArticuloDetailView(PanolView):
    def get(self, request, pk):
        articulo = get_object_or_404(
            Articulo.objects.select_related("categoria"), pk=pk
        )
        if not puede_operar(request.user) and not (
            articulo.activo and articulo.categoria.activo
        ):
            raise Http404
        return self.page(
            "articulo",
            titulo=articulo.nombre,
            articulo=articulo,
            movimientos=(
                articulo.movimientos.select_related("actor", "prestamo")[:30]
                if puede_operar(request.user)
                else []
            ),
            agregar=forms.AgregarForm(
                initial={
                    "articulo": articulo.pk,
                    "version": Carrito.objects.filter(propietario=request.user)
                    .values_list("version", flat=True)
                    .first()
                    or 0,
                }
            ),
        )


class AjusteView(PanolView):
    operador = True

    def get(self, request, pk):
        articulo = get_object_or_404(Articulo, pk=pk)
        return self.page(
            "formulario",
            titulo=f"Ajustar existencias · {articulo.nombre}",
            ayuda=f"Disponible: {articulo.disponible}. No disponible: {articulo.no_disponible}. Usa valores positivos para ingresar y negativos para retirar.",
            form=self.form(forms.AjusteForm),
            volver=reverse("panol:articulo", args=[pk]),
        )

    def post(self, request, pk):
        self.bound_form = forms.AjusteForm(request.POST)
        if not self.bound_form.is_valid():
            return self.invalid()
        services.ajustar_stock(request.user, pk, **self.bound_form.cleaned_data)
        messages.success(request, "Existencias ajustadas y movimiento registrado.")
        return redirect("panol:articulo", pk=pk)


class KitsView(PanolView):
    def get(self, request):
        qs = Kit.objects.prefetch_related("componentes__articulo")
        if not puede_operar(request.user):
            qs = qs.filter(activo=True)
        return self.page(
            "kits",
            titulo="Kits del pañol",
            objetos=self.paginar(qs),
            version=Carrito.objects.filter(propietario=request.user)
            .values_list("version", flat=True)
            .first()
            or 0,
        )


class KitEditView(PanolView):
    operador = True

    def instancia(self, pk=None):
        if not hasattr(self, "kit"):
            self.kit = get_object_or_404(Kit, pk=pk) if pk else Kit()
        return self.kit

    def get(self, request, pk=None):
        kit = self.instancia(pk)
        formset = getattr(self, "formset", None)
        if formset is None:
            formset = forms.ComponentesFormSet(instance=kit)
        return self.page(
            "kit_form",
            titulo="Editar kit" if pk else "Preparar kit",
            form=self.form(forms.KitForm, instance=kit),
            formset=formset,
            volver=reverse("panol:kits"),
        )

    def post(self, request, pk=None):
        kit = self.instancia(pk)
        self.bound_form = forms.KitForm(request.POST, instance=kit)
        self.formset = forms.ComponentesFormSet(request.POST, instance=kit)
        valid_form = self.bound_form.is_valid()
        valid_lines = self.formset.is_valid()
        if not valid_form or not valid_lines:
            return self.invalid()
        componentes = [
            (f.cleaned_data["articulo"], f.cleaned_data["cantidad"])
            for f in self.formset.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE")
        ]
        services.guardar_kit(
            request.user,
            self.bound_form.save(commit=False),
            componentes,
            self.bound_form.cleaned_data["revision"],
        )
        messages.success(
            request, "Kit guardado. Los pedidos anteriores conservan su composición."
        )
        return redirect("panol:kits")


class CarritoView(PanolView):
    def get(self, request):
        carrito = Carrito.objects.filter(propietario=request.user).first()
        version = carrito.version if carrito else 0
        lineas = (
            list(carrito.lineas.select_related("articulo__categoria", "kit"))
            if carrito
            else []
        )
        return self.page(
            "carrito",
            titulo="Mi carrito",
            lineas=lineas,
            version=version,
            agregar=getattr(self, "agregar", None)
            or forms.AgregarForm(initial={"version": version}),
            **catalogo(request.GET),
            guardar=getattr(self, "guardar", None)
            or forms.GuardarPlantillaForm(initial={"version": version}),
        )

    def post(self, request):
        accion = request.POST.get("accion")
        if accion == "guardar":
            self.guardar = forms.GuardarPlantillaForm(request.POST)
            if not self.guardar.is_valid():
                return self.invalid()
            services.guardar_plantilla(request.user, **self.guardar.cleaned_data)
            messages.success(request, "Pedido guardado en Mis plantillas.")
        elif accion == "agregar":
            self.agregar = forms.AgregarForm(request.POST)
            if not self.agregar.is_valid():
                return self.invalid()
            services.cambiar_carrito(
                request.user, accion=accion, **self.agregar.cleaned_data
            )
            messages.success(request, "Selección agregada al carrito.")
        else:
            form = forms.VersionForm(request.POST)
            if not form.is_valid():
                return self.invalid(
                    errors=["Versión del carrito inválida. Recarga la página."]
                )
            cantidad = request.POST.get("cantidad", "1")
            if len(cantidad) > 4 or not cantidad.isascii() or not cantidad.isdecimal():
                raise ValidationError("Indica una cantidad entera positiva.")
            linea_id = None
            if accion in ("quitar", "cantidad"):
                from django import forms as django_forms

                linea_id = django_forms.IntegerField(min_value=1).clean(
                    request.POST.get("linea")
                )
            services.cambiar_carrito(
                request.user,
                form.cleaned_data["version"],
                accion,
                linea_id=linea_id,
                cantidad=int(cantidad),
            )
            messages.success(request, "Carrito actualizado.")
        return redirect(
            reverse("panol:carrito")
            + ("?" + request.GET.urlencode() if request.GET else "")
        )


class PlantillasView(PanolView):
    def get(self, request):
        return self.page(
            "plantillas",
            titulo="Mis pedidos guardados",
            objetos=self.paginar(
                PlantillaPedido.objects.filter(
                    propietario=request.user
                ).prefetch_related("lineas__articulo", "lineas__kit")
            ),
        )


class PlantillaDetailView(PanolView):
    def objeto(self, pk):
        return get_object_or_404(PlantillaPedido, pk=pk, propietario=self.request.user)

    def get(self, request, pk):
        objeto = self.objeto(pk)
        return self.page(
            "plantilla",
            titulo=objeto.nombre,
            objeto=objeto,
            version=Carrito.objects.filter(propietario=request.user)
            .values_list("version", flat=True)
            .first()
            or 0,
            lineas=objeto.lineas.select_related("articulo__categoria", "kit"),
            form=self.form(forms.PlantillaForm, instance=objeto),
        )

    def post(self, request, pk):
        objeto = self.objeto(pk)
        accion = request.POST.get("accion")
        if accion == "cargar":
            form = forms.VersionForm(request.POST)
            if not form.is_valid() or request.POST.get("confirmar") != "si":
                raise ValidationError(
                    "Confirma el reemplazo del carrito y recarga si cambió su versión."
                )
            services.cargar_plantilla(request.user, form.cleaned_data["version"], pk)
            messages.success(
                request,
                "Pedido cargado. Revisa sus artículos y cualquier cambio de kit.",
            )
            return redirect("panol:carrito")
        if accion == "eliminar":
            if request.POST.get("confirmar") != "si":
                raise ValidationError("Confirma que deseas eliminar la plantilla.")
            objeto.delete()
            messages.success(request, "Plantilla eliminada.")
            return redirect("panol:plantillas")
        if accion != "nombre":
            raise ValidationError("Acción inválida.")
        self.bound_form = forms.PlantillaForm(request.POST, instance=objeto)
        if not self.bound_form.is_valid():
            return self.invalid()
        self.bound_form.save()
        messages.success(
            request,
            "Nombre actualizado. Para cambiar artículos, carga el pedido y guarda una nueva plantilla.",
        )
        return redirect("panol:plantilla", pk=pk)


class EnviarView(PanolView):
    directo = False

    def get(self, request):
        carrito = Carrito.objects.filter(propietario=request.user).first()
        version = carrito.version if carrito else 0
        accion = "directo" if self.directo else "solicitud"
        avisos = []
        if carrito and carrito.lineas.exists():
            try:
                detalle, _ = services.consolidar(
                    carrito.lineas.select_related("articulo__categoria", "kit")
                )
                avisos = [
                    f"{a.nombre}: pides {c} y hay {a.disponible} disponibles ahora."
                    for a, c in detalle
                    if c > a.disponible
                ]
            except ValidationError as error:
                avisos = error.messages
        return self.page(
            "formulario",
            titulo="Entregar préstamo directo" if self.directo else "Enviar solicitud",
            ayuda=(
                "Se entregarán todos los artículos del carrito. La entrega descuenta existencias."
                if self.directo
                else "La solicitud no reserva existencias. El pañol confirmará la entrega."
            ),
            resumen=carrito.lineas.select_related("articulo", "kit") if carrito else [],
            avisos=avisos,
            form=self.form(
                forms.DirectoForm if self.directo else forms.SolicitudForm,
                **({} if self.directo else {"actor": request.user}),
                initial={
                    "version": version,
                    "token": token(request.user, accion),
                    "fecha_necesaria": timezone.localdate(),
                },
            ),
            volver=reverse("panol:carrito"),
            boton="Confirmar entrega" if self.directo else "Enviar solicitud",
        )

    def post(self, request):
        self.bound_form = (forms.DirectoForm if self.directo else forms.SolicitudForm)(
            request.POST, **({} if self.directo else {"actor": request.user})
        )
        if not self.bound_form.is_valid():
            return self.invalid()
        data = self.bound_form.cleaned_data
        operacion = clave(
            request.user, "directo" if self.directo else "solicitud", data["token"]
        )
        if self.directo:
            obj = services.prestamo_directo(
                request.user,
                data["version"],
                operacion,
                data["beneficiario"],
                data["fecha_limite"],
                data["motivo"],
            )
            messages.success(request, "Préstamo entregado y existencias descontadas.")
            return redirect("panol:prestamo", pk=obj.pk)
        obj = services.crear_solicitud(request.user, data["version"], operacion, data)
        messages.success(request, "Solicitud enviada. Puedes consultar su estado aquí.")
        return redirect("panol:solicitud", pk=obj.pk)


class DirectoView(EnviarView):
    operador = True
    directo = True


class DocumentosView(PanolView):
    prestamos = False

    def get(self, request):
        filtro = forms.FiltroForm(request.GET or None)
        filtro.fields["estado"].choices += (
            [("abierto", "Abiertos"), ("cerrado", "Cerrados"), ("vencido", "Vencidos")]
            if self.prestamos
            else list(Solicitud.Estado.choices)
        )
        qs = (
            prestamos_visibles(request.user)
            if self.prestamos
            else solicitudes_visibles(request.user)
        )
        if request.GET and filtro.is_valid():
            data = filtro.cleaned_data
            campo_fecha = "entregado__date" if self.prestamos else "creada__date"
            for campo, sufijo in (("desde", "gte"), ("hasta", "lte")):
                if data.get(campo):
                    qs = qs.filter(**{f"{campo_fecha}__{sufijo}": data[campo]})
            if data.get("estado"):
                if self.prestamos:
                    qs = qs.filter(cerrado=data["estado"] == "cerrado")
                    if data["estado"] == "vencido":
                        qs = qs.filter(fecha_limite__lt=timezone.localdate())
                else:
                    qs = qs.filter(estado=data["estado"])
            if data.get("q"):
                q = data["q"]
                persona = "beneficiario" if self.prestamos else "propietario"
                cond = Q(**{f"{persona}__username__icontains": q}) | Q(
                    lineas__nombre__icontains=q
                )
                if self.prestamos:
                    numero = q.upper().removeprefix("PRE-")
                    if numero.isascii() and numero.isdecimal() and len(numero) < 18:
                        cond |= Q(pk=int(numero))
                else:
                    cond |= Q(folio__icontains=q.upper().removeprefix("SOL-"))
                    numero = q.upper().removeprefix("SOL-")
                    if numero.isascii() and numero.isdecimal() and len(numero) < 18:
                        cond |= Q(pk=int(numero))
                qs = qs.filter(cond).distinct()
        elif request.GET and not filtro.is_valid():
            qs = qs.none()
        return self.page(
            "documentos",
            titulo="Préstamos" if self.prestamos else "Solicitudes",
            prestamos=self.prestamos,
            objetos=self.paginar(qs),
            filtro=filtro,
        )


class PrestamosView(DocumentosView):
    prestamos = True


class SolicitudDetailView(PanolView):
    def get(self, request, pk):
        objeto = get_object_or_404(solicitudes_visibles(request.user), pk=pk)
        return self.page(
            "solicitud",
            titulo=str(objeto),
            objeto=objeto,
            lineas=objeto.lineas.all(),
            resolver=getattr(self, "resolver", None) or forms.ResolucionForm(),
            entregar=getattr(self, "entregar", None) or forms.EntregaForm(),
        )

    def post(self, request, pk):
        if not puede_operar(request.user):
            raise PermissionDenied
        get_object_or_404(solicitudes_visibles(request.user), pk=pk)
        if request.POST.get("accion") == "entregar":
            self.entregar = forms.EntregaForm(request.POST)
            if not self.entregar.is_valid():
                return self.invalid()
            obj = services.entregar_solicitud(
                request.user, pk, self.entregar.cleaned_data["fecha_limite"]
            )
            messages.success(request, "Préstamo entregado y existencias descontadas.")
            return redirect("panol:prestamo", pk=obj.pk)
        self.resolver = forms.ResolucionForm(request.POST)
        if not self.resolver.is_valid():
            return self.invalid()
        services.resolver_solicitud(
            request.user,
            pk,
            self.resolver.cleaned_data["decision"] == "aprobar",
            self.resolver.cleaned_data["motivo"],
        )
        messages.success(
            request, "Decisión registrada. La aprobación no descuenta existencias."
        )
        return redirect("panol:solicitud", pk=pk)


class PrestamoDetailView(PanolView):
    def get(self, request, pk):
        objeto = get_object_or_404(prestamos_visibles(request.user), pk=pk)
        return self.page(
            "prestamo",
            titulo=str(objeto),
            objeto=objeto,
            devoluciones=objeto.devoluciones.select_related(
                "operador"
            ).prefetch_related("lineas__linea"),
        )


class DevolverView(PanolView):
    operador = True

    def objeto(self, pk):
        return get_object_or_404(Prestamo.objects.prefetch_related("lineas"), pk=pk)

    def get(self, request, pk):
        objeto = self.objeto(pk)
        formset = getattr(self, "formset", None)
        if formset is None:
            formset = forms.RecepcionFormSet(
                initial=[{"linea": l.pk} for l in objeto.lineas.all()]
            )
        return self.page(
            "devolver",
            titulo=f"Registrar devolución · {objeto}",
            objeto=objeto,
            form=self.form(
                forms.DevolucionForm,
                initial={
                    "version": objeto.version,
                    "token": token(request.user, f"devolucion:{pk}"),
                },
            ),
            filas=list(zip(objeto.lineas.all(), formset.forms)),
            formset=formset,
        )

    def post(self, request, pk):
        objeto = self.objeto(pk)
        self.bound_form = forms.DevolucionForm(request.POST)
        self.formset = forms.RecepcionFormSet(request.POST)
        valid_form = self.bound_form.is_valid()
        valid_lines = self.formset.is_valid()
        if not valid_form or not valid_lines:
            return self.invalid()
        data = self.bound_form.cleaned_data
        recepciones = [
            (
                f.cleaned_data["linea"],
                f.cleaned_data["utilizables"],
                f.cleaned_data["danados"],
                f.cleaned_data["perdidos"],
            )
            for f in self.formset
        ]
        services.devolver(
            request.user,
            objeto.pk,
            data["version"],
            clave(request.user, f"devolucion:{pk}", data["token"]),
            recepciones,
            data["notas"],
        )
        messages.success(
            request, "Recepción registrada. Solo lo utilizable volvió a disponible."
        )
        return redirect("panol:prestamo", pk=pk)


CATALOGOS = {
    "categorias": (Categoria, "Categorías"),
    "areas": (Area, "Áreas"),
    "carreras": (Carrera, "Carreras"),
    "asignaturas": (Asignatura, "Asignaturas"),
}


class CatalogoView(PanolView):
    operador = True

    def get(self, request, tipo):
        if tipo not in CATALOGOS:
            raise Http404
        model, titulo = CATALOGOS[tipo]
        return self.page(
            "catalogo",
            titulo=titulo,
            tipo=tipo,
            objetos=self.paginar(model.objects.all()),
        )


class CatalogoEditView(PanolView):
    operador = True

    def config(self, tipo, pk=None):
        if tipo not in CATALOGOS:
            raise Http404
        model, titulo = CATALOGOS[tipo]
        return model, titulo, get_object_or_404(model, pk=pk) if pk else model()

    def get(self, request, tipo, pk=None):
        model, titulo, objeto = self.config(tipo, pk)
        return self.page(
            "formulario",
            titulo=f"{'Editar' if pk else 'Registrar'} · {titulo}",
            form=self.form(forms.catalogo_form(model), instance=objeto),
            volver=reverse("panol:catalogo", args=[tipo]),
        )

    def post(self, request, tipo, pk=None):
        model, _, objeto = self.config(tipo, pk)
        self.bound_form = forms.catalogo_form(model)(request.POST, instance=objeto)
        if not self.bound_form.is_valid():
            return self.invalid()
        with transaction.atomic():
            if tipo == "asignaturas" and objeto.pk:
                from .models import PlanAsignatura

                necesarios = set(
                    PlanAsignatura.objects.filter(asignatura=objeto).values_list(
                        "carrera_id", flat=True
                    )
                )
                elegidos = set(
                    self.bound_form.cleaned_data["carreras"].values_list(
                        "pk", flat=True
                    )
                )
                if not necesarios <= elegidos:
                    raise ValidationError(
                        "No puedes retirar carreras con pautas académicas. Conserva sus vínculos históricos."
                    )
            self.bound_form.save()
        messages.success(request, "Catálogo guardado.")
        return redirect("panol:catalogo", tipo=tipo)


class ConsultasView(PanolView):
    operador = True

    def get(self, request):
        filtro = forms.FiltroForm(request.GET or None)
        filtro.fields.pop("estado")
        qs = MovimientoInventario.objects.select_related(
            "articulo", "actor", "prestamo"
        )
        if request.GET and filtro.is_valid():
            data = filtro.cleaned_data
            if data.get("q"):
                qs = qs.filter(
                    Q(articulo__nombre__icontains=data["q"])
                    | Q(articulo__codigo__icontains=data["q"])
                    | Q(actor__username__icontains=data["q"])
                )
            if data.get("desde"):
                qs = qs.filter(fecha__date__gte=data["desde"])
            if data.get("hasta"):
                qs = qs.filter(fecha__date__lte=data["hasta"])
        elif request.GET:
            qs = qs.none()
        return self.page(
            "consultas",
            titulo="Movimientos del pañol",
            filtro=filtro,
            objetos=self.paginar(qs),
        )
