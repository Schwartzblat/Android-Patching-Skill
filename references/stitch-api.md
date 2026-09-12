# stitch

Python library that injects a compiled Java module into an existing APK. Read
this when wiring `main.py` or debugging what the patcher did to the APK.

## Entry point

```python
from stitch import Stitch
from stitch.common import ExternalModule

with Stitch(
        apk_path='./input.apk',        # .apk, .xapk, .apkm, or a bundle zip
        output_apk='./output.apk',
        temp_path='./temp',            # MUST NOT EXIST -- see below
        artifactory_list=[MyFinder(args)],
        external_modules=[ExternalModule(Path('./smali_generator'),
                                         'com.smali_generator.InitProviderMyApp')],
        arch='arm64-v8a',
        google_api_key=None,
        should_sign=True,
        extra_artifacts={},
) as stitch:
    stitch.patch()
```

| Parameter | Notes |
|---|---|
| `temp_path` | `__init__` raises `Exception('[!] The temp path already exists')` if present, and `__exit__` deletes it. Never point it at anything you want to keep. |
| `arch` | Only this ABI's `libarthooks.so` is injected. Default `arm64-v8a`. |
| `artifactory_list` | `SimpleArtifactoryFinder` instances. See `signatures.md`. |
| `extra_artifacts` | `dict` merged over the finders' output. Hard-codes a `{{KEY}}` without writing a finder. |
| `should_sign` | False leaves `output_apk` unsigned. |

### `ExternalModule(module_path, invoke_line)`

Despite the field name, in stitch 1.1.x `invoke_line` is **the fully-qualified
provider class name**, and it is passed straight to `patch_manifest` as
`android:name` / `android:authorities`.

```python
ExternalModule(Path('./smali_generator'), 'com.smali_generator.InitProviderMyApp')   # correct
```

MakoPatcher still passes the older form,
`'invoke-static {}, Lcom/smali_generator/TheAmazingPatch;->on_load()V'`. Current
stitch will happily install that entire string as a provider name and produce a
manifest that does nothing. Do not copy that pattern.

## What `patch()` does, in order

1. **`extract_apk`** — apktool `d -q -r` into `<temp>/extracted`.
   For a bundle (any zip containing a `.apk`), the parts are unpacked to
   `<temp>/bundle` first, splits are copied to `<temp>/bundle_apks`, and the
   **largest** inner APK becomes `base.apk`, which is then extracted.
2. **`generate_artifactory`** — walks every `*.smali` under `<temp>/extracted`,
   offering each file to each finder. Returns `{KEY: value}`.
   `extra_artifacts` is merged on top.
3. **Per module: `prepare_smali`** — copies the module tree to
   `<temp>/smali_generator`, runs `patch_artifacts`, then
   `./gradlew assembleRelease`, and expects `<module>/smali_generator.apk` to
   exist afterwards (`SMALI_GENERATOR_OUTPUT_PATH`).
4. **`patch_manifest`** — one `<provider>` per module in the target's
   `AndroidManifest.xml`:
   `name` and `authorities` both the provider FQN, `exported=false`,
   `initOrder=2147483647`. Binary AXML via `pyaxml`, plain XML via lxml.
5. **`patch_google_api_key`** *(only if `google_api_key` is set)* — reads the
   original value out of `resources.arsc` with `ARSCParser` and replaces those
   bytes in place.
6. **`compile_apk`** — apktool `build`, after appending `so` to
   `doNotCompress` in `apktool.yml`. Retries once on failure.
7. **`inject_dex_and_libs`** — rewrites the output zip:
   the module's dex files are appended as `classes<N+1>.dex` (DEFLATED, where
   `N` is the target's highest existing index) and its `lib/<arch>/*` entries
   are added **ZIP_STORED**, uncompressed, because ART maps `.so` files
   directly out of the APK.
8. **`sign_apk`** — uber-apk-signer over the output and every split.
9. Bundles are repacked into a zip of signed APKs at `output_apk`.

## `patch_artifacts` — the substitution

```python
data = data.replace(f'{{{{{key}}}}}'.encode(), value.encode())
```

Byte-level, unconditional, over **every file** in the copied module tree —
Java, Gradle, XML, anything. A key with no placeholder does nothing; a
placeholder with no key stays in the source verbatim, compiles (it is just a
string literal), and fails at runtime. Run `validate-artifactory.py` before
every build.

## Signing

Driven entirely by environment variables, read at import time in
`apk_utils.py`:

| Variable | Effect |
|---|---|
| `KEYSTORE_PATH` | `--ks`. **Its presence also flips the expected output suffix.** |
| `KEY_ALIAS` | `--ksAlias` |
| `KEYSTORE_PASSWORD` | `--ksPass` |
| `KEY_PASSWORD` | `--ksKeyPass` |

```python
APK_SUFFIX = '-aligned-debugSigned.apk' if os.environ.get('KEYSTORE_PATH') is None else '-aligned-signed.apk'
```

Because that constant is computed at **import** time, setting `KEYSTORE_PATH`
after `stitch` is imported makes `sign_apk` look for the wrong filename and
fail with `FileNotFoundError`. Export it before launching Python. Note stitch
does not read `.env` itself; source it first
(`set -a; . ./.env; set +a`).

## Bundles and XAPKs

`is_bundle(path)` is true when the zip contains any `.apk` entry. For `.xapk`,
the largest inner APK is chosen as `main_apk_name`; otherwise it is `base.apk`.
`main_apk_name` is a module-level global mutated during extraction, so import
it at point of use (`from stitch.apk_utils import main_apk_name`) rather than
binding it early.

Output for a bundle input is a zip of signed splits. Install with:

```bash
unzip -o output.xapk -d out && adb install-multiple out/*.apk
```

## Dead ends

- `Stitch.prepare_artifactory()` and the `artifactory: Path` annotation are
  vestigial: `self.artifactory` is never assigned in `__init__` and `patch()`
  never calls the method. A `--artifactory` CLI flag does nothing.
- `patcher.add_static_call_to_on_load` / `patch_or_add_function` /
  `get_activities_with_entry_points` / `get_new_smali_folder` are the old
  smali-rewriting entry path, superseded by the provider. Unused by `patch()`.
