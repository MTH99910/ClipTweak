#!/usr/bin/env bash
# Install only a desktop entry pointing to this checkout; do not copy dependencies.
set -euo pipefail
project_dir="$(dirname -- "$(dirname -- "$(readlink -f -- "$0")")")"
python - "$project_dir" <<'PY'
from pathlib import Path
import os, sys
project = Path(sys.argv[1])
launcher = str(project / 'cliptweak-launcher')
# Desktop Entry Exec quoting is distinct from shell quoting.
escaped = launcher.replace('\\', '\\\\\\\\').replace('"', '\\\\"').replace('`', '\\\\`').replace('$', '\\\\$').replace('%', '%%')
entry = (project / 'cliptweak.desktop').read_text()
entry = entry.replace('Exec=cliptweak %f', 'Exec="' + escaped + '" %f')
applications = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share'))) / 'applications'
applications.mkdir(parents=True, exist_ok=True)
target = applications / 'cliptweak.desktop'
target.write_text(entry)
print(f'Installed {target}. Keep the checkout at {project}.')
PY
