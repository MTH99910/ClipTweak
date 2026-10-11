# Local repository and GitHub publication

ClipTweak is complete and committed locally. GitHub CLI is installed and the
user has logged in, but this managed workspace cannot connect to
`api.github.com`. This prevents publication here; it does not establish that
the user's credentials are invalid. Publish from a normal desktop terminal
with the commands below. No public repository has been created by this session.

This managed workspace exposes an empty, read-only `.git` placeholder. A normal
`git init` fails with “Read-only file system.” The local repository is therefore
initialized on branch `main` in `.video-tint-git`, with the application folder as
its working tree. Its initial commit contains only project source and documentation.
You can inspect it while still inside that environment with:

```sh
cd "/home/mth/Applications/Video adjuster"
git --git-dir=.video-tint-git --work-tree=. status
git --git-dir=.video-tint-git --work-tree=. log --oneline
```

In a normal terminal on your PC, restore conventional Git metadata and publish:

```sh
cd "/home/mth/Applications/Video adjuster"
# Remove only the empty placeholder. Do not remove an existing real repository.
if [ -d .video-tint-git ] && [ ! -f .git/HEAD ]; then
    rmdir .git && mv .video-tint-git .git
fi
git status
sudo pacman -S --needed github-cli
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status --hostname github.com
gh repo create ClipTweak --public --source=. --remote=origin --push
```

If `.git` remains protected outside the managed environment, first copy the
project into an ordinary writable folder, preserving `.video-tint-git` but
excluding the empty `.git` placeholder, then rename `.video-tint-git` to `.git`
there. Its `core.worktree` is relative (`..`), so the repository can move safely.
Do not include footage, cache files, corrected results, credentials, or `.venv`.

`LICENSE` covers application code; third-party dependencies have separate
licenses documented in `README.md`. No dependency binaries or credentials are
stored in this repository.

A portable `cliptweak.bundle` is also supplied locally (and excluded from Git).
If you prefer a fresh standard checkout instead of moving the metadata:

```sh
mkdir -p "$HOME/Projects"
git clone "/home/mth/Applications/Video adjuster/cliptweak.bundle" "$HOME/Projects/ClipTweak"
cd "$HOME/Projects/ClipTweak"
git remote remove origin
sudo pacman -S --needed github-cli
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status --hostname github.com
gh repo create ClipTweak --public --source=. --remote=origin --push
```
