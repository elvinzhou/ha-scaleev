# Working on ha-scaleev

- **Always open a pull request**; never push to `main`.
- **PR titles must be conventional commits** (`fix: ...`, `feat: ...`, `feat!: ...` for breaking changes; `docs:`, `test:`, `ci:`, `chore:` etc. don't release). Merge with **squash**, so the title becomes the commit on `main`. the release workflow reads those commits to choose the next version.
- Don't edit `version` in `manifest.json` (it stays `0.0.0`); the release workflow patches the real version into the release zip. There is no version-bump PR.
- Run `pytest` before opening a PR (see README).
