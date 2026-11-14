<!-- Voice2Doc is a personal project: replies may take a while, and a pull request may not be merged depending on the project's direction and maintenance. For larger changes, please open an issue first. See CONTRIBUTING.md.
個人開発のため、お返事に時間がかかったり、方針・保守の都合で取り込めなかったりします。大きな変更は、先に Issue でご相談ください。 -->

## What and why / 変更内容と理由

<!-- What does this change and why? Link related issues (e.g. Fixes #123). -->

## Checklist

- [ ] `python -m pytest src_backends/ -v` passes
- [ ] `python scripts/ruff_baseline.py` passes (no new `ruff` findings)
- [ ] Tests added/updated (fixtures and fakes only; no real settings, history, `~/Downloads` or model downloads)
- [ ] No models, recordings, `config.json` / `history.json` or `.env` committed
- [ ] `spec.md` and `CHANGELOG.md` (`[Unreleased]`) updated if behavior changed; README updated if user-visible
