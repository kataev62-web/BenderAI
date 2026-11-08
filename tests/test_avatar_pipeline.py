"""Checks the import contract; actual Blender rendering requires a local Blender install."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "import_3d_avatar.py"


def load_script():
    spec = spec_from_file_location("bender_avatar_import", SOURCE)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_avatar_states_match_bender_character_pack():
    script = load_script()
    from bender_ai.character import STATES
    assert script.STATES == STATES
    assert len(script.STATES) == 8


def test_blender_cli_import_arguments():
    script = load_script()
    args = script.parse_args([
        "blender", "-b", "-P", "import_3d_avatar.py", "--",
        "--input", "scene.glb", "--output", "pack",
        "--author", "Artist", "--source-url", "https://example.org/model",
    ])
    assert args.input == "scene.glb"
    assert args.output == "pack"
    assert args.author == "Artist"
