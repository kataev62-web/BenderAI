"""Portable checks for the test launcher's strict token admission policy."""
import sys
from types import SimpleNamespace

import pytest

from windows_fixture_process import _verify_medium_token


class IntegritySid:
    def __init__(self, level):
        self.level = level

    def GetSubAuthorityCount(self):
        return 1

    def GetSubAuthority(self, index):
        assert index == 0
        return self.level


@pytest.fixture
def tokens(monkeypatch):
    current, child = object(), object()
    data = {
        current: {'user': ('same-user', 0), 'session': 3},
        child: {'elevation': 0, 'integrity': IntegritySid(0x2000),
                'groups': [('administrators', 0x10)],  # deny-only is acceptable
                'user': ('same-user', 0), 'session': 3},
    }
    api = SimpleNamespace(TokenElevation='elevation', TokenIntegrityLevel='integrity',
                          TokenGroups='groups', TokenUser='user', TokenSessionId='session',
                          WinBuiltinAdministratorsSid='administrators',
                          CreateWellKnownSid=lambda sid, domain: sid,
                          GetTokenInformation=lambda token, information: data[token][information])
    monkeypatch.setitem(sys.modules, 'win32security', api)
    return current, child, data[child]


@pytest.mark.parametrize('tuple_form', [False, True])
def test_verified_medium_nonadmin_same_user_session_is_accepted(tokens, tuple_form):
    current, child, data = tokens
    if tuple_form:
        data['integrity'] = (data['integrity'], 0x20)
    _verify_medium_token(child, current)


@pytest.mark.parametrize('field,value,message', [
    ('elevation', 1, 'elevated'),
    ('integrity', IntegritySid(0x3000), 'medium integrity'),
    ('integrity', IntegritySid(0x1000), 'medium integrity'),
    ('integrity', IntegritySid(0x2100), 'medium integrity'),
    ('groups', [('administrators', 0x00000004)], 'administrators'),
    ('user', ('different-user', 0), 'user and session'),
    ('session', 4, 'user and session'),
])
def test_unsafe_or_wrong_identity_tokens_are_rejected(tokens, field, value, message):
    current, child, data = tokens
    data[field] = value
    with pytest.raises(RuntimeError, match=message):
        _verify_medium_token(child, current)


def test_token_query_failure_is_not_treated_as_safe(tokens, monkeypatch):
    current, child, _ = tokens
    def inaccessible(*args):
        raise OSError('token inaccessible')
    monkeypatch.setattr(sys.modules['win32security'], 'GetTokenInformation', inaccessible)
    with pytest.raises(OSError):
        _verify_medium_token(child, current)
