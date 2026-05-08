# Release Process

This repository uses GitHub tags to create releases.

## Versioning

- Stable releases use tags like `v0.5.0`.
- Test releases use tags like `v0.5.0-beta.1`, `v0.5.0-beta.2`, and so on.
- Tags with a hyphen are automatically published as GitHub pre-releases.

The tag version must match `custom_components/vattenfall_incharge/manifest.json`.

## Beta Release

Use beta releases from the `dev` branch while testing:

```powershell
git checkout dev
```

Update the manifest version:

```json
"version": "0.5.0-beta.1"
```

Commit and push the change, then tag it:

```powershell
git add custom_components/vattenfall_incharge/manifest.json
git commit -m "Prepare v0.5.0-beta.1"
git push origin dev
git tag v0.5.0-beta.1
git push origin v0.5.0-beta.1
```

GitHub Actions will create a pre-release automatically.

## Stable Release

When the beta is good, merge to `main`, update the manifest to the stable version,
and tag it:

```powershell
git checkout main
git merge dev
```

Update the manifest version:

```json
"version": "0.5.0"
```

Commit and push the change, then tag it:

```powershell
git add custom_components/vattenfall_incharge/manifest.json
git commit -m "Prepare v0.5.0"
git push origin main
git tag v0.5.0
git push origin v0.5.0
```

GitHub Actions will create a stable release automatically.
