# Pañol de informática

Plataforma Django para inventario por cantidades, kits de materiales, carritos personales guardados, solicitudes de docentes/estudiantes, préstamos y devoluciones parciales. Conserva los usuarios, cargos, allauth/MFA, navegación, diseño y modo claro/oscuro de la base. Idioma `es-cl`, zona `America/Santiago`.

## Uso del pañol

ADMIN y MANAGER administran inventario, kits, solicitudes, entregas y devoluciones. RESTRICTED prepara carritos, guarda pedidos para repetirlos y consulta solo sus solicitudes/préstamos. Los kits agrupan componentes del inventario; no llevan un segundo stock. Docente/estudiante es el tipo del pedido y no añade un cargo ni una cuenta distinta.

1. En **Catálogos del pañol**, registra una categoría. Áreas/carreras/asignaturas son opcionales.
2. En **Inventario**, registra artículos y cantidades iniciales. Para cambiar cantidades después, usa **Ajustar existencias** con motivo.
3. En **Kits del pañol**, prepara conjuntos de artículos por cantidad.
4. En **Mi carrito**, agrega artículos/kits, guarda el pedido con un nombre o envía una solicitud. Para modificar un pedido guardado, cárgalo, edita el carrito y guarda con el mismo nombre confirmando su actualización.
5. Un operador aprueba o rechaza la solicitud. **Aprobar no reserva ni descuenta**. **Confirmar entrega** crea el préstamo y descuenta todo de forma atómica. También puede entregar un préstamo directo desde su carrito a una cuenta habilitada.
6. **Registrar devolución** admite recepciones parciales por artículo. Solo las unidades utilizables vuelven a disponibles; daños quedan separados y pérdidas como incidencias justificadas, sin sanciones automáticas.

Inicio conserva la tabla de actividad de usuarios, permanece primero en el menú y muestra cifras reales: globales para operadores y propias para solicitantes. Movimientos conserva operador, fecha, motivo y documento de origen. No se incluyen datos/cuentas iniciales del ZIP externo ni solicitudes públicas sin cuenta.

Si olvidaste tu contraseña, usa la ayuda del login y comunícate con el administrador de la plataforma. Este proyecto no envía enlaces de recuperación por correo; las URLs antiguas muestran la misma orientación y no aceptan restablecimientos por token. El administrador autorizado conserva la acción de restablecimiento del listado de usuarios. El menú de cuenta incluye perfil, seguridad y cierre de sesión, sin acceso a correo electrónico. El cambio de tu propia contraseña con sesión y MFA siguen disponibles.

### Instalar el nuevo esquema en una base existente

La migración `PanolApp/0001_initial.py` está incluida y fue ensayada con datos ficticios en SQLite temporal. No se ha aplicado a la base local existente por esta entrega. Después de respaldar y elegir explícitamente la base de destino, usa el entorno configurado:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

Sin instalar el esquema, Inicio informa que el resumen del pañol no está disponible; no muestra cifras ficticias. No copies la base de datos del sistema Flask. Revertir la migración elimina datos del módulo: para una instalación con uso real, coordina restauración de código y respaldo.

## Instalar

Versión verificada: **Python 3.12.16 y Django 5.2.18 LTS**. `.python-version` registra el runtime esperado; no actualiza automáticamente un Python ya instalado. Clona el proyecto:

```bash
git clone --depth 1 --single-branch --branch main https://github.com/fersrm/PANOL.git
cd PANOL
```

Con Python 3.12.16 instalado, crea un entorno virtual e instala las dependencias. En Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.2.2
python -m pip install --require-hashes -r requirements.txt
python -m pip check
```

En Windows comprueba primero `py -3.12 --version`: debe indicar 3.12.16. Crea el entorno con `py -3.12 -m venv .venv` y actívalo con `.\.venv\Scripts\Activate.ps1`. Después ejecuta los mismos comandos de instalación y comprobación de dependencias. Si usas uv para obtener el runtime parcheado, `uv venv --python 3.12.16 --seed .venv` prepara el entorno; no copies entornos de otros equipos.

Para actualizar una instalación existente, conserva el entorno anterior y valida uno nuevo antes de sustituirlo. En esta copia la corrección se comprobó en `.venv-produccion` (Python 3.12.16); `.venv` quedó intacto. Para usar el nuevo entorno local: `.\.venv-produccion\Scripts\Activate.ps1`. Este cambio de entorno no aplica migraciones ni cambia la base de datos.

El runtime local descargado para ese entorno está en `.work-panol/python`; consérvalo mientras uses `.venv-produccion`, o recrea el entorno sobre una instalación estable de Python 3.12.16. Ninguno de esos directorios se publica en Git.

`requirements.txt` fija 51 paquetes de aplicación, herramientas, opcionales y transitivas, con hashes SHA-256 de sus distribuciones de PyPI para Windows/Linux. Conserva los hashes al actualizar; `pip freeze` por sí solo no los regenera. Tailwind 4.3.0 usa un binario standalone, sin npm/Node.js. Su CSS compilado se conserva en `theme/static/css/dist/styles.css`.

## Configuración de cada instalación

Crea `.env` en la raíz, protegido y excluido de Git. En una instalación nueva genera una clave propia con:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Guarda el resultado únicamente en el entorno de esa instalación. Al actualizar o trasladar una instalación existente, conserva su `SECRET_KEY` para mantener las firmas y sesiones existentes. Respalda conjuntamente configuración, base y archivos antes de actualizar. Los autenticadores MFA están en la base; el adaptador actual de allauth no añade cifrado a sus secretos, por lo que la base y sus respaldos requieren protección propia.

### Desarrollo local · HTTP

Usa una clave exclusiva de desarrollo y estos valores en tu `.env` local:

```dotenv
SECRET_KEY=<genera-una-clave-unica>
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SECURE_HSTS_SECONDS=0
```

### Producción · HTTPS

Usa una clave exclusiva de esa instalación y reemplaza el dominio de ejemplo por el del servidor:

```dotenv
SECRET_KEY=<genera-una-clave-unica-para-esta-instalacion>
DEBUG=False
ALLOWED_HOSTS=plataforma.example.org
CSRF_TRUSTED_ORIGINS=https://plataforma.example.org
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=3600
```

Las variables anteriores se leen en `core/settings.py`. La base se selecciona en `.env` con `DATABASE_URL=sqlite:///db.sqlite3`; si se omite, se conserva SQLite en `db.sqlite3`. Las rutas SQLite relativas parten de la raíz del proyecto. [`.env.example`](.env.example) incluye la configuración local y alternativas comentadas para PostgreSQL y MySQL: activa solo una `DATABASE_URL`, crea la base e instala el controlador correspondiente (`psycopg` o `mysqlclient`) antes de cambiar de motor. Cambiar la URL no traslada los datos existentes. El correo sigue usando consola; configurar SMTP requiere adaptar y verificar los settings.

