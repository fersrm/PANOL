"""Vistas y formularios del seguimiento exclusivo ADMIN."""

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect

from .academico_selectors import catalogo, inactividad, seguimiento
from .academico_services import cambiar_plan
from .models import (
    Articulo,
    Asignatura,
    Carrera,
    Categoria,
    Docencia,
    PeriodoAcademico,
    PlanAsignatura,
)
from .permissions import puede_academico
from .views import PanolView


class PeriodoForm(forms.ModelForm):
    class Meta:
        model = PeriodoAcademico
        fields = ["codigo", "nombre", "inicio", "fin", "activo"]
        labels = {"codigo": "Código"}
        widgets = {
            n: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            for n in ("inicio", "fin")
        }

    def clean_codigo(self):
        return self.cleaned_data["codigo"].strip().upper()

    def clean(self):
        d = super().clean()
        if d.get("inicio") and d.get("fin") and d["inicio"] > d["fin"]:
            raise ValidationError("El inicio no puede ser posterior al fin.")
        if (
            self.instance.pk
            and self.instance.planasignatura_set.exists()
            and {"inicio", "fin"} & set(self.changed_data)
        ):
            raise ValidationError(
                "Este período tiene pautas; crea otro para cambiar sus fechas."
            )
        return d


class PlanForm(forms.ModelForm):
    class Meta:
        model = PlanAsignatura
        fields = ["carrera", "asignatura", "periodo"]

    def clean(self):
        d = super().clean()
        c, a, p = d.get("carrera"), d.get("asignatura"), d.get("periodo")
        if (
            c
            and a
            and (
                not c.activo
                or not c.area.activo
                or not a.activo
                or not a.carreras.filter(pk=c.pk).exists()
            )
        ):
            raise ValidationError(
                "Selecciona una asignatura que pertenezca a la carrera habilitada."
            )
        if p and not p.activo:
            raise ValidationError("Período deshabilitado.")
        return d


class CuentaDocenteField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return f"{user.get_full_name() or user.username} ({user.username})"


class DocenteForm(forms.Form):
    docente = CuentaDocenteField(
        queryset=get_user_model()
        .objects.filter(is_active=True)
        .filter(
            Q(is_superuser=True)
            | Q(
                profile__position_FK__permission_code__in=[
                    "ADMIN",
                    "MANAGER",
                    "RESTRICTED",
                ]
            )
        )
        .distinct()
    )


