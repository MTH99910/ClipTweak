#!/usr/bin/env bash
# Install only a desktop entry pointing to this checkout; do not copy dependencies.
set -euo pipefail
project_dir="$(dirname -- "$(dirname -- "$(readlink -f -- "$0")")")"
python - "$project_dir" <<'PY'
from pathlib import Path
import os, sys, shutil
project = Path(sys.argv[1])
launcher = str(project / 'cliptweak-launcher')
# Desktop Entry Exec quoting is distinct from shell quoting.
escaped = launcher.replace('\\', '\\\\\\\\').replace('"', '\\\\"').replace('`', '\\\\`').replace('$', '\\\\$').replace('%', '%%')
entry = (project / 'cliptweak.desktop').read_text()
entry = entry.replace('Exec=cliptweak %f', 'Exec="' + escaped + '" %f')
data_home = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))
icon = data_home / 'icons' / 'cliptweak.png'
icon.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(project / 'cliptweak' / 'resources' / 'cliptweak.png', icon)
# Icon is a desktop-entry string, not an Exec argument. Keep spaces literal.
icon_value = str(icon).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
entry = entry.replace('Icon=cliptweak', 'Icon=' + icon_value)
applications = data_home / 'applications'
applications.mkdir(parents=True, exist_ok=True)
target = applications / 'cliptweak.desktop'
target.write_text(entry)
print(f'Installed {target}. Keep the checkout at {project}.')
PY
