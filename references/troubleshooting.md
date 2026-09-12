# Troubleshooting

Ordered by how often it bites and how quietly it fails.

## The APK installs and runs, but nothing is hooked

The default failure. Nothing crashes, so start by proving the patch loaded at
all:

```bash
adb logcat -c && adb shell am force-stop <pkg> && adb shell monkey -p <pkg> 1 >/dev/null
adb logcat -s PATCH ArtHooks
```

Read the log top-down:

| What you see | Meaning |
|---|---|
| nothing at all | The provider never ran. See *provider not firing* below. |
| `InitProvider…: onCreate called` then nothing | Provider ran, no hook registered in `hooks`. |
| `ClassNotFoundException: {{FOO_CLASS_NAME}}` | **A placeholder was never substituted.** The literal braces in the message are the tell. Run the gate. |
| `ClassNotFoundException: X.8Jt` | Real name, class not loadable yet. See *too early* below. |
| `NoSuchMethodException` | Finder found the class but the method name/sig is stale, or you used `getDeclaredMethod` on an inherited method. |
| ArtHooks logs a native failure | See *wrong ABI* below. |
| `hooked` logged but behaviour unchanged | Right method, wrong one for the job — or it was inlined. |

### A `{{PLACEHOLDER}}` survived into the build

`patch_artifacts` is an unconditional string replace, so an unmatched key
leaves the literal `{{FOO}}` in the source. It compiles — it is only a string —
and fails at runtime inside the hook's own try/catch.

```bash
python scripts/validate-artifactory.py --project . --work ./.patcher-work/<stem>
```

To confirm after the fact, look inside **the injected dex only** -- the
highest-numbered one, since stitch appends it as `classes<N+1>.dex`:

```bash
DEX=$(unzip -l output.apk | grep -oE 'classes[0-9]*\.dex' | sort -V | tail -1)
unzip -p output.apk "$DEX" | strings | grep '{{'
```

Do not scan every dex. Host apps routinely contain `{{` by coincidence --
compressed data and random byte runs that `strings` picks up -- so a whole-APK
grep reports false leaks. Measured on a real target: the app's own dex files
held a dozen `{{` hits while the injected dex held none.

### Wrong ABI

Stitch injects only `lib/<arch>/libarthooks.so` for the single `arch` it was
given, default `arm64-v8a`. On a mismatched device `System.loadLibrary` throws
in the `ArtHooks` static initialiser, `is_available()` is false, and every
`hook_function` returns false.

```bash
adb shell getprop ro.product.cpu.abi     # arm64-v8a | x86_64 | ...
python main.py -p in.apk --arch x86_64
unzip -l output.apk | grep arthooks      # verify what actually got in
```

### Too early: the target class is not loaded yet

The provider is injected with `initOrder=2147483647`, so it runs before the
host `Application.onCreate` and before most of the app's dex files are touched.
A `Class.forName` on a class in a not-yet-loaded dex throws.

Hook lazily instead — find something that *is* loaded, hook that, and install
the real hook the first time it runs:

```java
public void load() throws Exception {
    Method attach = Application.class.getDeclaredMethod("attach", Context.class);
    ArtHooks.hook_function(attach,
            MyHook.class.getDeclaredMethod("attach_hook", Object.class, Context.class),
            MyHook.class.getDeclaredMethod("attach_backup", Object.class, Context.class));
}

static void attach_backup(Object thiz, Context c) { }

static void attach_hook(Object thiz, Context c) {
    attach_backup(thiz, c);
    install_real_hooks();     // app classloader is ready by now
}
```

### The method was inlined

A small, hot method the JIT already inlined keeps running its original body at
existing call sites. Hook its caller, or a larger method on the same path.

## Provider not firing

