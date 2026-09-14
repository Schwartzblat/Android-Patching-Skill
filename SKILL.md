---
name: android_patching
description: Reverse engineer an APK to find a target function, then build a generic patcher project that hooks it - a Python driver using the stitch module plus an Android Gradle module using the ArtHooks library. Use when asked to patch, mod, or crack an Android app, hook or bypass a method in an APK (subscription/premium checks, ads, FLAG_SECURE, signature verification), write artifactory signature finders, build or debug a stitch/ArtHooks patcher, or make an existing patcher survive an app update.
---

# android_patching

Builds a **generic** patcher: one that keeps working after the app updates,
because every app-specific identifier is discovered at patch time by a regex
signature instead of hard-coded.

That is not a nicety. Running the same three WhatsApp finders against two real
releases, every class name had moved:

| artifact | 2.25.34.72 | 2.26.29.74 |
|---|---|---|
| `DECRYPT_PROTOBUF_CLASS_NAME` | `X.7WU` | `X.8Jt` |
| `FMESSAGE_CLASS` | `X.1Hf` | `X.1G7` |

Never write an obfuscated name into a patcher. Names are outputs, never inputs.

## The pipeline

```
APK ──apktool──> smali ──finders──> artifactory {KEY: value}
                                         │
                     smali_generator/ ───┤ {{KEY}} substituted
                     (Gradle + ArtHooks) │
                                         ▼
                              gradlew assembleRelease
                                         │
          dex + libarthooks.so + assets + the module's resource table
                            injected into the APK
          <provider> added to the manifest (initOrder=MAX), and the
                     module's own manifest merged in
                                         ▼
                                  signed output APK
```

At runtime the injected `ContentProvider` starts before the host app and calls
`load()` on each `Hook`, which uses ArtHooks to redirect the target methods.

## Stages

Work through these in order. Each has a gate; a stage that cannot pass its gate
stops the pipeline. Create a todo per stage.

| # | Stage | Gate |
|---|---|---|
| 0 | Preflight | `scripts/preflight.sh` exits 0 (warnings are fine) |
| 1 | Extract | `scripts/apk-extract.py` produced `extracted/` |
| 2 | Locate target | candidate is a concrete class + method + smali signature |
| 3 | Frida confirm *(optional)* | probe fires and returns what you expect |
| 4 | Scaffold | `scripts/new-patcher.py <Name> <outdir>` |
| 5 | Write finder | reads `references/signatures.md` first |
| 6 | Write hook | reads `references/hook-patterns.md` first |
| 7 | **Validate** | `scripts/validate-artifactory.py` exits 0 |
| 8 | Build & patch | signed output APK exists |

Read a `references/*.md` when the stage you are on calls for it. Do not
preload them all.

### 0. Preflight

```bash
scripts/preflight.sh                    # PYTHON=/path/to/venv/bin/python to override
```

Hard requirements: java 17+, Python 3.11+ with `stitch` importable, and an
Android SDK. Everything else warns rather than fails:

- **`asc`** — the preferred way to *read* the app in stage 2, with **`jadx`** as
  the fallback. Neither is needed when the user supplies the target, or when you
  are driving the jadx GUI / MCP tools.
- **`adb`, `frida`** — stage 3 only.
- **apktool** — never needed on PATH. stitch ships its own jar and the scripts
  use that one, so recon and the real patch cannot diverge on version.

### 1. Extract

```bash
scripts/apk-extract.py ./app.apk        # or .xapk / .apkm
```

Extracts to `./.patcher-work/<stem>/` via stitch's own `extract_apk`, so the
layout matches what a real patch produces. This directory persists, which is
what lets you iterate on a regex without re-running apktool. Stitch's own
`temp_path` cannot be used for this — it must not exist when `Stitch()` starts
and is deleted on exit.

Prints the package name and version. Note both; you need the package for
stage 3.

### 2. Locate the target

