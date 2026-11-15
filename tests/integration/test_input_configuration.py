import asyncio
from typing import TYPE_CHECKING

from gigas.game_io.input.config import resolve_serial_config
from gigas.services.input import InputService
from tests.integration.test_input_service import Device

if TYPE_CHECKING:
    from gigas.game_io.input.config import SerialConfig


def test_input_settings_lifecycle_without_codex() -> None:
    async def scenario() -> None:
        devices: list[Device] = []
        results = []

        def factory(_config: "SerialConfig") -> Device:
            device = Device()
            devices.append(device)
            return device

        service = InputService(factory=factory, on_connection_changed=results.append)
        config = resolve_serial_config("COM3", "ch552", None)
        try:
            for target in (config, config, None, config):
                service.request_config(target)
                await service.apply_config()
            assert [result.connected for result in results] == [True, True, False, True]
            assert not any(result.error for result in results)
            assert len(devices) == 2
            assert devices[0].calls == ["open", "close"]
        finally:
            await service.close()
        assert devices[1].calls == ["open", "close"]

    asyncio.run(scenario())
