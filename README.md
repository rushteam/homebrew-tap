# rushteam/homebrew-tap

Homebrew tap for RushTeam apps.

```sh
brew install --cask rushteam/tap/<name>
```

Homebrew adds the tap on first install. `brew upgrade` then covers every app installed from it.

## Casks

| Cask | App | Install |
| --- | --- | --- |
| `aiopt` | [AiOpt](https://github.com/rushteam/aiopt): route AI coding agent CLIs to the model providers you choose. macOS, Apple Silicon. | `brew install --cask rushteam/tap/aiopt` |

## Unsigned apps

Some apps here are not yet signed with an Apple Developer ID or notarized. Their casks clear the
macOS quarantine flag after installing so Gatekeeper does not refuse the app, and each such
cask says so in its caveats. Adding this tap means trusting every cask in it, so changes land
only through reviewed pull requests.

## Adding an app

- Put a cask in `Casks/<name>.rb` or a formula in `Formula/<name>.rb`. Names must be unique in the tap.
- Where the app's own repository keeps the source of its cask, change it there first and copy
  it here.
- Check before opening a pull request:

  ```sh
  brew style --cask Casks/<name>.rb
  brew audit --cask --strict --online rushteam/tap/<name>
  ```
