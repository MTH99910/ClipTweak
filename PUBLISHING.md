# Developing and publishing ClipTweak

The GitHub checkout is `/home/mth/Projects/ClipTweak`, with origin
`https://github.com/MTH99910/ClipTweak.git`. Use ordinary Git commands there:

```sh
git status
git pull --ff-only
git push origin main
```

The application checkout can be moved from `Applications/Video adjuster` to
`Applications/ClipTweak`. Close the app before moving it, and then run
`./scripts/install-desktop.sh` from its new location. This updates the KDE
launcher path and installs the bundled PNG icon. Package imports and resource
paths are relative to the checkout, so the folder name is not hard-coded.

This managed build environment exposes a read-only `.git` placeholder in the
application checkout. Its local Git metadata is therefore stored in
`.video-tint-git`, with a relative working-tree path (`..`). A verified
`cliptweak.bundle` carries the complete history for importing commits into a
normal checkout. Both metadata and bundles are excluded from version control.
The obsolete Video Tint shell launcher is removed, rather than restored.

## GitHub connectivity diagnosis

GitHub authentication and command-network access are separate. This session's
sandbox blocks command networking: DNS lookups for both `github.com` and
`api.github.com` fail with “Temporary failure in name resolution,” and even an
unauthenticated API request fails. Consequently, `gh auth status` reporting an
invalid token here does not prove the user's token is invalid. The CLI also
cannot retrieve an OAuth token here, and connecting to the desktop session bus
is denied (`Operation not permitted`). This is consistent with the KDE credential
store being inaccessible inside the sandbox; it is an additional blocker. Confirm access
from a normal desktop terminal with `gh api user --jq .login`.

The current writable roots are the application checkout and `/tmp`. This does
not permit editing the Projects checkout, renaming the application directory
in its parent, or updating the desktop entry in the user's home directory.
For a new local Codex CLI session that can perform those operations:

```sh
codex -C "/home/mth/Applications/Video adjuster" \
  --sandbox workspace-write --ask-for-approval on-request \
  --add-dir "/home/mth/Applications" \
  --add-dir "/home/mth/Projects/ClipTweak" \
  --add-dir "/home/mth/.local/share/applications" \
  --add-dir "/home/mth/.local/share/icons" \
  -c sandbox_workspace_write.network_access=true
```

After the rename, use `/home/mth/Applications/ClipTweak` for `-C`. A managed
Codex app/session may enforce its own policy; enable command-network access and
the required writable directories there too. These settings apply to a new
session, not to an already-running restricted one. Protected Git metadata may
require an execution approval with the `on-request` policy.

See [official OpenAI configuration documentation](https://learn.chatgpt.com/docs/config-file/config-reference)
for `sandbox_workspace_write.network_access` and writable roots. No GitHub token
belongs in the project or in a chat message.