**If the user already named the target** — class, method, and ideally the
signature — take it and skip straight to confirming it in the smali (below).
Do not decompile to re-derive what you were handed. This is the common case
when they have already done the reversing in asc, jadx, Ghidra or Frida.

Otherwise, read decompiled Java rather than smali. Smali is the ground truth
the finder matches against, but Java is what you reason in.

Work backwards from a **string the app cannot rename**: a wire-format key, a
log message, an analytics tag, an error string shown in the UI. Then find the
method that gates the behaviour.

**Prefer `asc` (Droid ASC) over jadx.** It queries the APK as a read-only
database — any xref in ~0.2s, one class decompiled in 0.13s, no unpacking and no
index to build first. If the `reversing-apks-with-asc` skill is installed,
invoke it and let it drive this stage; it carries the methodology. Without it,
these five commands cover stage 2:

```bash
asc-manifest ./app.apk --info                                     # package, version
asc findrefs ./app.apk string "subscribed_skus" -o hits.txt >/dev/null 2>&1
ascq outline ./app.apk 'Lcom/example/Foo;'                        # signatures only
ascq method  ./app.apk 'Lcom/example/Foo;' isSubscribed           # one body
asc-classes  ./app.apk --extends 'Lcom/example/Gate;'             # implementations
```

`asc` needs its helpers on PATH, and a cache that does not follow your CWD:

```bash
export PATH="$HOME/.claude/skills/reversing-apks-with-asc/scripts:$PATH"
export ASCQ_CACHE="$PWD/.patcher-work/.asc-cache"      # must be absolute
```

**Every bare `asc` call takes `-o FILE >/dev/null 2>&1`**, then `wc -l FILE`
before you read it — `asc` writes to stdout *even when you pass `-o`*, and one
unredirected `getclass` on a large class is 120k tokens. The `ascq`,
`asc-classes` and `asc-manifest` helpers are bounded by construction, so print
those directly. A zero-result is usually an unescaped regex metacharacter
(`Foo\$Bar`) or the wrong case, not a missing symbol.

**Fallback, when `asc` is not on PATH** — jadx:

```bash
jadx -d ./.patcher-work/<stem>/jadx --no-res ./app.apk     # slow, once
grep -rl "subscribed_skus" ./.patcher-work/<stem>/jadx/sources/
```

If the `jadx-mcp-server` tools are live (the APK is open in the jadx GUI),
prefer them — `search_classes_by_keyword`, `get_method_by_name`,
`get_xrefs_to_method`, `get_smali_of_class` — instead of decompiling again.

**Either way, finish this stage by confirming the target in the smali** — it is
what the finder will match against, and a name that came from decompiled Java
or from memory can be stale or wrong:

```bash
# the class exists, and here is its .method line with the real signature
grep -rn --include=*.smali "subscribed_skus" ./.patcher-work/<stem>/extracted/
grep -n "\.method.*isSubscribed" ./.patcher-work/<stem>/extracted/smali*/com/example/Foo.smali
```

You need three things before stage 5: the class, the method name, and the
method's signature exactly as smali writes it (e.g. `()Z`, `([B)LX/8Jt;`).

### 3. Confirm with Frida (optional)

Only when the user asks and a device is attached. Fill
`templates/frida-probe.js.tmpl` (`{{TARGET_CLASS}}`, `{{TARGET_METHOD}}`,
`{{APP_PACKAGE}}`) and run:

```bash
frida -U -f <package> -l script.js
```

Answers what static analysis cannot: is this method actually called, what does
it really return, which overload runs. Cheap insurance before committing to a
hook and a signature. Skip silently if there is no device.

### 4. Scaffold

```bash
scripts/new-patcher.py Moovit ~/projects/MoovitPatcher --package com.tranzmate
```

`<Name>` becomes the provider class `com.smali_generator.InitProvider<Name>`.
stitch scopes the manifest authority to the target's package
(`<target package>.<provider FQN>`), so patchers for two different apps may
share a name — keep it per-project anyway, since it is what tells your classes
apart in a logcat shared with every other patched app on the device.

