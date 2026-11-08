"""CLI entry for the trusted local GLB converter, launched through Blender.

blender --factory-startup --disable-autoexec -b -P scripts/import_3d_avatar.py -- \
    --input model.glb --output NewPack

GUI imports use bender_ai.avatar_import for validation, cancellation and timeout.
No assets are downloaded or bundled. Output is PNG animation, not realtime 3D.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bender_ai.avatar_blender import STATES, parse_args, main


if __name__ == '__main__':
    from bender_ai.avatar_import import validate_glb
    validate_glb(parse_args(sys.argv).input)
    main()
