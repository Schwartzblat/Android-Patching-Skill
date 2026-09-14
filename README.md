# android_patching

A [Claude Code](https://claude.com/claude-code) skill for patching Android apps.
Point it at an APK and a thing you want changed; it finds the responsible
function and builds you a patcher project that hooks it, using
[stitch](https://pypi.org/project/stitch/) and
[ArtHooks](https://github.com/Schwartzblat/ArtHooks).

The patchers it writes are **generic**: no obfuscated name is ever written down.
Classes and methods are located at patch time by regex signatures, so the
patcher keeps working when the app updates. Running the same three finders
against two real WhatsApp releases:

| artifact | 2.25.34.72 | 2.26.29.74 |
|---|---|---|
| `DECRYPT_PROTOBUF_CLASS_NAME` | `X.7WU` | `X.8Jt` |
| `FMESSAGE_CLASS` | `X.1Hf` | `X.1G7` |

Every name moved. The finders tracked them without a change.

## Install

```bash
git clone <this repo> ~/.claude/skills/android_patching
~/.claude/skills/android_patching/scripts/preflight.sh
```

Required: JDK 17+, Python 3.11+ with `pip install stitch`, and an Android SDK.
`asc` (Droid ASC) is recommended for reading the app, with `jadx` as the
fallback; `adb` and `frida` are optional (confirming a target on a live
device). apktool is not needed — stitch ships its own.

## Use

Just ask, in Claude Code:

> patch this APK so the subscription check always returns true — ./app.apk

Or invoke it directly with `/android_patching`. It runs a nine-stage pipeline —
extract, locate, scaffold, write a signature finder, write the hook, validate,
build — and stops at a signed APK.

If you already know the target, say so and it skips the reversing:

> hook com.example.Billing.isPro()Z in ./app.apk to return true

## What you get

```
YourPatcher/
├── main.py                  wires finders + hook module into stitch
├── artifactory_generator/   signature finders -> {{KEY}} values
└── smali_generator/         Gradle module with the hooks, built and injected
```

```bash
python main.py -p ./app.apk -o ./patched.apk --arch arm64-v8a
```

## The gate

`scripts/validate-artifactory.py` is the part worth knowing about. stitch
substitutes `{{KEY}}` placeholders with a plain string replace, so a finder that
matches nothing leaves `{{FOO_CLASS_NAME}}` in the Java source — which compiles
fine, since it is only a string, then fails at runtime inside the hook's own
`catch`. You get an APK that installs, launches, behaves normally and silently
does not hook.

The gate catches that before you build. Run it every time.

## Docs

`SKILL.md` is the pipeline. Deeper material in `references/`: the stitch API,
the ArtHooks API and its replacement-shape rules, how to write signatures that
survive an update, hook patterns, and a troubleshooting catalogue.

## Disclaimer

For educational purposes. Patch only apps you are authorised to modify.
