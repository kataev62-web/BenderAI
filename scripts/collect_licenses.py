"""Collect installed distribution notices for the actual build, never credentials."""
from importlib import metadata
from pathlib import Path
import json
import shutil
import sys


def collect(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for dist in metadata.distributions():
        name = dist.metadata.get('Name', 'unknown')
        inventory.append({'name':name,'version':dist.version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License','See upstream notices')})
        folder = destination / name
        for file in dist.files or []:
            if not any(word in file.name.casefold() for word in ('license','copying','notice')):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file() and source.stat().st_size < 2_000_000:
                folder.mkdir(exist_ok=True)
                # Preserve relative subdirectories to avoid overwriting Qt notices with equal basenames.
                safe_parts = [p for p in file.parts if p not in ('.','..')]
                target = folder.joinpath(*safe_parts)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target)
    (destination / 'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),'utf-8')
    return len(inventory)


if __name__ == '__main__':
    print(f'Collected notices for {collect(sys.argv[1])} distributions')
