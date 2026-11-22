# Casa Energy

Prototipo local para explorar el historial de calefacción: temperatura, objetivo, demanda conjunta,
contexto exterior y facturas de gas. React, TypeScript y Highcharts Stock sobre una API FastAPI y SQLite.

**Este repositorio contiene código y pruebas sintéticas. No incluye lecturas reales, facturas,
credenciales, identificadores de instalaciones ni direcciones privadas. La interfaz arranca en demo.**

## Qué permite

- Línea temporal continua con desplazamiento, pinch para zoom y consulta de lecturas.
- Sesiones calculadas como unión de demanda de habitaciones, sin duplicar solapamientos.
- Contexto meteorológico opcional mediante Open-Meteo y sensor local Hue.
- Facturas verificadas importadas localmente, con desglose entre consumo y cargos fijos.
- Actualización silenciosa de datos desfasados mientras la web está visible, con límites y bloqueo compartido.
- Acceso privado opcional por Tailscale; el servidor escucha en loopback.

Demanda no confirma llama ni mide consumo de gas. Los costes de la demo son ejemplos hipotéticos;
no son estimaciones calibradas para una vivienda. Los huecos y periodos incompletos se conservan.

## Probar sin conectar dispositivos

Requiere Python 3.11 o posterior y Node.js 22 o posterior. Los instaladores de servicios son para macOS.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-web.lock
npm ci --prefix web
npm run build --prefix web
TADO_DATA_ROOT="$PWD/.local/history" TADO_ENV_ROOT="$PWD/.local/environment" TADO_WEB_ROOT="$PWD/.local/web" .venv/bin/python web_server.py
```

Abrir `http://127.0.0.1:8765`. La demo genera datos sintéticos en memoria y no necesita cuentas.
Si el puerto está ocupado, elegir otro con `TADO_WEB_PORT`. El endpoint de salud del histórico real
requiere una base configurada; la demo funciona sin ella.

## Conectar una instalación propia

La vinculación inicial de cuentas y puentes no está automatizada. Cada usuario debe obtener sus
propias autorizaciones y mantenerlas fuera del repositorio. No es una integración oficial.

- **Tado X:** configurar `home_id`, `client_id` y `auth_path` en el archivo privado `config.json` del
  recolector. Este código puede renovar una autorización existente; no incluye el inicio OAuth.
  La configuración por defecto no contiene ningún identificador de vivienda ni cliente.
- **Hue:** el archivo privado `~/.config/tado-experiment/hue-auth.json` necesita `ip`, `host`,
  `username`, `certificate_pem`, `temperature_resource_id` y `connectivity_resource_id`.
  Se valida el certificado del puente emparejado.
- **Tiempo:** configurar `latitude` y `longitude` en el `config.json` privado del directorio de
  `TadoEnvironment`. No hay coordenadas preconfiguradas. La zona horaria del prototipo es
  `Europe/Madrid`; adaptarla de forma consistente si se utiliza en otra zona.
- **Octopus España:** cliente experimental de consultas y descarga de documentos. La sesión se
  almacena fuera del proyecto. El acceso antiguo con contraseña puede dejar de estar disponible;
  comprobar los métodos admitidos por el proveedor antes de utilizarlo. No generar claves ni
  modificar contratos desde el cliente. El modelo de consumo estructurado requiere validación
  frente a los documentos originales; no se considera automáticamente consumo medido.

La forma mínima de configuración se documenta en `examples/collector-config.example.json`.
Los valores vacíos deben sustituirse únicamente en un archivo local ignorado por Git.

## Servicios y límites

Los instaladores `install_service.py`, `install_environment_service.py` e `install_web_service.py`
crean LaunchAgents del usuario. Revisarlos y configurar las fuentes antes de ejecutarlos.
`prepare_wake_install.py` prepara un instalador separado de despertares que requiere administrador;
no lo ejecuta automáticamente. Revisar los horarios de ejemplo para cada instalación.

El Mac necesita estar encendido y con sesión iniciada. La pantalla bloqueada no detiene los servicios;
la suspensión impide servir la web. No se garantiza despertar mediante Tailscale.

El recolector Tado limita peticiones y almacenamiento y conserva el historial ante errores.
La actualización desde la web tiene un máximo adicional de cuatro intentos diarios, enfriamiento,
reserva de cuota para las recogidas programadas y exclusión entre pestañas. Nunca envía órdenes
para cambiar temperaturas. Los recolectores son procesos separados de la API web.

## Desarrollo y pruebas

```sh
.venv/bin/python -m unittest discover -s tests -v
npm run build --prefix web
python3 scripts/check_publication.py
```

Las pruebas usan datos artificiales y directorios temporales. No necesitan servicios reales.
El registro de archivos publicables se encuentra en `PUBLIC_FILES.txt`. Antes de subir cambios,
actualizarlo solo con archivos revisados y ejecutar el control de publicación. Véase
[la política de datos](docs/privacy.md).

## Dependencias y alcance

Highcharts conserva sus créditos y tiene sus propios términos de licencia. Consultar la
[licencia de Highcharts](https://www.highcharts.com/license) antes de distribuir o explotar este
prototipo. Publicar este código no concede una licencia para sus dependencias comerciales.

No se incluye una licencia de software para este proyecto por ahora. Las API de los proveedores
pueden cambiar; no se garantiza compatibilidad ni disponibilidad futura.
