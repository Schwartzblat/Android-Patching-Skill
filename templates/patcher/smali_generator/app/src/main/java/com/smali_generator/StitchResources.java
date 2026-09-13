package com.smali_generator;

import android.content.Context;
import android.content.ContextWrapper;
import android.content.res.AssetManager;
import android.content.res.Resources;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Method;

/**
 * Gives the module access to its own resources inside a patched APK.
 *
 * <p>Stitch injects the module's resource table as an asset instead of merging it into the host's, because the
 * two tables assign the same 0x7f ids to different things. Loading it into a Resources of its own keeps the
 * module's R constants meaningful: they are resolved against the table they were generated from.
 */
public final class StitchResources {

    private static final String MODULE_PACKAGE = modulePackage();
    private static final String ASSET_PATH = "stitch/" + MODULE_PACKAGE + ".apk";

    private static volatile Resources moduleResources;

    private StitchResources() {
    }

    private static String modulePackage() {
        String name = StitchResources.class.getName();
        return name.substring(0, name.lastIndexOf('.'));
    }

    /** The module's own resources. */
    public static Resources get(Context context) {
        Resources resources = moduleResources;
        if (resources == null) {
            synchronized (StitchResources.class) {
                resources = moduleResources;
                if (resources == null) {
                    resources = load(context);
                    moduleResources = resources;
                }
            }
        }
        return resources;
    }

    /**
     * A context that resolves resources against the module's table, for inflating the module's layouts.
     */
    public static Context wrap(Context base) {
        final Resources resources = get(base);
        return new ContextWrapper(base) {
            @Override
            public Resources getResources() {
                return resources;
            }

            @Override
            public AssetManager getAssets() {
                return resources.getAssets();
            }
        };
    }

    private static Resources load(Context context) {
        try {
            File apk = extractResourceApk(context);
            AssetManager assets = AssetManager.class.newInstance();
            Method addAssetPath = AssetManager.class.getMethod("addAssetPath", String.class);
            Object cookie = addAssetPath.invoke(assets, apk.getAbsolutePath());
            if (!(cookie instanceof Integer) || (Integer) cookie == 0) {
                throw new IllegalStateException("addAssetPath rejected " + apk);
            }
            Resources host = context.getResources();
            return new Resources(assets, host.getDisplayMetrics(), host.getConfiguration());
        } catch (Exception e) {
            throw new IllegalStateException("Could not load the resources of " + MODULE_PACKAGE, e);
        }
    }

    private static File extractResourceApk(Context context) throws Exception {
        byte[] data;
        try (InputStream input = context.getAssets().open(ASSET_PATH)) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            for (int read = input.read(chunk); read != -1; read = input.read(chunk)) {
                buffer.write(chunk, 0, read);
            }
            data = buffer.toByteArray();
        }
        File apk = new File(context.getFilesDir(), MODULE_PACKAGE + ".apk");
        if (apk.length() != data.length) {
            try (OutputStream output = new FileOutputStream(apk)) {
                output.write(data);
            }
        }
        return apk;
    }
}
