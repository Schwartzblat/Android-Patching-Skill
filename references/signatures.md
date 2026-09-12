# Writing signature finders

A finder locates an app-specific class or method at patch time and emits
`{{KEY}}` → value pairs. This is what makes a patcher generic: nothing
obfuscated is ever written down, so the same patcher keeps working when the app
updates and every name changes.

## Why this is not optional

Measured by running the same three WhatsApp finders against two real releases:

| artifact | 2.25.34.72 | 2.26.29.74 |
|---|---|---|
| `DECRYPT_PROTOBUF_CLASS_NAME` | `X.7WU` | `X.8Jt` |
| `DECRYPT_PROTOBUF_METHOD_NAME` | `A01` | `A01` |
| `DECRYPT_PROTOBUF_METHOD_SIG` | `([B)LX/7WU;` | `([B)LX/8Jt;` |
| `FMESSAGE_CLASS` | `X.1Hf` | `X.1G7` |

Every class name moved. A patcher that hard-coded `X.7WU` was dead the day the
update shipped; the finders tracked it without a change.

## The interface

```python
from stitch.artifactory_generator.SimpleArtifactoryFinder import SimpleArtifactoryFinder, CLASS_NAME_RE


class SubscriptionManager(SimpleArtifactoryFinder):
    IS_SUBSCRIBED_RE = re.compile(
        r'.method public final (?P<method_name>\w+)(?P<sig>\(\)Z)(?:(?!end method)[\s\S])*?booleanValue')

    def __init__(self, args):
        super().__init__(args)
        self.is_once = True
        self.is_found = False

    def class_filter(self, class_data: str) -> bool:
        return '"subscribed_skus"' in class_data

    def extract_artifacts(self, artifacts: dict, class_data: str) -> None:
        matches = list(self.IS_SUBSCRIBED_RE.finditer(class_data))
        if len(matches) != 1:
            return                       # ambiguous -> do not fire
        artifacts['SUBSCRIPTION_MANAGER_CLASS_NAME'] = \
            CLASS_NAME_RE.match(class_data).groupdict().get('name').replace('/', '.')
        artifacts['SUBSCRIPTION_MANAGER_METHOD_NAME'] = matches[0].groupdict().get('method_name')
        artifacts['SUBSCRIPTION_MANAGER_METHOD_SIG'] = matches[0].groupdict().get('sig')
        self.is_found = True
```

`generate_artifactory` reads each `.smali` file as one string and offers it to
every finder: `class_filter` first, then `extract_artifacts` on a hit. A finder
with `is_once = True` is dropped from the list once `is_found` is set, and the
whole scan stops early when the list empties — which is why a well-anchored
finder set costs well under a second even on 92,000 classes.

`args` is the parsed `argparse` namespace from `main.py`, so
`self.args.temp_path` and `self.args.apk_path` are available for finders that
need to read `resources.arsc`, `META-INF`, or the APK itself rather than smali.

## Two-part pattern

**1. `class_filter` — narrow by something R8 cannot rename.**

In order of preference:

| Anchor | Example | Why it survives |
|---|---|---|
| String literal | `'"subscribed_skus"' in class_data` | Wire-format keys, log messages and analytics tags are load-bearing; renaming them would break the server or the protocol. |
| Framework API reference | `'Landroid/content/pm/PackageManager;->getPackageInfo' in class_data` | Platform symbols cannot be renamed. |
| Field name of a framework/serialised type | `'viewOnce_'` | Protobuf-generated field names are fixed by the schema. |

Long, specific literals are best: `'"PromoEligibilityManager/refreshEligibility: promo eligibility disabled"'`
identifies exactly one class. Short or generic ones (`"error"`, `"ok"`) match
hundreds.

**2. `extract_artifacts` — pin the method by shape, not by name.**

The class name comes free from `CLASS_NAME_RE`:

```python
CLASS_NAME_RE = re.compile(r'\.class public.*L(?P<name>[\w/]+)')
```

