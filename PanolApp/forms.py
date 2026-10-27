from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import (
    Area,
    Articulo,
    Asignatura,
    Carrera,
    Categoria,
    ComponenteKit,
    Kit,
    PlantillaPedido,
    Solicitud,
)


class NormalizarForm(forms.ModelForm):
    def clean(self):
        data = super().clean()
        if "codigo" in data:
            data["codigo"] = data["codigo"].strip().upper()
        if "nombre" in data:
            data["nombre"] = data["nombre"].strip()
        return data


class ArticuloForm(NormalizarForm):
    inicial = forms.IntegerField(
        label="Existencias iniciales",
        min_value=0,
        max_value=999999,
        initial=0,
        required=False,
    )

    class Meta:
        model = Articulo
        fields = ["codigo", "nombre", "descripcion", "categoria", "minimo", "activo"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields.pop("inicial")
        else:
            self.fields["categoria"].queryset = Categoria.objects.filter(activo=True)


class AjusteForm(forms.Form):
    disponible = forms.IntegerField(
        label="Variación de unidades disponibles",
        min_value=-999999,
        max_value=999999,
        initial=0,
    )
    no_disponible = forms.IntegerField(
        label="Variación de unidades no disponibles",
        min_value=-999999,
        max_value=999999,
        initial=0,
    )
    motivo = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))


class KitForm(NormalizarForm):
    revision = forms.IntegerField(min_value=1, initial=1, widget=forms.HiddenInput)

    class Meta:
        model = Kit
        fields = ["codigo", "nombre", "descripcion", "activo"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["revision"] = self.instance.revision


class ComponenteForm(forms.ModelForm):
    class Meta:
        model = ComponenteKit
        fields = ["articulo", "cantidad"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["articulo"].queryset = Articulo.objects.filter(
            activo=True, categoria__activo=True
        )


ComponentesFormSet = inlineformset_factory(
    Kit,
    ComponenteKit,
    form=ComponenteForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
    max_num=100,
    validate_max=True,
    absolute_max=101,
)


class VersionForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class AgregarForm(VersionForm):
    articulo = forms.ModelChoiceField(
        label="Artículo", queryset=Articulo.objects.none(), required=False
    )
    kit = forms.ModelChoiceField(queryset=Kit.objects.none(), required=False)
    cantidad = forms.IntegerField(min_value=1, max_value=9999, initial=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["articulo"].queryset = Articulo.objects.filter(
            activo=True, categoria__activo=True
        )
        self.fields["kit"].queryset = Kit.objects.filter(activo=True)

    def clean(self):
        data = super().clean()
        if bool(data.get("articulo")) == bool(data.get("kit")):
            raise ValidationError("Elige un artículo o un kit, uno a la vez.")
        return data


class GuardarPlantillaForm(VersionForm):
    nombre = forms.CharField(label="Nombre para repetir este pedido", max_length=120)
    reemplazar = forms.BooleanField(
        label="Actualizar mi plantilla existente si ya tiene este nombre",
        required=False,
    )


class PlantillaForm(forms.ModelForm):
    class Meta:
        model = PlantillaPedido
        fields = ["nombre"]


class SolicitudForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    token = forms.CharField(widget=forms.HiddenInput)

    class Meta:
        model = Solicitud
        fields = [
            "tipo",
            "motivo",
            "fecha_necesaria",
            "carrera",
            "asignatura",
            "profesor",
        ]
        widgets = {
            "motivo": forms.Textarea(attrs={"rows": 3}),
            "fecha_necesaria": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["carrera"].queryset = Carrera.objects.filter(
            activo=True, area__activo=True
        )
        self.fields["asignatura"].queryset = Asignatura.objects.filter(activo=True)
        for name in ("carrera", "asignatura", "profesor"):
            self.fields[name].help_text = "Opcional."

    def clean_fecha_necesaria(self):
        value = self.cleaned_data["fecha_necesaria"]
        if value < timezone.localdate():
            raise ValidationError("Elige hoy o una fecha posterior.")
        return value

    def clean(self):
        data = super().clean()
        carrera, asignatura = data.get("carrera"), data.get("asignatura")
        if (
            carrera
            and asignatura
            and not asignatura.carreras.filter(pk=carrera.pk).exists()
        ):
            raise ValidationError(
                "La asignatura no pertenece a la carrera seleccionada."
            )
        return data


class ResolucionForm(forms.Form):
    decision = forms.ChoiceField(
        label="Decisión", choices=[("aprobar", "Aprobar"), ("rechazar", "Rechazar")]
    )
    motivo = forms.CharField(
        label="Motivo de la decisión",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class EntregaForm(forms.Form):
    fecha_limite = forms.DateField(
        label="Fecha límite de devolución",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def clean_fecha_limite(self):
        value = self.cleaned_data["fecha_limite"]
        if value < timezone.localdate():
            raise ValidationError("La fecha límite no puede ser anterior a hoy.")
        return value


class DirectoForm(EntregaForm, VersionForm):
    token = forms.CharField(widget=forms.HiddenInput)
    beneficiario = forms.ModelChoiceField(
        label="Persona que recibe", queryset=get_user_model().objects.none()
    )
    motivo = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["beneficiario"].queryset = (
            get_user_model()
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


class DevolucionForm(VersionForm):
    token = forms.CharField(widget=forms.HiddenInput)
    notas = forms.CharField(
        label="Observaciones / motivo de pérdida",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class RecepcionForm(forms.Form):
    linea = forms.IntegerField(widget=forms.HiddenInput)
    utilizables = forms.IntegerField(
        label="Utilizables", min_value=0, max_value=9999, initial=0
    )
    danados = forms.IntegerField(
        label="Dañados", min_value=0, max_value=9999, initial=0
    )
    perdidos = forms.IntegerField(
        label="Perdidos", min_value=0, max_value=9999, initial=0
    )


RecepcionFormSet = forms.formset_factory(
    RecepcionForm, extra=0, max_num=100, validate_max=True, absolute_max=101
)


class FiltroForm(forms.Form):
    q = forms.CharField(
        label="Buscar folio, persona o artículo", required=False, max_length=200
    )
    desde = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    hasta = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    estado = forms.ChoiceField(required=False, choices=[("", "Todos los estados")])

    def clean(self):
        data = super().clean()
        if data.get("desde") and data.get("hasta") and data["desde"] > data["hasta"]:
            raise ValidationError(
                "El inicio del rango no puede ser posterior al término."
            )
        return data


def catalogo_form(model):
    fields = {
        Categoria: ["nombre", "descripcion", "activo"],
        Area: ["nombre", "activo"],
        Carrera: ["codigo", "nombre", "area", "activo"],
        Asignatura: ["codigo", "nombre", "carreras", "activo"],
    }[model]
    return forms.modelform_factory(model, form=NormalizarForm, fields=fields)
