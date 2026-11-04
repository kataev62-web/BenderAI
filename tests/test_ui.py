import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QRect
from bender_ai.character import Character, CharacterPack, clamp_position
from bender_ai.app import MainWindow
from bender_ai.storage import Store


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])


def test_monitor_removal_clamps_negative_coordinates():
    assert clamp_position(-1400, 900, 120, 140, [QRect(0, 0, 800, 600)]) == (0, 460)
    assert clamp_position(-800, 20, 120, 140, [QRect(-1024, 0, 1024, 768), QRect(0, 0, 800, 600)]) == (-800, 20)


def test_character_renders_all_states_and_clickthrough(qt):
    character = Character()
    for state in ('idle', 'walk', 'thinking', 'talk', 'working', 'success', 'error', 'sleep'):
        character.state = state
        image = character.grab().toImage()
        assert not image.isNull()
    character.set_dnd(True)
    assert character.dnd
    character.close()


def test_mainwindow_offline_plan_and_settings_survive_restart(qt, tmp_path):
    store = Store(tmp_path / 'db')
    window = MainWindow(store, test_mode=True)
    window.accept_plan(__import__('bender_ai.models', fromlist=['Plan']).Plan.from_json('{"steps":[{"action":"open_app","args":{"app_id":"notepad"}}]}'))
    assert window.pending.steps[0].action == 'open_app'
    assert window.execute_button.isEnabled()
    window.stop()
    assert window.pending is None
    window.character.set_dnd(True)
    window.save_state()
    window.shutdown()
    again = MainWindow(store, test_mode=True)
    assert again.character.dnd
    again.shutdown()


def test_pack_rejects_escape_and_missing_animations(tmp_path):
    (tmp_path / 'manifest.json').write_text('{"schema":1,"animations":{"idle":["../outside.png"]}}')
    with pytest.raises(ValueError):
        CharacterPack.load(tmp_path)


def test_chat_workers_execute_once_and_keep_gui_responsive(qt, tmp_path):
    import time
    store = Store(tmp_path / 'worker-db')
    window = MainWindow(store,test_mode=True)
    window.policy.read_roots = [str(tmp_path)]
    window.policy.write_roots = [str(tmp_path)]
    window.confirm = lambda *_: True  # deterministic substitute for modal user consent only
    def idle():
        end = time.monotonic() + 3
        ticks = 0
        while window.task is not None and time.monotonic() < end:
            qt.processEvents()
            ticks += 1
            time.sleep(.005)
        assert window.task is None
        assert ticks > 0
    window.input.setText(f'Создай папку "once" в "{tmp_path}"')
    window.send()
    window.send()
    idle()
    assert not (tmp_path / 'once').exists()  # planning has no effects
    window.execute_pending()
    window.execute_pending()
    idle()
    assert (tmp_path / 'once').is_dir()
    assert len(store.runs()) == 1
    assert store.runs()[0]['status'] == 'success'
    window.shutdown()
