# Grupo Norus

Frontend responsive y API Python para los flujos de huésped y la consola de operaciones. La API persiste en MySQL; si la API no está disponible, el frontend conserva algunos comportamientos locales de demostración, que no deben usarse para operar reservas reales.

El esquema SQL de la base de datos del modelo de negocio se encuentra en [database/schema.sql](./database/schema.sql) y está orientado a MySQL 8.

## Requisitos

- Python 3.13 o compatible con las dependencias de `requirements.txt`.
- MySQL 8, con una base de datos y usuario de aplicación.
- Un servidor SMTP y buzones de sede para enviar invitaciones, recuperación de contraseña y copias de pre check-in.
- Una terminación HTTPS/TLS 1.3 para producción.

## Puesta en marcha en Windows

Desde la raíz del proyecto, crea un entorno virtual e instala las dependencias:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Luego crea la base, el usuario de aplicación y la configuración local:

```powershell
.\scripts\configure-local.ps1
```

El asistente solicita el correo inicial y el usuario y contraseña de administración de MySQL (la contraseña se ingresa oculta), crea la base `norus` y el usuario `norus_app` con permisos limitados, genera secretos únicos y escribe `.env` con permisos restringidos al usuario actual. Si la base ya existe, pide confirmación antes de cambiar la contraseña de `norus_app`; no elimina datos. Muestra una sola vez la contraseña temporal de la cuenta administradora; guárdala en un gestor de contraseñas. No compartas la contraseña de MySQL ni los valores de `.env`.

Si una configuración previa del usuario de base de datos quedó sin permisos de claves foráneas, ejecuta `.\scripts\fix-mysql-permissions.ps1`: repara el permiso `REFERENCES` sin eliminar datos y rota la contraseña temporal del primer administrador antes de su creación inicial.

SMTP y los correos de cada sede se dejan vacíos hasta tener las credenciales y direcciones reales del proveedor. Para desarrollo local usa `NORUS_COOKIE_SECURE=false`; para producción usa `true` y sirve exclusivamente detrás de HTTPS. No uses la clave de ejemplo ni credenciales reales en el repositorio.

Inicia la aplicación:

```powershell
.\.venv\Scripts\python.exe backend\run.py
```

Abre `http://127.0.0.1:8000`. Waitress sirve el frontend y la API en el mismo origen para que las cookies de sesión y el token CSRF se apliquen sin CORS. Las tablas y manuales iniciales se crean al iniciar la aplicación. El primer administrador se crea con `NORUS_BOOTSTRAP_ADMIN_EMAIL` y `NORUS_BOOTSTRAP_ADMIN_PASSWORD`; entra una vez, configura TOTP con un autenticador y luego vuelve a iniciar sesión con el código temporal. Cambia y rota los secretos y la contraseña temporal antes de producción.

Ejecuta las pruebas:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\test_app.py -q
```

## Despliegue en Render

La app está pensada para producción con MySQL 8. Si quieres desplegarla en Render, debes usar una base MySQL real, no SQLite. SQLite solo sirve para pruebas locales o demos, pero no es una opción adecuada para reservas, sesiones, documentos cifrados y datos de operación.

1. Crea un servicio web en Render y selecciona la opción de Python.
2. Conecta este repositorio como servicio web.
3. Añade estas variables de entorno en Render:

```env
DATABASE_URL=mysql+pymysql://USER:PASSWORD@mysql+pymysql://usuario:password@host:3306/norus?charset=utf8mb4
NORUS_SECRET_KEY=generar_un_secreto_largo_y_aleatorio
NORUS_DOCUMENT_KEY=generar_una_clave_base64_urlsafe_de_32_bytes
NORUS_COOKIE_SECURE=true
PUBLIC_APP_URL=https://tu-dominio.onrender.com
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=noreply@tu-dominio.com
SMTP_STARTTLS=true
PROPERTY_EMAIL_LAS_CONDES=
PROPERTY_EMAIL_PROVIDENCIA=
PROPERTY_EMAIL_LUUM=
HOST=0.0.0.0
PORT=10000
```

4. Usa el comando de inicio del servicio:

```bash
python backend/run.py
```

5. Si prefieres configurar Render con un archivo `render.yaml`, este repositorio incluye la plantilla correspondiente.

Render expone el puerto asignado por la plataforma en la variable `PORT`; el proyecto ya está preparado para escuchar en `0.0.0.0` y usar ese puerto. La base de datos debe ser MySQL 8 real, preferiblemente administrada por un servicio externo o por la base de datos que te proporcione tu hosting.

## Funciones de la API

- Reserva: alta y cambio de estado por personal autenticado; se genera un código aleatorio y se envía una invitación si SMTP está configurado.
- Huésped: valida código y correo de reserva; la asistencia exige una sesión asociada a esa reserva.
- Pre check-in: valida contacto, nacimiento, acuerdo y firma; los documentos de identidad, firma y fotos de incidencias se cifran con AES-256-GCM antes de guardarse en MySQL.
- Estancia: entrega códigos guardados solo durante el rango de la reserva; manuales por sede e idioma; tickets y cambios de estado guardados en el servidor.
- Operaciones: tickets, asignaciones, métricas, exportación CSV, manuales, descarga autorizada de documentos/fotos y registro de eventos son endpoints protegidos por rol.
- Seguridad: sesiones HTTP-only con SameSite, token CSRF para cambios, contraseñas con hash, bloqueo temporal tras intentos fallidos, y MFA TOTP obligatorio para entrar a las operaciones.
- Check-out y encuesta: la API valida la ventana de salida y acepta una encuesta por reserva.
- Mensajería: los correos de invitación, recuperación, PDF de pre check-in y recordatorio usan SMTP configurable. Las respuestas identifican explícitamente cuándo un correo no se envió.

Los endpoints están implementados en [backend/app.py](./backend/app.py). El API sirve el frontend estático en el mismo origen. La ruta antigua `frontend/incidencias.html` redirige al dashboard; no hay formulario público de asistencia.

## Integraciones y despliegue que faltan para producción

- **Cerraduras:** los códigos se guardan cifrados en la reserva, pero aún no hay un adaptador de proveedor para crear, sincronizar y revocar códigos físicos. Se necesita el proveedor, API, credenciales y reglas de cada sede antes de habilitar códigos reales.
- **Reservas externas:** el panel permite crear reservas en MySQL; la sincronización con PMS/OTA no está conectada y requiere identificar el sistema.
- **Alertas:** la consola consulta eventos nuevos cada 10 segundos. Para notificaciones push/WhatsApp/SMS o procesamiento de correos pendientes se necesita un proveedor y un trabajador de cola.
- **Recordatorios:** `POST /api/admin/reminders/run` procesa salidas próximas a una hora. Debe programarse desde el scheduler del servidor cada pocos minutos para que sea automático.
- **TLS:** Waitress no termina HTTPS. En producción debe colocarse detrás de un proxy configurado con TLS 1.3 y confiar únicamente en los proxies declarados; no se debe confiar ciegamente en `X-Forwarded-For`.
- **Retención y cumplimiento:** define con el responsable legal la versión final del acuerdo, política de privacidad, conservación y eliminación de documentos antes de procesar datos reales.
- **Migraciones/operación:** `create_all` prepara una base nueva; no reemplaza una estrategia de migraciones versionadas para cambios posteriores. Configura backups cifrados, rotación de claves, monitoreo, límites de carga y restauración probada.

La presentación usa un contenedor más ancho en escritorio y reduce los márgenes en tablet y móvil; la consola mantiene tablas desplazables y formularios apilados en pantallas angostas.
