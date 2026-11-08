"""Render a user-supplied local 3D GLB model into BenderAI's native 8-state PNG pack.

Usage (Blender 3.6+):
blender -b -P scripts/import_3d_avatar.py -- --input scene.glb --output BenderPack \
    --author "Local model author" \
    --source-url "https://example.org/your-model-license"

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
    if source.suffix.lower() != '.glb' or not source.is_file():
        raise ValueError('Choose an existing self-contained .glb file')
    if source.is_relative_to(target):
        raise ValueError('Keep output separate from the source model')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('Output directory must be empty (no overwrites)')
    target.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    scene = bpy.context.scene
    scene.animation_data_clear()
    for obj in scene.objects:
        obj.animation_data_clear()
        if obj.type in ('CAMERA', 'LIGHT'):
            obj.hide_render = True
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
        transform.translation -= center
        obj.matrix_world = transform

    camera_data = bpy.data.cameras.new('BenderAI Camera')
    camera = bpy.data.objects.new('BenderAI Camera', camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.location = (0, -size * 3.0, size * .22)
    camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'
    # Portrait output has a narrower horizontal field of view. Leave enough
    # room for the widest pose and movement instead of clipping broad models.
    camera.data.ortho_scale = size * 2.25

    light_data = bpy.data.lights.new('Key', type='AREA')
    light = bpy.data.objects.new('Key', light_data)
    scene.collection.objects.link(light)
    light.location = (-size * 2, -size * 2, size * 3)
    light.rotation_euler = (-light.location).to_track_quat('-Z', 'Y').to_euler()
    light_data.energy = 600 * size * size
    light_data.size = size * 2

    scene.render.resolution_x = 256
    scene.render.resolution_y = 384
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    # CPU rendering also works headlessly, without an OpenGL display or GPU.
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 8
    scene.cycles.use_denoising = False
    scene.render.image_settings.color_depth = '8'
    scene.world = bpy.data.worlds.new('BenderAI World')
    scene.world.color = (.18,.18,.18)
    # Whole-model poses plus a visible state marker; no automatic limb rigging.
    state_colors = {'idle':(.3,.9,.7,1), 'walk':(.2,.7,1,1),
                    'thinking':(.65,.35,1,1), 'talk':(.1,.6,1,1),
                    'working':(1,.55,.05,1), 'success':(.2,1,.15,1),
                    'error':(1,.1,.1,1), 'sleep':(.3,.35,.55,1)}
    marker_data = bpy.data.curves.new('State marker', type='FONT')
    marker = bpy.data.objects.new('State marker', marker_data)
    scene.collection.objects.link(marker)
    marker.rotation_euler = camera.rotation_euler
    marker.location = (0,-size*.65,size*.55)
    marker_data.align_x = 'CENTER'
    marker_data.size = size*.12
    material = bpy.data.materials.new('State color')
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    emission = nodes.new('ShaderNodeEmission')
    output = nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(emission.outputs[0],output.inputs['Surface'])
    marker_data.materials.append(material)
    labels = {'idle':'o','walk':'>','thinking':'?','talk':'...',
              'working':'*','success':'+','error':'!','sleep':'zZ'}
    animations = {}
    for state in STATES:
        frames = []
        for frame in range(6):
            phase = frame / 6 * 2 * math.pi
            wave = math.sin(phase)
            root.rotation_euler = (0,0,0)
            root.location = (0,0,0)
            root.scale = (1,1,1)
            if state == 'idle':
                root.scale = (1,1,1+.025*wave)
            elif state == 'walk':
                root.rotation_euler.y = .16*wave
                root.location = (size*.07*wave,0,size*.035*abs(wave))
            elif state == 'thinking':
                root.rotation_euler.y = .20+.06*wave
                root.rotation_euler.z = .15*wave
            elif state == 'talk':
                root.scale = (1+.04*wave,1,1-.04*wave)
                root.rotation_euler.x = .08*wave
            elif state == 'working':
                root.rotation_euler.x = .20+.13*wave
                root.rotation_euler.z = .18*wave
            elif state == 'success':
                root.location.z = size*.12*abs(wave)
                root.rotation_euler.z = .30*wave
            elif state == 'error':
                root.rotation_euler.z = .25*wave
                root.location.x = size*.08*wave
            elif state == 'sleep':
                root.rotation_euler.y = .65
                root.scale = (1,1,.85+.025*wave)
            marker_data.body = labels[state] + ('.' if frame%2 else '')
            emission.inputs['Color'].default_value = state_colors[state]
            marker.scale = (1+.08*wave,)*3
            filename = f'{state}_{frame:02d}.png'
            scene.render.filepath = str(target / filename)
            bpy.ops.render.render(write_still=True)
            frames.append(filename)
        animations[state] = frames
    (target / 'manifest.json').write_text(
        json.dumps({'schema': 1, 'animations': animations, 'render': 'Local GLB rendered into PNG; not realtime 3D or rigged limb animation'}, indent=2), encoding='utf-8')
    (target / 'CREDITS.txt').write_text(
        'Model author: ' + args.author + '\nSource: ' + args.source_url + '\n'
        'Preserve model attribution. Fan artwork does not grant Futurama character rights.\n',
        encoding='utf-8')
    print('CharacterPack ready:', target)


if __name__ == '__main__':
    main()