class ReporteForm(forms.Form):
    periodo = forms.ModelChoiceField(PeriodoAcademico.objects.all(), required=False)
    carrera = forms.ModelChoiceField(Carrera.objects.all(), required=False)
    asignatura = forms.ModelChoiceField(Asignatura.objects.all(), required=False)
    docente = forms.ModelChoiceField(
        get_user_model().objects.filter(docencia__isnull=False).distinct(),
        required=False,
    )
    categoria = forms.ModelChoiceField(Categoria.objects.all(), required=False)
    q = forms.CharField(
        label="Artículo: nombre o código", max_length=200, required=False
    )
    veces = forms.ChoiceField(
        label="Número exacto de solicitudes",
        required=False,
        choices=[("", "Todas"), ("0", "Sin solicitudes")]
        + [(str(n), str(n)) for n in range(1, 6)]
        + [("mas", "Más de 5")],
    )
    desde = forms.DateField(
        label="Fecha necesaria desde",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    hasta = forms.DateField(
        label="Fecha necesaria hasta",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    rechazadas = forms.BooleanField(label="Incluir demanda rechazada", required=False)

    def clean(self):
        d = super().clean()
        if d.get("desde") and d.get("hasta") and d["desde"] > d["hasta"]:
            raise ValidationError("Rango de fechas inválido.")
        return d


class InactividadForm(forms.Form):
    numero = forms.IntegerField(
        label="Tiempo sin actividad", min_value=1, max_value=100, initial=1
    )
    unidad = forms.ChoiceField(
        choices=[("semanas", "Semanas"), ("meses", "Meses"), ("anos", "Años")]
    )
    modo = forms.ChoiceField(
        choices=[
            ("prestamos", "Sin préstamos"),
            ("movimientos", "Sin movimientos de inventario"),
        ]
    )
    q = forms.CharField(
        label="Artículo: nombre o código", max_length=200, required=False
    )
    categoria = forms.ModelChoiceField(Categoria.objects.all(), required=False)
    inactivos = forms.BooleanField(
        label="Incluir artículos deshabilitados", required=False
    )


class AcademicoView(PanolView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not puede_academico(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ConfiguracionView(AcademicoView):
    def get(self, request):
        return self.page(
            "academico_config",
            titulo="Configuración académica",
            periodos=PeriodoAcademico.objects.all(),
            planes=PlanAsignatura.objects.select_related(
                "carrera", "asignatura", "periodo"
            ),
            periodo_form=getattr(self, "periodo_form", PeriodoForm()),
            plan_form=getattr(self, "plan_form", PlanForm()),
        )

    def post(self, request):
        if request.POST.get("accion") == "periodo":
            instancia = (
                get_object_or_404(PeriodoAcademico, pk=request.POST["periodo_id"])
                if request.POST.get("periodo_id")
                else None
            )
            self.periodo_form = PeriodoForm(request.POST, instance=instancia)
            if not self.periodo_form.is_valid():
                return self.invalid()
            with transaction.atomic():
                self.periodo_form.save()
        else:
            self.plan_form = PlanForm(request.POST)
            if not self.plan_form.is_valid():
                return self.invalid()
            with transaction.atomic():
                p = self.plan_form.save(commit=False)
                p.actor = request.user
                p.save()
        return redirect("panol:academico_config")


class PlanView(AcademicoView):
    def get(self, request, pk):
        p = get_object_or_404(
            PlanAsignatura.objects.select_related("carrera", "asignatura", "periodo"),
            pk=pk,
        )
        return self.page(
            "academico_plan",
            titulo=str(p),
            plan=p,
            docente_form=getattr(self, "docente_form", DocenteForm()),
            equipos=p.equipamiento.select_related("articulo"),
            docentes=p.docencias.select_related("docente"),
            **catalogo(request.GET),
        )

    def post(self, request, pk):
        get_object_or_404(PlanAsignatura, pk=pk)
        revision = forms.IntegerField(min_value=1).clean(request.POST.get("revision"))
        accion = request.POST.get("accion")
        objeto = None
        if accion in ("agregar", "quitar"):
            objeto = get_object_or_404(
                Articulo.objects.select_related("categoria"),
                pk=forms.IntegerField(min_value=1).clean(request.POST.get("articulo")),
            )
        elif accion == "docente":
            self.docente_form = DocenteForm(request.POST)
            if not self.docente_form.is_valid():
                return self.invalid()
            objeto = self.docente_form.cleaned_data["docente"]
        elif accion == "desvincular":
            objeto = get_object_or_404(
                Docencia,
                pk=forms.IntegerField(min_value=1).clean(request.POST.get("docencia")),
                plan_id=pk,
            ).pk
        cambiar_plan(request.user, pk, revision, accion, objeto)
        return redirect(request.get_full_path())


class ReporteView(AcademicoView):
    adicionales = False
    sin_movimiento = False

    def get(self, request):
        if self.sin_movimiento:
            form = InactividadForm(
                request.GET or {"numero": 1, "unidad": "meses", "modo": "prestamos"}
            )
            filas, corte = (
                inactividad(request.user, form.cleaned_data)
                if form.is_valid()
                else ([], None)
            )
            return self.page(
                "academico_inactividad",
                titulo="Equipamiento sin movimiento",
                filtro=form,
                objetos=self.paginar(filas),
                corte=corte,
            )
        form = ReporteForm(request.GET)
        filas, desconocidos = (
            seguimiento(request.user, form.cleaned_data, self.adicionales)
            if form.is_valid()
            else ([], 0)
        )
        return self.page(
            "academico_reporte",
            titulo="Adicionales frecuentes"
            if self.adicionales
            else "Seguimiento académico",
            filtro=form,
            objetos=self.paginar(filas),
            desconocidos=desconocidos,
        )


class PeriodoEditView(AcademicoView):
    def get(self, request, pk):
        periodo = get_object_or_404(PeriodoAcademico, pk=pk)
        return self.page(
            "formulario",
            titulo="Editar período",
            form=self.form(PeriodoForm, instance=periodo),
            volver="/panol/academico/configuracion/",
        )

    def post(self, request, pk):
        with transaction.atomic():
            periodo = get_object_or_404(
                PeriodoAcademico.objects.select_for_update(), pk=pk
            )
            self.bound_form = PeriodoForm(request.POST, instance=periodo)
            if not self.bound_form.is_valid():
                return self.invalid()
            self.bound_form.save()
        return redirect("panol:academico_config")
