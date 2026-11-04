import time
import pytest
from bender_ai.isolation import IsolatedChannel
from bender_ai.actions import Cancellation, Cancelled


def service(connection):
    while True:
        value = connection.recv()
        if value == 'hang':
            time.sleep(20)
        else:
            connection.send(('ok', value))


def test_external_call_has_hard_timeout_and_restarts():
    channel = IsolatedChannel(service, timeout=2)
    try:
        assert channel.call('hello')[1] == 'hello'
        with pytest.raises(TimeoutError):
            channel.call('hang')
        assert channel.call('restarted')[1] == 'restarted'
    finally:
        channel.close()


def test_external_call_respects_cancellation():
    channel = IsolatedChannel(service)
    token = Cancellation()
    token.event.set()
    try:
        with pytest.raises(Cancelled):
            channel.call('hang', cancellation=token)
    finally:
        channel.close()
