import pytest
from bender_ai import brain

@pytest.mark.parametrize('text', ['Привет', 'Как дела?', 'Почему небо голубое?', 'Как открыть папку в Windows?'])
def test_regular_conversation_never_requests_an_os_plan(text):
    assert brain.classify_message(text) == 'chat'

@pytest.mark.parametrize('text', ['Открой диск D', r'Открой папку D:\Тест', 'Открой Блокнот', 'Создай папку "Новая" в "D:\\Тест"'])
def test_explicit_os_commands_are_plans(text):
    assert brain.classify_message(text) == 'command'


def test_unknown_ambiguous_text_requires_explicit_mode():
    assert brain.classify_message('тестовая папка завтра') == 'ambiguous'


def test_drive_and_unquoted_folder_rules_are_structured_data():
    assert brain.RuleBasedProvider().plan('Открой диск D').steps[0].args == {'path': 'D:\\'}
    assert brain.RuleBasedProvider().plan(r'Открой папку D:\Тест').steps[0].args == {'path': r'D:\Тест'}
