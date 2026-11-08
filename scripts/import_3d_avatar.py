"""Render a user-supplied 3D .glb/.gltf model into BenderAI's native 8-state PNG pack.

Usage (Blender 3.6+):
blender -b -P scripts/import_3d_avatar.py -- --input scene.glb --output BenderPack \
    --author "Mateus Bühler Schallenberger" \
    --source-url "https://sketchfab.com/3d-models/bender-futurama-8eda06b87dab4944940360caa0811d1e"

Does not download, bundle, or redistribute copyrighted character assets.
"""
import argparse
import json
import math
from pathlib import Path
import sys

STATES = ('idle', 'walk', 'thinking', 'talk', 'working', 'success', 'error', 'sleep')


def parse_args(argv):
    p = argparse.ArgumentParser(description='Render glTF to BenderAI character pack')
    p.add_argument('--input', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--author', default='Unknown; check source asset')
    p.add_argument('--source-url', default='')
    return p.parse_args(argv[argv.index('--') + 1:] if '--' in argv else argv[1:])


def main():
    import bpy
    from mathutils import Vector

    args = parse_args(sys.argv)
    source = Path(args.input).expanduser().resolve()
    target = Path(args.output).expanduser().resolve()
    if source.suffix.lower() not in ('.glb', '.gltf') or not source.is_file():
        raise ValueError('Choose an existing .glb/.gltf file')
    if source.is_relative_to(target):
        raise ValueError('Keep output separate from the source model')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('Output directory must be empty (no overwrites)')
    target.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    scene = bpy.context.scene
    meshes = [obj for obj in scene.objects if obj.type == 'MESH']
    if not meshes:
        raise ValueError('No renderable mesh in selected glTF model')
    points = [obj.matrix_world @ Vector(c) for obj in meshes for c in obj.bound_box]
    low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    center = (low + high) / 2
    size = max(*(high - low), .01)

    # Parent only scene roots: keep imported child rigs and meshes together.
    root = bpy.data.objects.new('BenderAI Root', None)
    scene.collection.objects.link(root)
    for obj in [x for x in scene.objects if x is not root and x.parent is None]:
        transform = obj.matrix_world.copy()
        obj.parent = root
        obj.matrix_world = transform

    camera_data = bpy.data.cameras.new('BenderAI Camera')
    camera = bpy.data.objects.new('BenderAI Camera', camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.location = (0, -size * 3.0, size * .22)
    camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = size * 1.35

    light_data = bpy.data.lights.new('Key', type='AREA')
    light = bpy.data.objects.new('Key', light_data)
    scene.collection.objects.link(light)
    light.location = (-size * 2, -size * 2, size * 3)
    light.rotation_euler = (-light.location).to_track_quat('-Z', 'Y').to_euler()
    light_data.energy = 600
    light_data.size = size * 2

    scene.render.resolution_x = 256
    scene.render.resolution_y = 384
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.engine = 'BLENDER_EEVEE_NEXT' if bpy.app.version >= (4, 2, 0) else 'BLENDER_EEVEE'

    angles = {'idle': .05, 'walk': .12, 'thinking': .11, 'talk': .03,
              'working': .17, 'success': .18, 'error': .10, 'sleep': .0}
    animations = {}
    for state in STATES:
        frames = []
        for frame in range(6):
            phase = frame / 6 * 2 * math.pi
            root.rotation_euler = (0, 0, math.sin(phase) * angles[state])
            bounce = .02 if state in ('walk', 'talk', 'success') else .005
            root.location = -center + Vector((0, 0, math.sin(phase) * size * bounce))
            filename = f'{state}_{frame:02d}.png'
            scene.render.filepath = str(target / filename)
            bpy.ops.render.render(write_still=True)
            frames.append(filename)
        animations[state] = frames
    (target / 'manifest.json').write_text(
        json.dumps({'schema': 1, 'animations': animations}, indent=2), encoding='utf-8')
    (target / 'CREDITS.txt').write_text(
        'Model author: ' + args.author + '\nSource: ' + args.source_url + '\n'
        'Preserve model attribution. Fan artwork does not grant Futurama character rights.\n',
        encoding='utf-8')
    print('CharacterPack ready:', target)


if __name__ == '__main__':
    main()
