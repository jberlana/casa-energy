# Setup and operation

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

La forma mínima de configuración se documenta en [examples/collector-config.example.json](../examples/collector-config.example.json).
Los valores vacíos deben sustituirse únicamente en un archivo local ignorado por Git.

## Servicios y límites

Los módulos de instalación de `scripts/macos/`
crean LaunchAgents del usuario. Revisarlos y configurar las fuentes antes de ejecutarlos.
`python3 -m scripts.macos.prepare_wake_install` prepara un instalador separado de despertares que requiere administrador;
no lo ejecuta automáticamente. Revisar los horarios de ejemplo para cada instalación.

El Mac necesita estar encendido y con sesión iniciada. La pantalla bloqueada no detiene los servicios;
la suspensión impide servir la web. No se garantiza despertar mediante Tailscale.

El recolector Tado limita peticiones y almacenamiento y conserva el historial ante errores.
La actualización desde la web tiene un máximo adicional de cuatro intentos diarios, enfriamiento,
reserva de cuota para las recogidas programadas y exclusión entre pestañas. Nunca envía órdenes
para cambiar temperaturas. Los recolectores son procesos separados de la API web.
Consultar [scripts/README.md](../scripts/README.md) para los comandos de instalación.
