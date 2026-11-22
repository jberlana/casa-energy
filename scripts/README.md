# Project tools

`check_publication.py` validates reviewed files in the Git index, commit identities/messages and the
pending identity. It never prints matched secret values. Run it after deliberately staging the manifest
and intended changes. `--all-branches` includes local and origin branches, excluding archived PR refs.
Names must be GitHub aliases and emails must be GitHub noreply addresses. CI also runs Gitleaks against
branch history; checks do not rewrite published commits or purge GitHub caches.

`macos/` contains platform installation tools. Execute them **as modules from the repository root**:

```sh
python3 -m scripts.macos.install_service
python3 -m scripts.macos.install_environment_service
python3 -m scripts.macos.install_web_service
python3 -m scripts.macos.prepare_wake_install
```

These commands affect the local user's services; they are not ordinary development checks.
Configure and review the target installation first. The environment installer requires Hue pairing;
the web installer requires a built frontend. Preparing the wake installer only creates `.install/`;
executing the generated installer is a separate administrator action.

The Tado collector is copied as a standalone script to its existing `bin/` path. The environment
collector is also standalone. The web service copies the `backend` package and runs
`python -m backend.web_server` with its installed working directory. The refresh coordinator retains
those fixed collector paths, so a source-directory reorganization does not change runtime storage.

Keep these tools idempotent and bounded. Never add app behavior, credentials or sample measurements
to an installer. Test generated paths and manifests in temporary directories before real installation.

Guided ventilation experiments are a runtime CLI (`python -m backend.window_calibration`), not a
service installer. Follow [the operator protocol](../docs/ventilation.md); no calibration daemon is
installed and ordinary builds/tests never contact Tado.
