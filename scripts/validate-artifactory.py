#!/usr/bin/env python3
"""Gate: prove the finders and the {{placeholders}} agree, before building.

    validate-artifactory.py --project ./MoovitPatcher --work ./.patcher-work/moovit

Why this exists
---------------
stitch's patch_artifacts() is an unconditional string replace:

    data = data.replace(f'{{{{{key}}}}}'.encode(), value.encode())

If a finder matches nothing its key never enters the artifactory, the literal
{{FOO_CLASS_NAME}} survives into the Java source, COMPILES CLEANLY because it
is only a string, and then fails at runtime inside the try/catch every hook
wraps its load() body in. The result is a patched APK that installs, launches,
behaves normally, and silently does not hook. Nothing else in the pipeline
notices.

What it checks
--------------
  FAIL  a finder with is_once=True that never fired
  FAIL  a finder that raised
  FAIL  a {{PLACEHOLDER}} in smali_generator/ with no artifactory key to fill it
  WARN  an artifactory key no placeholder consumes (usually a typo on one side)
  WARN  a finder on disk that main.py never registers

It runs the real stitch generate_artifactory() over the real extracted smali,
with the same args shape main.py passes, so a pass here means the same code
path will produce the same artifactory during the actual patch.
"""
import argparse
import importlib.util
import json
import re
import sys
import time
import traceback
from argparse import Namespace
from pathlib import Path

PLACEHOLDER_RE = re.compile(rb'\{\{([A-Z0-9_]+)\}\}')
SKIP_SUFFIXES = {'.jar', '.apk', '.so', '.png', '.webp', '.keystore', '.jks', '.zip'}
SKIP_DIRS = {'build', '.gradle', '.idea', '.cxx', '.git', '__pycache__', 'release'}


class Guard:
    """Wraps a finder so an exception inside it becomes a reported FAIL rather
    than a traceback that kills the gate and hides every other finder."""

    def __init__(self, finder):
        self.finder = finder
        self.name = type(finder).__name__
        self.error = None

    @property
    def is_once(self):
        return getattr(self.finder, 'is_once', True)

    @property
    def is_found(self):
        return getattr(self.finder, 'is_found', False)

    def class_filter(self, class_data: str) -> bool:
        if self.error:
            return False
        try:
            return bool(self.finder.class_filter(class_data))
        except Exception:
            self.error = traceback.format_exc(limit=4)
            return False

    def extract_artifacts(self, artifacts: dict, class_data: str) -> None:
        if self.error:
            return
        try:
            self.finder.extract_artifacts(artifacts, class_data)
        except Exception:
            self.error = traceback.format_exc(limit=4)


def discover(project: Path, args_ns: Namespace):
    """Import every SimpleArtifactoryFinder subclass under artifactory_generator/."""
    from stitch.artifactory_generator.SimpleArtifactoryFinder import SimpleArtifactoryFinder

    pkg_dir = project / 'artifactory_generator'
    if not pkg_dir.is_dir():
        sys.exit(f'[!] no artifactory_generator/ under {project}')
    sys.path.insert(0, str(project))

    found = []
    for py in sorted(pkg_dir.glob('*.py')):
        if py.name == '__init__.py':
            continue
        mod_name = f'artifactory_generator.{py.stem}'
        spec = importlib.util.spec_from_file_location(mod_name, py)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            sys.exit(f'[!] failed to import {py.name}: {e}')
        for attr in vars(module).values():
            if (isinstance(attr, type) and issubclass(attr, SimpleArtifactoryFinder)
                    and attr is not SimpleArtifactoryFinder
                    and attr.__module__ == mod_name):
                try:
                    found.append(Guard(attr(args_ns)))
                except Exception as e:
                    sys.exit(f'[!] {attr.__name__}(args) raised on construction: {e}')
    return found


