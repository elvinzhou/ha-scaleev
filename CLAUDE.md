# Working on ha-scaleev

- **Always open a pull request**; never push to `main`.
- **PR titles must be conventional commits** (`fix: ...`, `feat: ...`, `feat!: ...` for breaking changes; `docs:`, `test:`, `ci:`, `chore:` etc. don't release). Merge with **squash**, so the title becomes the commit on `main`. release-please reads those titles to choose the next version.
- Don't edit `version` in `manifest.json` or `CHANGELOG.md`; release-please does that in its release PR.
- Run `pytest` before opening a PR (see README).
