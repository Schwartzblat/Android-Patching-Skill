# ArtHooks

Java method hooking by overwriting the target `art::ArtMethod`'s entry point.
Gradle coordinate `com.github.Schwartzblat:ArtHooks` (jitpack), package
`com.arthooks`.

## API

```java
public static boolean    is_available();
public static Executable find_function(Class<?> owner, String name, String signature);
public static boolean    hook_function(Executable original, Executable replacement);
public static boolean    hook_function(Executable original, Executable replacement, Executable backup);
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

The three-argument form points `backup` at the original's pre-hook body:

```java
// Empty body. Its entry point is rewritten; only the signature matters.
static void set_flags_hook_backup(Window thiz, int flags, int mask) { }

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

The backup keeps its Java identity; only its entry point changes. Its body is
never executed, so leave it empty (or `return null` / a dummy value to satisfy
the compiler). Backup and replacement must have **identical** signatures.

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

## Constraints

- The replacement class must be loaded and initialised before hooking.
- Hooks are process-wide and permanent for the process; there is no unhook.
  A `Hook.unload()` in these projects is a logging stub.
- Inlined methods cannot be hooked reliably. A tiny getter the JIT has already
  inlined may keep running its original body at existing call sites.
- The `flag_probe_*` and `layout_probe_*` methods in `ArtHooks.java` exist to
  discover ART's `ArtMethod` layout at startup. Never rename, reorder or add
  methods between them.
