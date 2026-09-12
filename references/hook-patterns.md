# Hook patterns

The Java side of a patch. See `arthooks-api.md` for the hooking API itself and
`signatures.md` for where the `{{PLACEHOLDER}}` values come from.

## Wiring

Every patch is a `Hook` registered in the generated provider:

```java
static Hook[] hooks = {
        new SubscriptionManager(),
};
```

`InitProvider<Name>.onCreate()` runs each `load()` once, guarded by an
`AtomicBoolean` and a per-hook try/catch. `load()` must not throw — it runs
during app startup, and an escaping exception takes the host app down.

## The worked pair

A finder and the hook that consumes it. Copy both, rename, adjust.

**`artifactory_generator/subscription_manager.py`**

```python
import re
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
            return
        artifacts['SUBSCRIPTION_MANAGER_CLASS_NAME'] = \
            CLASS_NAME_RE.match(class_data).groupdict().get('name').replace('/', '.')
        artifacts['SUBSCRIPTION_MANAGER_METHOD_NAME'] = matches[0].groupdict().get('method_name')
        artifacts['SUBSCRIPTION_MANAGER_METHOD_SIG'] = matches[0].groupdict().get('sig')
        self.is_found = True
```

**`patches/SubscriptionManager.java`**

```java
package com.smali_generator.patches;

import android.util.Log;
import com.arthooks.ArtHooks;
import com.smali_generator.Hook;
import java.lang.reflect.Executable;
import java.lang.reflect.Method;

public class SubscriptionManager implements Hook {

    // Replacement for an instance method: static, leading Object thiz.
    static boolean is_subscribed(Object thiz) {
        return true;
    }

    public void load() {
        try {
            Class<?> cls = Class.forName("{{SUBSCRIPTION_MANAGER_CLASS_NAME}}");
            Method replacement = SubscriptionManager.class
                    .getDeclaredMethod("is_subscribed", Object.class);
            Executable original = ArtHooks.find_function(
                    cls, "{{SUBSCRIPTION_MANAGER_METHOD_NAME}}", "{{SUBSCRIPTION_MANAGER_METHOD_SIG}}");
            if (original == null) {
                Log.e("PATCH", "SubscriptionManager: target not found");
                return;
            }
            ArtHooks.hook_function(original, replacement);
            Log.i("PATCH", "SubscriptionManager: hooked");
        } catch (Exception e) {
            Log.e("PATCH", "SubscriptionManager: " + e);
        }
    }

    public void unload() { }
}
```

Register the finder in `main.py`'s `artifactory_list` and the hook in
`InitProvider<Name>.hooks`, then run the gate.

## Shapes

**Force a return value** — two-argument `hook_function`, replacement ignores
everything:

```java
static boolean is_premium(Object thiz, Object feature) { return true; }
```

**Filter arguments, keep behaviour** — three-argument form with a backup:

```java
static void set_flags_backup(Window thiz, int flags, int mask) { }

static void set_flags(Window thiz, int flags, int mask) {
    if ((flags & LayoutParams.FLAG_SECURE) != 0) {
        flags ^= LayoutParams.FLAG_SECURE;
        mask  ^= LayoutParams.FLAG_SECURE;
    }
    set_flags_backup(thiz, flags, mask);
}
```

**Inspect and rewrite a return value** — call the backup, mutate, return:

```java
static PackageInfo get_package_info(PackageManager thiz, String pkg, int flags) {
    PackageInfo info = get_package_info_backup(thiz, pkg, flags);
    if (info != null && pkg.equals("com.whatsapp")) {
        info.signatures = new Signature[]{ new Signature("{{PACKAGE_SIGNATURE}}") };
    }
    return info;
}
```

**Static method** — no `thiz`:

```java
static Object decrypt_protobuf_hook(byte[] bArr) { ... }
```

**Framework method, no finder needed.** Platform classes are not obfuscated, so
hook them directly:

```java
Method original = Activity.class.getDeclaredMethod(
        "registerScreenCaptureCallback", Executor.class, Activity.ScreenCaptureCallback.class);
```

Guard by API level (`Build.VERSION.SDK_INT`) when the method only exists on
newer platforms — a missing method throws `NoSuchMethodException` and, without
a guard, aborts the rest of `load()`.

## Opt-in: `Wrapper`, for reading obfuscated fields

When a hook receives an object of an obfuscated class and needs its fields, a
`Wrapper` resolves the reflection once at startup instead of on every call.

```java
public interface Wrapper {
    static void init() { }
}
```

Register the class (not an instance) and invoke `init()` reflectively before
the hooks load:

```java
static Class<?>[] wrappers = { FMessage.class };

for (Class<?> wrapper : wrappers) {
    try { wrapper.getDeclaredMethod("init").invoke(null); }
    catch (Throwable t) { Log.e(TAG, "Wrapper " + wrapper.getSimpleName() + " failed: " + t); }
}
```

```java
public class FMessage implements Wrapper {
    public static Class<?> FMESSAGE_CLASS;
    private static Field device_jid;
    private final Object fmessage;

    public FMessage(Object fmessage) { this.fmessage = fmessage; }

    public static void init() {
        try {
            FMESSAGE_CLASS = Class.forName("{{FMESSAGE_CLASS}}");
            Class<?> jid = Class.forName("com.whatsapp.jid.DeviceJid");
            device_jid = ReflectionUtils.findFieldUsingFilter(
                    FMESSAGE_CLASS, f -> f.getType() == jid);
        } catch (Exception e) {
            Log.e("PATCH", "FMessage: init error: " + e.getMessage());
        }
    }

    public Object getDeviceJid() {
        try { return device_jid.get(this.fmessage); } catch (Exception e) { return null; }
    }
}
```

Finding a field *by its type* rather than its name is the point: the type is
often a non-obfuscated or separately-resolvable class even when the field name
is a single letter.

## Opt-in: `ReflectionUtils`

Predicate-based lookup helpers that walk the superclass chain —
`findMethodUsingFilter`, `findFieldUsingFilter`, `findAllFieldsUsingFilter`,
`getFieldByType`, `isCalledFromClass`, `callMethod`. Copy
`utils/ReflectionUtils.java` from WhatsAppPatcher when a target needs
structural lookup that a signature cannot express. Skip it otherwise; it is
~300 lines.

## Logging

Everything logs under the `PATCH` tag, ArtHooks itself under `ArtHooks`:

```bash
adb logcat -s PATCH ArtHooks
```

Log on both success and failure in `load()`. A hook that logs nothing is
indistinguishable from a hook that never ran.