The fresh project builds and passes the gate as-is. It also carries
`StitchResources.java` and `res/layout/stitch_demo.xml`, inert until the patch
needs UI; delete them if it never does.

### 5. Write the finder

Read `references/signatures.md`. One finder per concept, in
`artifactory_generator/`, emitting `PREFIX_CLASS_NAME`, `PREFIX_METHOD_NAME`,
`PREFIX_METHOD_SIG`. Register it in `main.py`'s `artifactory_list`.

Anchor `class_filter` on a string literal or framework reference. Pin the
method by shape, never by name. `if len(matches) != 1: return` — ambiguity
means do not fire.

### 6. Write the hook

Read `references/hook-patterns.md`, and `references/arthooks-api.md` for the
replacement-shape rules. Add a `Hook` implementation under
`smali_generator/app/src/main/java/com/smali_generator/patches/` and register
it in `InitProvider<Name>.hooks`.

The rule that breaks everything: **a replacement must be `static`, and an
instance method's replacement takes a leading `Object thiz`.**

If the patch needs a screen rather than only a hook, read
`references/module-ui.md`. Declare the `<activity>` in the *module's*
`AndroidManifest.xml` — stitch merges it into the target's — and inflate the
layout through `StitchResources`, never with the target's own resources.

### 7. Validate — do not skip

```bash
scripts/validate-artifactory.py --project . --work ./.patcher-work/<stem>
```

`patch_artifacts` is an unconditional string replace. A finder that matches
nothing leaves the literal `{{FOO_CLASS_NAME}}` in the Java source, which
**compiles cleanly** and then fails at runtime inside the try/catch every hook
wraps its `load()` in. The result installs, launches, behaves normally, and
silently does not hook. Nothing else in the pipeline notices.

The gate fails on an unfired finder, a finder that raised, and a placeholder
with no key; it warns on an unused key and on a finder `main.py` never
registers.

If a second version of the APK is available, extract it and run the gate
against both. A finder that fires on only one is not yet generic.

### 8. Build and patch

```bash
cd smali_generator && ./gradlew assembleRelease && cd ..     # isolates Java errors
python main.py -p ./app.apk -o ./output.apk
```

Set `--arch` to the target device's ABI (`adb shell getprop ro.product.cpu.abi`).
The default is `arm64-v8a`, and the wrong one means `libarthooks.so` never
loads and every hook silently fails.

A bundle input yields a zip of signed splits:
`unzip -o output.xapk -d out && adb install-multiple out/*.apk`.

Stop here. Installing and runtime verification are the user's call; if they
ask, `adb logcat -s PATCH ArtHooks` is the filter.

## References

| File | Read when |
|---|---|
| `references/stitch-api.md` | wiring `main.py`, or debugging what the patcher did to the APK |
| `references/arthooks-api.md` | writing any replacement method (stage 6) |
| `references/signatures.md` | writing any finder (stage 5) |
| `references/hook-patterns.md` | writing any hook (stage 6); copy-paste finder/hook pair |
| `references/module-ui.md` | the patch adds a screen, or needs its own layouts, assets or resources |
| `references/troubleshooting.md` | anything fails, or the patch runs but does nothing |

## Rules

- **Never hard-code an obfuscated name.** `X.8Jt`, `com.moovit.app.subscription.o`
  and method `c` are outputs of finders, never inputs to them.
- **Never skip stage 7.** The failure it catches is invisible at every other
  stage.
- **`load()` must not throw.** It runs during app startup; an escaping
  exception takes the host app down. Wrap it and log under the `PATCH` tag.
- **Log on success as well as failure.** A hook that logs nothing is
  indistinguishable from one that never ran.
- **`--extra-artifacts K:V` is a debugging aid, not a shipping mechanism.** It
  hard-codes exactly what the finders exist to avoid.
- Patch only apps the user is authorised to modify.
