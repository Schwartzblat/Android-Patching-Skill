# ArtHooks

Java method hooking by overwriting the target `art::ArtMethod`'s entry point.
Gradle coordinate `com.github.Schwartzblat:ArtHooks` (jitpack), package
`com.arthooks`.

Verified against **1.1.0**: the API comes from `javap` on the AAR, and the
behaviour from the diagnostic strings in `libarthooks.so` and from a real patch
running on API 37. When the library is bumped, re-check with
`javap -cp classes.jar com.arthooks.ArtHooks` and
`unzip -p ArtHooks-*.aar jni/arm64-v8a/libarthooks.so | strings -n 30`. The
native error text states its rules more precisely than any doc.

## API

```java
public static boolean    is_available();
public static Executable find_function(Class<?> owner, String name, String signature);
public static boolean    hook_function(Executable original, Executable replacement);
public static boolean    hook_function(Executable original, Executable replacement, Executable backup);
public static boolean    is_hooked(Executable original);
public static boolean    unhook_function(Executable original);   // restores the original entry point
public static boolean    is_aot_disabled();                      // see "AOT code and inlining"
public static boolean    disable_aot();                          // run by the static initialiser
public static final String KEEP_AOT_PROPERTY;                    // "arthooks.keep_aot"
```

Targets are `Executable`, so `Method` and `Constructor` both work. Nothing
throws — failures return `false`/`null` and log under the **`ArtHooks`** tag.
Always check `is_available()` once at startup; when the native library did not
load, every hook silently no-ops.

## The replacement shape — the rule that breaks everything

**A replacement must be `static`, and it takes the receiver as an explicit
leading `Object thiz` parameter.**

The hook redirects the call without touching arguments already in place, so a
static `(Object thiz, ...)` is exactly what an instance method's argument
layout looks like from the callee's side. Get this wrong and you corrupt the
frame.

| Target | Replacement signature |
|---|---|
| instance `boolean isPro()` | `static boolean hook(Object thiz)` |
| instance `void setFlags(int f, int m)` on `Window` | `static void hook(Window thiz, int f, int m)` |
| static `byte[] decode(byte[] b)` | `static byte[] hook(byte[] b)` — no `thiz` |
| constructor `Foo(String s)` | `static void hook(Object thiz, String s)` — returns `void` |

The leading parameter may be typed (`Window thiz`) or just `Object thiz`.

For a constructor, the object is already allocated when `<init>` is entered, so
**nothing initialises it unless your replacement calls the backup.** A
constructor hook that does not call through leaves a blank object.

## Backups

The three-argument form points `backup` at the original's pre-hook body.

**A backup must be declared `static native`, with no body.** A backup with a
Java body (even an empty `{ }` or `return null;`) is small enough for dex2oat
to inline into the replacement. The call to the backup then never goes through
its entry point, so the swap is invisible: the backup silently returns its own
dummy answer (`null`, `false`, nothing) instead of running the original. For a
constructor that means a blank object. ArtHooks checks the backup's modifiers
and logs this exact rule as an error under the `ArtHooks` tag. `native` is never
linked, because the entry point is overwritten before anything calls it.

```java
// static native, no body: ArtHooks rewrites its entry point to the original.
static native void set_flags_hook_backup(Window thiz, int flags, int mask);

static void set_flags_hook(Window thiz, int flags, int mask) {
    if ((flags & LayoutParams.FLAG_SECURE) != 0) {
        flags ^= LayoutParams.FLAG_SECURE;
        mask  ^= LayoutParams.FLAG_SECURE;
    }
    set_flags_hook_backup(thiz, flags, mask);   // call it by its own name
}

Method original = Window.class.getMethod("setFlags", int.class, int.class);
ArtHooks.hook_function(original,
        MyHook.class.getDeclaredMethod("set_flags_hook", Window.class, int.class, int.class),
        MyHook.class.getDeclaredMethod("set_flags_hook_backup", Window.class, int.class, int.class));
```