Always `.replace('/', '.')` — smali writes `X/8Jt`, `Class.forName` wants
`X.8Jt`. Note it only matches `.class public`; a package-private target needs
your own pattern.

For the method, match on structure that reflects what the code *does*:

```python
# a no-arg boolean whose body unboxes a Boolean
r'.method public final (?P<method_name>\w+)(?P<sig>\(\)Z)(?:(?!end method)[\s\S])*?booleanValue'

# the only method taking byte[] in this class
r'\.method public (?:static )?(?P<method_name>\w+)(?P<sig>\(\[B\).*)'

# a (L...)Z whose body touches a ConcurrentHashMap within a few lines
r'\.method public ?(?P<method_name>\w+)(?P<sig>\(L.*Z)(?:\n[^\n]*){3,4}[^\n]ConcurrentHashMap'
```

`(?:(?!end method)[\s\S])*?` is the idiom for "still inside this method body" —
a lazy any-character run that cannot cross `.end method` into the next one.

## Rules

**Capture the signature, not just the name.** `{{..._METHOD_SIG}}` feeds
`ArtHooks.find_function(cls, name, sig)`, which is the only way to pick one
overload out of several. Capturing it costs one group.

**Ambiguity means do not fire.** `if len(matches) != 1: return` is the whole
safety model. Two matches means the app changed shape and your regex no longer
identifies one thing; guessing produces a hook on the wrong method, which is
worse than no hook. The unfired finder is then caught by the gate.

**Set `is_found = True` only on complete success**, after every artifact is
written. It is the signal the gate reads.

**One finder per concept**, emitting a `PREFIX_CLASS_NAME` / `PREFIX_METHOD_NAME`
/ `PREFIX_METHOD_SIG` group. Keys are `[A-Z0-9_]+` — that is what both
`patch_artifacts` and the gate's scanner recognise.

**Never anchor on an obfuscated name.** `com.moovit.app.subscription.o`,
`X.8Jt` and method `c` are all outputs, never inputs.

## Fragility, and how it shows up

Of the three WhatsApp finders above, `WhatsAppPlusFinder` fired on 2.26.29.74
and **not** on 2.25.34.72. Its regex requires a `ConcurrentHashMap` reference
3–4 lines into the method:

```python
r'\.method public ?(?P<method_name>\w+)(?P<sig>\(L.*Z)(?:\n[^\n]*){3,4}[^\n]ConcurrentHashMap'
```

A fixed line-distance to an implementation detail is exactly the kind of anchor
that breaks on recompilation. The class-level anchor (a long log string) was
fine; the method-level one was too tight. Prefer "somewhere in this method
body" over "N lines down".

This is what the gate is for. A fragile signature fails loudly at validation
instead of producing an APK that installs and does nothing.

## Non-smali finders

`class_filter` returning `True` unconditionally makes a finder run once on the
first class and do arbitrary work — the established way to pull an artifact
from somewhere other than smali:

- **`SignatureFinder`** reads `META-INF/*.DSA` from the extracted tree and
  emits the app's certificate as a hex string, for defeating signature checks.
- **`FirebaseParamsFinder`** reads `resources.arsc` via `ARSCParser` to recover
  the original `google_api_key`.
- **`DexCopier`** copies the original `classes.dex` into
  `assets/smali_generator/classes.dex.bin` so the patched app can read its own
  pre-patch bytecode.

These still set `is_found`, so the gate covers them too.

## Workflow

1. Find the target by reading decompiled Java (`jadx`), which is far easier
   than reading smali.
2. Open that class's `.smali` in `.patcher-work/<stem>/extracted/` and pick the
   anchors from what is actually there.
3. Write the finder.
4. Run `validate-artifactory.py`. Iterate on the regex against the persistent
   work directory — no re-extraction needed.
5. If a second version of the APK is available, extract it too and run the gate
   against both. A finder that fires on only one is not yet generic.