La configuración confía en `HTTP_X_FORWARDED_PROTO=https`. Comprueba que el proxy del destino establezca ese encabezado y controle los valores enviados por el cliente. Las pruebas usan `core.test_settings`, que no lee `.env`; no uses esos settings para servir la aplicación.

## Instalación del esquema y cuenta inicial

La configuración normal usa SQLite en `db.sqlite3`. Sobre una **instalación nueva y una base vacía**, prepara tablas y la cuenta inicial:

```bash
python manage.py migrate
python manage.py createsuperuser
```

Los cargos se representan con `UsuarioApp.Position` y el perfil relaciona la cuenta con su cargo. Las acciones administrativas y Registro exigen cargo incluso al superusuario; configura el cargo ADMIN del operador desde el administrador Django en `/admin/`. No hay cuentas ni contraseñas iniciales incluidas.

Para una base existente, respalda y ensaya las migraciones antes de aplicarlas. Está disponible `python manage.py verificar_actualizacion_allauth --help` para conocer el preflight de actualización.

Para iniciar desarrollo con el `.env` HTTP descrito arriba:

```bash
python manage.py runserver
```

## CI y verificación local

La verificación usa `core.test_settings` y nunca la base de desarrollo. El workflow [CI Django](.github/workflows/ci.yml) se ejecutará en push a `main`, pull requests hacia `main` y ejecución manual, una vez publicado. Usa Ubuntu 24.04/Python 3.12.16, instalación con hashes, `pip check` y los comandos siguientes. Las acciones están fijadas por SHA, con permisos de lectura y checkout sin credenciales persistentes. No realiza despliegues ni necesita secretos de producción.

```bash
python manage.py check --settings=core.test_settings
python manage.py makemigrations --check --dry-run --settings=core.test_settings
python manage.py test UsuarioApp homeApp PanolApp --noinput --settings=core.test_settings
```

`core.test_settings` no lee `.env`: utiliza SQLite en memoria, correo/caché locales y archivos temporales. La suite verificada contiene 127 pruebas: 82 de la base/acceso y 45 del pañol, incluidas recuperación administrativa sin correo, concurrencia, navegación y migración/reversión ficticia. El workflow pasó actionlint y las pruebas también se comprobaron en GitHub Actions. Consulta las ejecuciones de cada commit en [CI Django](https://github.com/fersrm/PANOL/actions/workflows/ci.yml). Las pruebas no aplican migraciones a tu base existente ni despliegan el proyecto.

## Preparar el servidor

Configura el `.env` de producción descrito arriba y prepara el servidor WSGI según tu hosting. La entrada es `core.wsgi:application` (módulo `core.wsgi`, objeto `application`); el servidor WSGI no está incluido en las dependencias actuales.

```bash
python manage.py check --deploy
python manage.py collectstatic --noinput
```

Sirve `/static/` desde `staticfiles/` y `/media/` desde `media/`. La base y las subidas necesitan almacenamiento persistente, permisos y respaldo/restauración propios: clonar el código no recupera cuentas, MFA ni fotos. Configura correo, logs y acceso según la instalación. Axes y preventconcurrentlogins permanecen desactivados. `runserver` es solo para desarrollo. El alojamiento y el servidor WSGI se configuran por instalación.

## Personalizar y mantener liviana la copia

Los colores se centralizan en `static/css/tokens.css`. Si cambias clases, plantillas o fuentes Tailwind, recompila:

```bash
python manage.py tailwind --settings=core.test_settings install
python manage.py tailwind --settings=core.test_settings build
```

Conserva las raíces de fuentes en `theme/static_src/src/styles.css` y versiona el CSS compilado. Cambiar valores de colores existentes no exige compilar.

Se versionan código, migraciones, pruebas, dependencias y estáticos. Se excluyen `.env`, bases, archivos personales, entornos, cachés, binarios descargados y `staticfiles/`. Por decisión del propietario, docs/specs/prompts, instrucciones SDD y skills locales se conservan en el equipo y quedan fuera del seguimiento. Este README corresponde al pañol de informática y no incluye propuestas ni capturas de diseño de la base original.