The backup keeps its Java identity; only its entry point changes. Backup and
replacement must have **identical** signatures, both `static`, with the same
leading `thiz` for an instance target. Use `native` for every backup, including
a static target's: the error text names instance methods and constructors, but
a static backup with a body is just as easy to inline. In the built module,
`dexdump` should show it as `access : 0x0108 (STATIC NATIVE)`.

Use the two-argument form when you are replacing behaviour outright
(`return true`), and the three-argument form when you need the original's
result or side effects.

## `find_function` — picking one overload

```java
Executable m = ArtHooks.find_function(cls, "A01", "([B)LX/8Jt;");
Executable c = ArtHooks.find_function(cls, "<init>", "(Ljava/lang/String;)V");
```

Takes a JNI descriptor, which is what the smali already gives you — so a finder
that captures the method signature hands `find_function` exactly what it needs.
Use it when:

- several overloads share a name and you must pick one by exact signature;
- a parameter type is awkward to reach as a `Class` object (obfuscated,
  package-private, or not yet loaded).

Otherwise `getDeclaredMethod` is fine.

**It searches superclasses**, like JNI's own lookup and unlike
`getDeclaredMethod`. An inherited method therefore resolves to the
superclass's method — and hooking that redirects it for *every* subclass. Both
a footgun and a tool: it is how you hook one framework method and catch all
implementors, and it is how you accidentally hook far more than you meant to.

## Descriptor cheat sheet

| Java | Descriptor |
|---|---|
| `boolean` / `int` / `long` / `void` | `Z` / `I` / `J` / `V` |
| `byte` / `char` / `short` / `float` / `double` | `B` / `C` / `S` / `F` / `D` |
| `String` | `Ljava/lang/String;` |
| `byte[]` | `[B` |
| `String[]` | `[Ljava/lang/String;` |
| `boolean f(byte[] b)` | `([B)Z` |

Smali writes descriptors in this exact form, so capture them verbatim from the
`.method` line.

## AOT code and inlining

By default ArtHooks defends against inlining itself:

- **The static initialiser calls `disable_aot()`**, unless the Java system
  property `arthooks.keep_aot` (`KEEP_AOT_PROPERTY`) is `"true"`. ART stops
  running the app's precompiled code and deoptimizes the boot image. A caller
  that dex2oat compiled with the target inlined into it is never run again. The
  log line is `ART will no longer run AOT code, and the boot image was
  deoptimized; hooks now survive a speed/speed-profile build`.
  `is_aot_disabled()` reports the outcome.
- **Every hooked target gets `kAccCompileDontBother`** (`kAccCompileDontBother
  set on ...`), so the JIT never compiles it and never inlines it into a caller
  later.

So a tiny target (a 3-instruction `OkHttpClient$Builder.build()`, a Kotlin
companion factory) does hook reliably, provided ArtHooks loads before the
target's class is linked. The injected provider is normally early enough. These
warnings mean it was not, or the platform would not cooperate:

| ArtHooks log | Meaning |
|---|---|
| `the target is on AOT code because ART linked its class before ArtHooks loaded` | Touch `com.arthooks.ArtHooks` earlier, or hook the caller instead |
| `the target is on AOT code and ArtHooks did not stop ART using it` | `disable_aot()` failed or `arthooks.keep_aot` was set; inlined copies keep the old body |
| `libart does not export what it takes to stop ART using AOT code` | This platform cannot be defended; hook a larger method on the same path |
| `could not deoptimize the boot image` | A framework (boot-classpath) target can still miss calls made from inside the framework |
| `static target ... still on the resolution stub ...; refusing to hook it` | The static target's class has not run its fixups. ArtHooks forces class init first, and this means that failed |

## Constraints

- The replacement class must be loaded and initialised before hooking.
- Hooks are process-wide. `unhook_function` restores an original, but the
  projects never need it, and `Hook.unload()` in these projects is a logging
  stub.
- Give each target its own replacement method. Sharing one replacement across
  several originals is untested, and separate replacements cost nothing and make
  the log say which target fired.
- The `flag_probe_*` and `layout_probe_*` methods in `ArtHooks.java` exist to
  discover ART's `ArtMethod` layout at startup. Never rename, reorder or add
  methods between them.
