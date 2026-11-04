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
    class Handle:
        closed = False
        def Close(self): self.closed = True
    current, child = Handle(), Handle()
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

@pytest.mark.parametrize('failure_code', [1312, 5])
def test_linked_token_absence_only_uses_restricted_fallback(tokens, monkeypatch, failure_code):
    import windows_fixture_process as launcher
    current, child, data = tokens
    api = sys.modules['win32security']
    calls = []
    api.TokenLinkedToken = 'linked'
    original = api.GetTokenInformation
    class TokenError(OSError):
        winerror = failure_code
    def info(token, information):
        if information == 'linked': raise TokenError('no linked token')
        return original(token, information)
    def restrict(token, flags, disabled, deleted, restricted):
        calls.append(('restrict', token, flags, disabled, deleted, restricted))
        return child
    def set_info(token, information, value):
        calls.append(('set', token, information, value))
        data['integrity'] = value
    monkeypatch.setattr(api, 'GetTokenInformation', info)
    api.CreateRestrictedToken = restrict
    api.ConvertStringSidToSid = lambda value: IntegritySid(0x2000) if value == 'S-1-16-8192' else None
    api.SetTokenInformation = set_info
    if failure_code != 1312:
        with pytest.raises(RuntimeError, match='5'):
            launcher._fixture_token(current)
        assert calls == []
    else:
        assert launcher._fixture_token(current) is child
        assert calls[0] == ('restrict', current, 0x5, [('administrators', 0)], None, None)
        assert calls[1][:3] == ('set', child, 'integrity')
        assert calls[1][3][1] == 0x20


def test_linked_token_is_preferred_without_restriction(tokens, monkeypatch):
    import windows_fixture_process as launcher
    current, child, _ = tokens
    api = sys.modules['win32security']
    api.TokenLinkedToken = 'linked'
    original = api.GetTokenInformation
    api.GetTokenInformation = lambda token, info: child if info == 'linked' else original(token, info)
    assert launcher._fixture_token(current) is child


@pytest.mark.parametrize('unsafe_field,unsafe_value', [
    ('elevation', 1), ('groups', [('administrators', 4)]),
    ('session', 9), ('user', ('another-user', 0)),
])
def test_restricted_token_still_must_pass_unchanged_policy_and_closes_on_failure(tokens, unsafe_field, unsafe_value):
    import windows_fixture_process as launcher
    current, child, data = tokens
    api = sys.modules['win32security']
    api.TokenLinkedToken = 'linked'
    original = api.GetTokenInformation
    class MissingLogon(OSError):
        winerror = 1312
    def info(token, information):
        if information == 'linked': raise MissingLogon('no session')
        return original(token, information)
    api.GetTokenInformation = info
    api.CreateRestrictedToken = lambda *args:child
    api.ConvertStringSidToSid = lambda value: IntegritySid(0x2000)
    def set_medium(token, information, value): data['integrity'] = value
    api.SetTokenInformation = set_medium
    data[unsafe_field] = unsafe_value
    with pytest.raises(RuntimeError):
        launcher._fixture_token(current)
    assert child.closed
