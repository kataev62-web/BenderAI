# Third-party software and artwork

BENDER AI is a working project name. The robot is original procedural artwork in `character.py`. No Futurama images, voices, recordings, models, logos or animation frames are distributed. No claim of affiliation is made. A public product name and the license for the application's own code remain the owner's decision before distribution.

Principal dependencies (versions are constrained in `constraints.txt`):

| Component | License / terms | Use |
|---|---|---|
| Python | PSF License | Runtime |
| PySide6 / Shiboken6 / Qt 6.8.3 | LGPL-3.0 / GPL-3.0 / commercial, component dependent | QtCore, QtGui, QtWidgets; dynamic libraries |
| pywinauto 0.6.9 | BSD-3-Clause | Windows UIA adapter |
| pywin32 310 | PSF / upstream notices | Windows APIs |
| comtypes 1.4.10 | MIT | COM bindings |
| keyring 25.6.0 | MIT | Windows Credential Manager |
| six, pywin32-ctypes, jaraco.*, more-itertools | MIT and upstream notices | Dependency utilities |
| Vosk 0.3.45 | Apache-2.0; see bundled native notices | Optional local speech recognition runtime; no model weights |
| sounddevice 0.5.2 / PortAudio | MIT / upstream PortAudio notices | Explicit push-to-talk audio input |
| requests, urllib3, certifi, charset-normalizer, idna, tqdm, websockets, srt | Upstream Apache/MIT/BSD/MPL notices | Vosk dependencies; no automatic model download |
| pytest | MIT | Development/testing |
| PyInstaller | GPL-2.0-or-later with bootloader exception | Packaging; exception permits proprietary bundled applications |
| pyinstaller-hooks-contrib | Apache-2.0 / GPL-2.0 with exceptions, see files | Packaging hooks |
| altgraph | MIT | Packaging |
| setuptools, packaging, pluggy, iniconfig | MIT / BSD / Apache, see upstream notices | Build/test utilities |
| SecretStorage, jeepney, cryptography, cffi, pycparser | Upstream BSD / Apache / MIT notices | Linux dependency chain; not used for storing cloud credentials in this application |

`scripts/collect_licenses.py` copies available LICENSE/COPYING/NOTICE files from the installed distributions and writes a version inventory into the actual Windows release `licenses/`. Build script includes this inventory. A missing upstream notice requires resolution before public distribution; the inventory is not a substitute for complying with license terms.

Only Qt modules used by this application should be packaged. Do not introduce GPL-only Qt add-ons into a proprietary release without a compatible licensing decision. For an LGPL distribution retain the relevant notices and complete license texts, offer/provide corresponding modified library sources if any, preserve recipients' ability to replace/relink the dynamically loaded libraries, and do not prohibit debugging such modifications. The `onedir` package retains separate Qt DLLs; do not obfuscate or sign-check replacements. Qt/PySide sources and terms: https://code.qt.io/ and https://www.qt.io/licensing/.

Upstream references: https://github.com/pywinauto/pywinauto, https://github.com/mhammond/pywin32, https://github.com/enthought/comtypes, https://github.com/jaraco/keyring, https://pyinstaller.org/.

No third-party model weights are shipped. Ollama models and cloud services have their own terms, selected and installed by the user. Local character packs are user-controlled data; ensure appropriate rights before redistributing them.

Vosk acoustic models have separate per-model licenses; the user selects a local model and must review its model card. No model is redistributed. Windows SAPI voices are installed system components governed by their own terms. Blender is an optional separately installed external tool (GPL); no Blender binary is redistributed in this ZIP. User GLB assets are local-only and require appropriate rights.
