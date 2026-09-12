package com.smali_generator;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.util.Log;

import androidx.annotation.NonNull;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Entry point of the patch.
 *
 * stitch adds this class to the target app's AndroidManifest as
 *   <provider android:name="com.smali_generator.InitProvider__NAME__"
 *             android:authorities="com.smali_generator.InitProvider__NAME__"
 *             android:exported="false"
 *             android:initOrder="2147483647"/>
 * so onCreate() runs before the host Application's onCreate.
 *
 * Two consequences of running that early:
 *   - The authority string is this class's FQN, and Android requires
 *     authorities to be unique per device. Never ship two patchers using the
 *     same provider class name -- the second install fails with
 *     INSTALL_FAILED_CONFLICTING_PROVIDER.
 *   - Classes from dex files the app has not loaded yet may not resolve.
 *     If Class.forName() throws here, hook lazily instead (see
 *     references/troubleshooting.md).
 */
@SuppressWarnings("unused")
public class InitProvider__NAME__ extends ContentProvider {

    private static final String TAG = "PATCH";

    static Hook[] hooks = {
            // new MyHook(),
    };

    static final AtomicBoolean is_loaded = new AtomicBoolean(false);

    @Override
    public boolean onCreate() {
        Log.i(TAG, "InitProvider__NAME__: onCreate called");
        on_load();
        return true;
    }

    public static void on_load() {
        if (is_loaded.getAndSet(true)) {
            return;
        }
        Log.i(TAG, "Patch loaded!");
        for (Hook hook : hooks) {
            try {
                hook.load();
            } catch (Throwable t) {
                // One bad hook must not stop the rest, and must not kill the app.
                Log.e(TAG, "Hook " + hook.getClass().getSimpleName() + " failed: " + t);
            }
        }
    }

    @Override public Cursor query(@NonNull Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public String getType(@NonNull Uri u) { return null; }
    @Override public Uri insert(@NonNull Uri u, ContentValues v) { return null; }
    @Override public int delete(@NonNull Uri u, String s, String[] a) { return 0; }
    @Override public int update(@NonNull Uri u, ContentValues v, String s, String[] a) { return 0; }
}
