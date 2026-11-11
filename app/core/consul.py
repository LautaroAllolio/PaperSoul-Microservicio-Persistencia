"""Registro de la instancia en la API HTTP del agente Consul."""

import asyncio
import ipaddress
import json
import logging
import socket
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import Settings

logger = logging.getLogger(__name__)

CONSUL_HTTP_TIMEOUT = 2.0
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 30.0

TRAEFIK_TAGS = (
    "traefik.enable=true",
    "traefik.http.routers.persistencia.rule=Host(`persistencia.universidad.localhost`) && Method(`GET`)",
    "traefik.http.routers.persistencia.entrypoints=https",
    "traefik.http.routers.persistencia.tls=true",
    "traefik.http.routers.persistencia.middlewares=persistencia-retry",
    "traefik.http.middlewares.persistencia-retry.retry.attempts=2",
    "traefik.http.middlewares.persistencia-retry.retry.initialinterval=500ms",
)


def _detect_advertise_address() -> str:
    """Devuelve la primera IPv4 del host que no sea loopback."""
    for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        address = result[4][0]
        if isinstance(address, str) and not ipaddress.ip_address(address).is_loopback:
            return address
    raise OSError("No se encontró una dirección IPv4 no loopback para anunciar en Consul")


class ConsulRegistrar:
    """Registra el servicio en segundo plano y lo desregistra al detenerse."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.service_id = settings.service_id or f"persistencia-{uuid4().hex[:8]}"
        self._advertise_address = settings.advertise_addr or _detect_advertise_address()
        self._base_url = settings.consul_http_addr.rstrip("/")
        self._stop_event = asyncio.Event()
        self._registration_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Inicia el registro sin esperar a que Consul esté disponible."""
        if self._registration_task is None or self._registration_task.done():
            self._registration_task = asyncio.create_task(self._register_until_available())

    async def stop(self) -> None:
        """Detiene los reintentos y solicita el desregistro al agente."""
        self._stop_event.set()
        try:
            if self._registration_task is not None:
                await self._registration_task
        finally:
            await self.deregister()

    async def register(self) -> None:
        """Registra la instancia en Consul sin bloquear el event loop."""
        await asyncio.to_thread(self._register_service)

    async def deregister(self) -> None:
        """Elimina la instancia del agente Consul sin bloquear el event loop."""
        await asyncio.to_thread(self._deregister_service)

    async def _register_until_available(self) -> None:
        delay = INITIAL_RETRY_DELAY
        while not self._stop_event.is_set():
            try:
                await self.register()
            except (OSError, TimeoutError, URLError) as exc:
                logger.warning(
                    "No se pudo registrar %s en Consul; próximo intento en %.1f s (%s)",
                    self.service_id,
                    delay,
                    type(exc).__name__,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except TimeoutError:
                    delay = min(delay * 2, MAX_RETRY_DELAY)
            else:
                logger.info("Servicio %s registrado en Consul", self.service_id)
                return

    def _register_service(self) -> None:
        registration = {
            "Name": self._settings.service_name,
            "ID": self.service_id,
            "Address": self._advertise_address,
            "Port": self._settings.service_port,
            "Check": {
                "HTTP": f"http://{self._advertise_address}:{self._settings.service_port}/ready",
                "Interval": "3s",
                "Timeout": "1s",
                "DeregisterCriticalServiceAfter": "1m",
            },
            "Tags": [
                *TRAEFIK_TAGS,
                "traefik.http.services.persistencia.loadbalancer.server.port="
                f"{self._settings.service_port}",
            ],
        }
        self._put("/v1/agent/service/register", registration)

    def _deregister_service(self) -> None:
        service_id = quote(self.service_id, safe="")
        self._put(f"/v1/agent/service/deregister/{service_id}")

    def _put(self, path: str, body: dict[str, object] | None = None) -> None:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{self._base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(request, timeout=CONSUL_HTTP_TIMEOUT) as response:
            if not 200 <= response.status < 300:
                raise URLError(f"Consul returned HTTP {response.status}")