- **Check it landed.** `apktool d -f -s output.apk -o /tmp/chk && grep -i provider /tmp/chk/AndroidManifest.xml`
- **Authority collision.** `android:authorities` is set to the provider FQN, and
  authorities must be unique per device. If another patched app already
  registered `com.smali_generator.InitProviderX`, install fails with
  `INSTALL_FAILED_CONFLICTING_PROVIDER`. Rescaffold with a distinct name, or
  uninstall the other app.
- **Dex not injected.** `unzip -l output.apk | grep classes` — the module's dex
  should be the highest-numbered `classes<N>.dex`.

## Build and packaging failures

**apktool build fails.** Stitch retries once, then raises. Usually a resource
the version of apktool cannot rebuild. Try `--no-sign` to isolate signing, and
build the extracted dir by hand to see the real error:

```bash
java -jar "$(python -c 'from stitch.common import APKTOOL_PATH; print(APKTOOL_PATH)')" \
     build ./temp/extracted --output /tmp/out.apk
```

**`FileNotFoundError` ending in `-aligned-signed.apk`.** `APK_SUFFIX` is
computed at *import* time from `KEYSTORE_PATH`. Setting the variable after
importing stitch makes uber-apk-signer write one filename while stitch looks
for the other. Export it before starting Python.

**`Exception: [!] The temp path already exists`.** `Stitch.__init__` refuses a
pre-existing `temp_path`. A previous crash left it behind: `rm -rf ./temp`.
Never point `temp_path` at your work directory.

**`UnsatisfiedLinkError` at runtime after a successful build.** The `.so` was
compressed. Stitch appends `so` to `doNotCompress` in `apktool.yml` and injects
libs ZIP_STORED; if you repacked the APK yourself, preserve that.

**Lint crashes the standalone build: `Incorrect type '{{FOO_CLASS_NAME}}' (JDK_23)`.**
AGP runs `lintVitalAnalyzeRelease` as part of `assembleRelease`, and
`PrivateApiDetector` tries to resolve the string in
`Class.forName("{{FOO_CLASS_NAME}}").getDeclaredMethod(...)` as a Java type.
Before stitch substitutes the artifactory that string is still `{{...}}`, which
is not a valid type name, and lint aborts the build. It only happens when you
build the module by hand -- a real patch substitutes first. The template
therefore ships:

```gradle
lint {
    checkReleaseBuilds false
    abortOnError false
}
```

Keep it. Linting a tree full of placeholders is meaningless, and without it
`./gradlew assembleRelease` cannot be used to isolate Java errors.

**Gradle cannot resolve ArtHooks.** `settings.gradle` needs
`maven { url 'https://jitpack.io' }` inside `dependencyResolutionManagement`.
Declaring that block twice makes the last one win and silently drops the
repositories from the first.

**Gradle cannot find the SDK.** Write `sdk.dir=/path/to/Android/Sdk` into
`smali_generator/local.properties`, or export `ANDROID_HOME`.

## Bundles

A `.xapk`/`.apkm` produces a zip of signed splits, not a single APK:

```bash
unzip -o output.xapk -d out && adb install-multiple out/*.apk
```

`INSTALL_FAILED_MISSING_SPLIT` means you installed only the base. Install all
of them.

## Signature and integrity checks

Repackaging changes the signing certificate, so an app that checks its own
signature will notice. `SignatureFinder` extracts the original certificate
from `META-INF/*.DSA` and a `PackageManagerHook` feeds it back to
`getPackageInfo` callers — see WhatsAppPatcher. Play Integrity attestation is
server-side and not defeated this way.

## Iterating quickly

- Keep `.patcher-work/<stem>/` and re-run only the gate while tuning a regex;
  extraction is the slow part.
- `--extra-artifacts KEY:value` hard-codes one value to unblock a build while a
  finder is still being written. Remove it before shipping — it is exactly the
  hard-coding the finders exist to avoid.
- Build the module alone (`cd smali_generator && ./gradlew assembleRelease`) to
  separate Java compile errors from patching errors.
