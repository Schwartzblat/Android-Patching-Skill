# __NAME__Patcher

Patches `__PACKAGE__` by injecting a hook module built with
[stitch](https://github.com/Schwartzblat/Stitch) and
[ArtHooks](https://github.com/Schwartzblat/ArtHooks).

Every app-specific identifier is discovered at patch time by a regex signature
in `artifactory_generator/`, so the patcher keeps working across app updates
instead of pinning obfuscated names that change on every release.

## Requirements

- Python 3.11+
- JDK 17+
- Android SDK (`gradlew` needs `sdk.dir` in `smali_generator/local.properties`,
  or `$ANDROID_HOME`)

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python main.py -p ./input.apk -o ./output.apk
```

For a bundle (`.xapk` / `.apkm`) the output is a zip of signed split APKs:

```bash
python main.py -p ./input.xapk -o ./output.xapk
unzip -o output.xapk -d out && adb install-multiple out/*.apk
```

Useful flags:

| Flag | Meaning |
|---|---|
| `--arch` | ABI whose `libarthooks.so` is injected. Default `arm64-v8a` — set `x86_64` for an emulator, or hooks will not load. |
| `--no-sign` | Leave the output unsigned. |
| `--extra-artifacts K:V` | Inject `{{K}}` → `V` without writing a finder. |
| `-g/--google-api-key` | Replace the app's `google_api_key` resource. |

Signing uses `uber-apk-signer`'s debug key unless you export a keystore — see
`.env.example`.

## Layout

| Path | Role |
|---|---|
| `main.py` | Wires finders + the hook module into `Stitch(...)` and runs `patch()`. |
| `artifactory_generator/` | Signature finders. Each produces `{{KEY}}` → value pairs. |
| `smali_generator/` | Android Gradle module holding the hooks. Built to `smali_generator.apk`; its dex and jni libs are injected into the target. |

`{{PLACEHOLDER}}` strings anywhere under `smali_generator/` are substituted from
the artifactory before the module is compiled.

## Adding a patch

1. Write a finder in `artifactory_generator/` that locates the target class and
   method by a stable anchor and emits `{{...}}` keys.
2. Register it in `main.py`'s `artifactory_list`.
3. Write a `Hook` implementation in
   `smali_generator/app/src/main/java/com/smali_generator/patches/` that reads
   those placeholders.
4. Register it in `InitProvider__NAME__.hooks`.
5. Validate before building — a finder that matches nothing leaves `{{KEY}}` in
   the compiled source and the hook fails silently at runtime.

## Debugging

```bash
adb logcat -s PATCH ArtHooks
```