def scan_placeholders(module_dir: Path):
    """Every {{KEY}} under smali_generator/, mapped to the files using it."""
    hits = {}
    for path in module_dir.rglob('*'):
        if not path.is_file() or path.suffix in SKIP_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.relative_to(module_dir).parts):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for m in PLACEHOLDER_RE.finditer(raw):
            hits.setdefault(m.group(1).decode(), set()).add(
                str(path.relative_to(module_dir)))
    return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', default='.', help='Patcher project root (default .)')
    parser.add_argument('--work', required=True,
                        help='Work dir from apk-extract.py, e.g. ./.patcher-work/moovit')
    parser.add_argument('--apk-path', default=None,
                        help='Original APK path, for finders that read self.args.apk_path')
    parser.add_argument('--extra-artifacts', nargs='+', default=[], metavar='K:V',
                        help='Same as main.py --extra-artifacts')
    parser.add_argument('--out', default=None,
                        help='Where to write artifactory.json (default <project>/artifactory.json)')
    args = parser.parse_args()

    try:
        from stitch.artifactory_generator.generate_artifactory import generate_artifactory
        from stitch.common import EXTRACTED_PATH
    except ImportError:
        sys.exit("[!] cannot import stitch -- activate your project venv, or run this\n    script with that venv's python: /path/to/.venv/bin/python <script>")

    project = Path(args.project).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    extracted = work / EXTRACTED_PATH
    if not extracted.is_dir():
        sys.exit(f'[!] {extracted} not found -- run apk-extract.py first')

    module_dir = project / 'smali_generator'
    if not module_dir.is_dir():
        sys.exit(f'[!] no smali_generator/ under {project}')

    extra = {kv.split(':', 1)[0]: kv.split(':', 1)[1] for kv in args.extra_artifacts}

    # The same args object shape main.py hands its finders.
    apk_path = args.apk_path
    if apk_path is None:
        bundle = work / 'bundle'
        if bundle.is_dir():
            from stitch.apk_utils import main_apk_name
            apk_path = str(bundle / main_apk_name)
        else:
            apk_path = str(work)
    args_ns = Namespace(apk_path=apk_path, temp_path=str(work), output='output.apk',
                        arch='arm64-v8a', api_key=None, should_sign=True,
                        extra_artifacts=args.extra_artifacts)

    guards = discover(project, args_ns)
    if not guards:
        print('[-] No finders found under artifactory_generator/.')

    print(f'[+] {len(guards)} finder(s): {", ".join(g.name for g in guards) or "-"}')
    print(f'[+] Scanning {sum(1 for _ in extracted.rglob("*.smali"))} smali classes ...')
    t0 = time.time()
    artifacts = generate_artifactory(work, list(guards))   # copy: it mutates the list
    artifacts.update(extra)
    print(f'[+] Finished in {time.time() - t0:.1f}s')
    print()

    fails, warns = [], []

    # --- finders ---
    print('Finders')
    for g in guards:
        if g.error:
            print(f'  FAIL  {g.name}  raised')
            print('        ' + g.error.strip().replace('\n', '\n        '))
            fails.append(f'{g.name} raised')
        elif g.is_once and not g.is_found:
            print(f'  FAIL  {g.name}  never fired')
            fails.append(f'{g.name} never fired')
        else:
            print(f'  PASS  {g.name}')

    # a finder on disk that main.py never registers would pass the gate and
    # then be absent from the real run
    main_py = project / 'main.py'
    if main_py.is_file():
        # An import alone is not registration, and a commented-out entry is not
        # registration either -- look for a live call site.
        lines = []
        for line in main_py.read_text().splitlines():
            line = re.sub(r'#.*$', '', line)
            if re.match(r'\s*(from|import)\s', line):
                continue
            lines.append(line)
        main_src = '\n'.join(lines)
        for g in guards:
            if not re.search(rf'\b{re.escape(g.name)}\s*\(', main_src):
                print(f'  WARN  {g.name}  not registered in main.py artifactory_list')
                warns.append(f'{g.name} not registered in main.py')

    # --- placeholders ---
    placeholders = scan_placeholders(module_dir)
    keys = set(artifacts)
    print()
    print(f'Placeholders  ({len(placeholders)} distinct in smali_generator/)')
    for key in sorted(placeholders):
        where = ', '.join(sorted(placeholders[key]))
        if key in keys:
            print(f'  PASS  {{{{{key}}}}}  <- {artifacts[key]!r}')
        else:
            print(f'  FAIL  {{{{{key}}}}}  no artifactory key  ({where})')
            fails.append(f'{{{{{key}}}}} unsubstituted')
    for key in sorted(keys - set(placeholders)):
        print(f'  WARN  {key}  produced but no placeholder uses it')
        warns.append(f'{key} unused')

    out = Path(args.out) if args.out else project / 'artifactory.json'
    out.write_text(json.dumps(artifacts, indent=2, sort_keys=True))
    print()
    print(f'[+] artifactory written to {out}')

    print()
    if fails:
        print(f'GATE FAILED  ({len(fails)} failure(s), {len(warns)} warning(s))')
        for f in fails:
            print(f'  - {f}')
        print()
        print('Do not build. A surviving {{...}} compiles fine and fails silently at runtime.')
        return 1
    print(f'GATE PASSED  ({len(warns)} warning(s))')
    return 0


if __name__ == '__main__':
    sys.exit(main())
