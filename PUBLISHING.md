# Local repository and GitHub publication

The application and project files are complete locally. GitHub publication was
not possible in the build session: no GitHub CLI login or environment token was
available, and a noninteractive lookup through the configured Git credential
helper returned no GitHub credential. No public repository has been created.

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
gh auth login
gh repo create video-tint --public --source=. --remote=origin --push
```

If `.git` remains protected outside the managed environment, first copy the
project into an ordinary writable folder, preserving `.video-tint-git` but
excluding the empty `.git` placeholder, then rename `.video-tint-git` to `.git`
there. Its `core.worktree` is relative (`..`), so the repository can move safely.
Do not include footage, cache files, corrected results, credentials, or `.venv`.

`LICENSE` covers application code; third-party dependencies have separate
licenses documented in `README.md`. No dependency binaries or credentials are
stored in this repository.
