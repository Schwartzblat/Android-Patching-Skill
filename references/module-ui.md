# Adding a screen to a patched app

Read this when the patch needs UI of its own — a settings screen, a toggle, a
debug panel — inside the app being patched.

Three pieces, all in the Gradle module:

1. a layout under `res/layout/`,
2. an `Activity` that inflates it through `StitchResources`,
3. an `<activity>` in the module's `AndroidManifest.xml`.

stitch merges (3) into the target's manifest and ships (1) as a separate
resource table; `StitchResources` (already in the scaffold) loads that table at
runtime. See `stitch-api.md` for why the table cannot simply be merged.

## The activity

`R` here is the **module's** `R`. Its ids are meaningless against the target's
resource table, which is exactly why the inflater must be given a context whose
resources are the module's:

```java
package com.smali_generator;

import android.app.Activity;
import android.content.Context;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.widget.Button;

public class SettingsActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        Context moduleContext = StitchResources.wrap(this);
        View root = LayoutInflater.from(this).cloneInContext(moduleContext)
                .inflate(R.layout.stitch_demo, null);
        setContentView(root);

        Button button = root.findViewById(R.id.stitch_demo_button);
        button.setOnClickListener(v -> { /* ... */ });
    }
}
```

`findViewById` works on the inflated tree because the ids baked into the view
hierarchy are the module's own. `StitchResources.get(context)` returns the
`Resources` alone if you need to look something up directly.

## The manifest entry

In `smali_generator/app/src/main/AndroidManifest.xml`:

```xml
<application tools:ignore="MissingApplicationIcon">
    <activity
        android:name=".SettingsActivity"
        android:exported="true" />
</application>
```

`android:exported="true"` makes it reachable with `am start` for testing. Add
an `<intent-filter>` only if the app itself must route to it — and then
`android:exported` is mandatory, not optional: stitch raises without it.

Do **not** give it `android:label` or `android:icon` pointing at the module's
resources. Those attributes are read against the target's table; stitch drops
them with a warning. Set the title from code.

## Constraints

| Constraint | Why |
|---|---|
| No `?attr/...` in the layout | Theme attributes resolve against the activity's theme, which belongs to the target app. Use explicit values or `@android:` ones. |
| `minifyEnabled false` | R8 would rename the activity the merged manifest points at. |
| Non-SDK API | `StitchResources` builds an `AssetManager` by reflection — there is no public way to construct one. `addAssetPath` is on Android's *unsupported* list, not the blocked one, and works as far as Android 16. If it is ever blocked, the replacement is `ResourcesLoader` (public since API 30) with the module built under a reserved package id. |

## Verify without a device

```bash
python - <<'EOF'
import io, zipfile
from androguard.core.apk import APK
from androguard.core.axml import ARSCParser
from androguard.util import set_log; set_log('CRITICAL')

out = 'output.apk'
print('activity registered:', 'com.smali_generator.SettingsActivity' in APK(out).get_activities())
inner = zipfile.ZipFile(out).read('assets/stitch/com.smali_generator.apk')
arsc = ARSCParser(zipfile.ZipFile(io.BytesIO(inner)).read('resources.arsc'))
print('layout in the module table:', arsc.get_id('com.smali_generator', 0x7f020000))
EOF
```

The second line is the one that matters: it proves the id the dex was compiled
against resolves to your layout *in the table that ships with it*. Compare it
against the same id in the target (`APK(out).get_android_resources().get_id(...)`)
and you will see two unrelated resources — that is the whole reason this
machinery exists.

## On a device

```bash
adb install -r output.apk
adb shell am start -n <target.package>/com.smali_generator.SettingsActivity
adb logcat -s PATCH ArtHooks
```

`INSTALL_FAILED_CONFLICTING_PROVIDER` here means another patched app on the
device already claims this authority — see `troubleshooting.md`.
