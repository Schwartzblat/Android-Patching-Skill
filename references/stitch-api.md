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
        merge_module_manifests=True,   # merge the module's AndroidManifest.xml
        inject_module_resources=True,  # ship the module's resources.arsc
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
| `merge_module_manifests` | Default True. False registers only the `<provider>` and ignores everything else in the module's manifest. |
| `inject_module_resources` | Default True. False skips `assets/stitch/<pkg>.apk`, so the module has no resources at runtime. |

### `ExternalModule(module_path, invoke_line)`

Despite the field name, `invoke_line` is **the fully-qualified provider class
name**. It becomes the provider's `android:name`; the `android:authorities` is
that name prefixed with the target's package (see `patch_manifest` below).

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
4. **`patch_manifest`** — two things to the target's `AndroidManifest.xml`.
   Binary AXML via `pyaxml`, plain XML via lxml.
   - One `<provider>` per module: `name` = the provider FQN, `exported=false`,
     `initOrder=2147483647`, and `authorities` = `<target package>.<provider FQN>`
     (e.g. `com.tranzmate.com.smali_generator.InitProviderMoovit`).
   - Then the module's own manifest is merged in — see *The module's manifest*
     below.
5. **`patch_google_api_key`** *(only if `google_api_key` is set)* — reads the
   original value out of `resources.arsc` with `ARSCParser` and replaces those
   bytes in place.
6. **`compile_apk`** — apktool `build`, after appending `so` to
   `doNotCompress` in `apktool.yml`. Retries once on failure.
7. **`inject_module_files`** (renamed from `inject_dex_and_libs`) —
   rewrites the output zip with four kinds of entry from the module APK:
   - **dex** appended as `classes<N+1>.dex` (DEFLATED, where `N` is the
     target's highest existing index);
   - **`lib/<arch>/*`** added **ZIP_STORED**, uncompressed, because ART maps
     `.so` files directly out of the APK;
   - **`assets/*`**, each keeping the compression AGP gave it. A path the
     target already has is replaced by the module's copy, and the replacement
     is printed;
   - **`assets/stitch/<module package>.apk`**, the module's resource table on
     its own — see *Module resources* below.
8. **`sign_apk`** — uber-apk-signer over the output and every split.
9. Bundles are repacked into a zip of signed APKs at `output_apk`.

## The module's manifest

`patch_manifest` merges the module's compiled `AndroidManifest.xml` into the
target's, so a component is declared once — in the Gradle module, where it has
to be declared anyway to compile — rather than twice.

| Carried over | Never touched |
|---|---|
| `<application>` children: `activity`, `activity-alias`, `service`, `receiver`, `provider` | `<application>` and `<manifest>` attributes |
| root: `uses-permission`, `uses-permission-sdk-23`, `uses-feature`, `queries` | `<uses-sdk>` — the target's min/target SDK stand |

Four things happen on the way across, and each is a failure you would otherwise
debug on a device:

- **`.Name` is expanded** against the module's package. Left relative it is a
  `ClassNotFoundException` in the target.
- **Attributes pointing at the module's resources are dropped**, with a warning.
  The target's manifest is read against the *target's* `resources.arsc`, so
  `android:label="@string/app_name"` would resolve to something unrelated.
  Framework references (`@android:style/...`) are kept. Set such attributes from
  code instead.
- **`tools:` attributes are stripped** — build-time only, and the target's
  manifest has no `tools` namespace.
- **A component already declared in the target is skipped**, matched on tag +
  `android:name`, or on a colliding provider `authorities`.

A component with an `<intent-filter>` and no explicit `android:exported`
**raises** rather than being guessed at — Android 12+ refuses to install it.
Declare `android:exported` in the module's manifest.

## Assets

Everything under the module's `src/main/assets/` lands in the patched APK, each
file keeping the compression AGP chose. Read them the usual way; the target's
package is what `getAssets()` belongs to, so no special handling is needed:

```java
InputStream in = context.getAssets().open("my_data/config.json");
```

A path the target already uses is **replaced** by the module's copy — useful
for swapping an asset of the app being patched, and printed so it is never
silent. Across modules, the last `ExternalModule` wins.

## Module resources

`res/` cannot be merged into the target's table: both are numbered from `0x7f`,
so the same id means different things in each. In a patched app the module's
`R.layout.x` (say `0x7f020000`) is whatever the target happens to have at that
id — `setContentView` inflates the wrong thing rather than failing.

So stitch ships the module's table as a **separate APK** at
`assets/stitch/<module package>.apk` (dex, libs, assets and signatures
stripped; `resources.arsc` left uncompressed so it can be mmap'd), and the
module loads it at runtime into a `Resources` of its own. Because that
`Resources` holds only the module's table, its generated `R` constants mean
exactly what they meant at compile time.

`StitchResources` in the scaffold does this. See `module-ui.md` for the whole
recipe, including the non-SDK-API caveat.

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
