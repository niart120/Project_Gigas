import asyncio

import pytest

from gigas.game_io.input.config import get_protocol
from gigas.game_io.input.models import InputState, InputStep
from gigas.services.input import InputService, InputServiceError, OperationResult
from tests.integration.test_input_service import Device


def test_manual_updates_and_short_tap_keep_order_without_a_chat() -> None:
    async def scenario() -> None:
        device = Device()
        service = InputService()
        await service.connect(device)
        try:
            a = InputState(buttons=("A",))
            b = InputState(buttons=("B",))
            assert service.update_manual(a).code == "OK"
            assert service.update_manual(a).code == "OK"
            assert service.update_manual(b).code == "OK"
            assert service.update_manual(InputState()).code == "OK"
            await service.release_manual()
            assert device.calls == ["open", a, b, InputState()]
            assert service.manual_available
            assert service.update_manual(a).code == "OK"
            assert service.update_manual(InputState()).code == "OK"
            await service.release_manual()
            assert device.calls[-2:] == [a, InputState()]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_manual_and_finite_operations_do_not_release_each_other() -> None:
    async def scenario() -> None:
        device = Device()
        service = InputService()
        await service.connect(device)
        try:
            a = InputState(buttons=("A",))
            service.update_manual(a)
            await device.pressed.wait()
            assert service.stop_finite() is None
            refused = service.start((InputStep("press", 1, a),))
            assert isinstance(refused, OperationResult)
            assert refused.code == "BUSY"
            assert device.calls == ["open", a]
            await service.release_manual()
            device.pressed.clear()
            finite = service.start((InputStep("press", 10000, a),))
            assert isinstance(finite, tuple)
            await device.pressed.wait()
            await service.release_manual()
            assert not finite[1].done()
            assert service.update_manual(InputState(buttons=("B",))).code == "BUSY"
            service.stop_finite()
            assert (await finite[1]).code == "INTERRUPTED"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_release_remains_owned_after_waiter_cancel_and_rejects_new_hold() -> None:
    async def scenario() -> None:
        releasing, finish = asyncio.Event(), asyncio.Event()

        class SlowNeutral(Device):
            async def send(self, state: InputState) -> None:
                if state == InputState():
                    releasing.set()
                    await finish.wait()
                await super().send(state)

        device = SlowNeutral()
        service = InputService()
        await service.connect(device)
        try:
            service.update_manual(InputState(buttons=("A",)))
            await device.pressed.wait()
            waiter = asyncio.create_task(service.release_manual())
            await releasing.wait()
            waiter.cancel()
            assert service.update_manual(InputState(buttons=("B",))).code == "BUSY"
            assert not service.manual_available
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert service.manual_available
            assert service.update_manual(InputState(buttons=("B",))).code == "OK"
            await service.release_manual()
            assert device.calls[-3:] == [InputState(), InputState(buttons=("B",)), InputState()]
        finally:
            finish.set()
            await service.close()

    asyncio.run(scenario())


def test_manual_capabilities_and_failure_diagnostic_do_not_replay_old_failure() -> None:
    async def scenario() -> None:
        device = Device()
        device.protocol = get_protocol("3ds")
        results = []
        service = InputService(on_manual_result=results.append)
        await service.connect(device)
        try:
            assert service.update_manual(InputState(buttons=("PLUS",))).code == "INVALID_SEQUENCE"
            assert device.calls == ["open"]
            service.update_manual(InputState(buttons=("START",)))
            await device.pressed.wait()
            device.fail_neutral = True
            with pytest.raises(InputServiceError):
                await service.release_manual()
            assert not service.connected
            assert results[-1].neutral == "unconfirmed"
            await service.release_manual()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_close_keeps_manual_release_failure_after_early_shutdown_signal() -> None:
    async def scenario() -> None:
        device = Device()
        service = InputService()
        await service.connect(device)
        service.update_manual(InputState(buttons=("A",)))
        await device.pressed.wait()
        device.fail_neutral = True
        service.begin_close()
        await service.drain_input()
        with pytest.raises(InputServiceError):
            await service.close()
        assert device.calls[-1] == "close"
        assert not service.connected

    asyncio.run(scenario())


def test_slow_send_coalesces_axes_and_center_does_not_end_gesture() -> None:
    async def scenario() -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        class SlowFirstSend(Device):
            async def send(self, state: InputState) -> None:
                if not entered.is_set():
                    entered.set()
                    await finish.wait()
                await super().send(state)

        device = SlowFirstSend()
        service = InputService()
        await service.connect(device)
        first = InputState(left_stick=(50, 0))
        last = InputState(left_stick=(-100, 0))
        try:
            service.update_manual(first)
            await entered.wait()
            for x in range(1, 101):
                assert service.update_manual(InputState(left_stick=(x, 0))).code == "OK"
            assert service.update_manual(InputState()).code == "OK"
            assert service.manual_available
            assert service.update_manual(last).code == "OK"
            release = asyncio.create_task(service.release_manual())
            finish.set()
            await release
            assert device.calls == ["open", first, last, InputState()]
        finally:
            finish.set()
            await service.close()

    asyncio.run(scenario())


def test_completed_manual_task_is_not_reused_before_ownership_cleanup() -> None:
    async def scenario() -> None:
        device = Device()
        service = InputService()
        await service.connect(device)
        try:
            service.update_manual(InputState(buttons=("A",)))
            await device.pressed.wait()
            operation = service.stop()
            assert operation is not None
            # Observe task completion before its ownership callback.
            while not operation.done():  # noqa: ASYNC110
                await asyncio.sleep(0)
            assert not service.manual_available
            assert service.update_manual(InputState(buttons=("B",))).code == "BUSY"
            finite = service.start((InputStep("press", 1, InputState(buttons=("X",))),))
            assert isinstance(finite, OperationResult)
            assert finite.code == "BUSY"
            await asyncio.sleep(0)
            assert service.update_manual(InputState(buttons=("B",))).code == "OK"
            await service.release_manual()
            assert device.calls[-2:] == [InputState(buttons=("B",)), InputState()]
        finally:
            await service.close()

    asyncio.run(scenario())
