#!/usr/bin/env python3
"""Scaffold a new patcher project from the vendored template.

    new-patcher.py Moovit ~/projects/MoovitPatcher --package com.moovit.app

Copies templates/patcher, then renames the InitProvider class everywhere it is
referenced. stitch scopes the manifest authority to the target's package
(<target package>.<provider FQN>), so two patchers for two different apps may
share a provider name. Keep it per-project anyway: it is what distinguishes
your classes in a logcat shared with every other patched app on the device.
"""
import argparse
import os
import re
import shutil
import stat
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / 'templates' / 'patcher'
SDK_CANDIDATES = ('ANDROID_HOME', 'ANDROID_SDK_ROOT')
NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9]*$')

# Build scratch and machine-local state. Never copied into a new project even if
# it somehow appears in the template -- .gradle/ in particular holds lock files
# and file hashes from whatever machine last ran a build there.
JUNK = shutil.ignore_patterns(
    '.gradle', 'build', '.cxx', '.idea', '__pycache__', '*.pyc',
    'local.properties', '.venv', 'temp', '.patcher-work', '*.apk', '.env',
)


def find_sdk():
    for var in SDK_CANDIDATES:
        val = os.environ.get(var)
        if val and (Path(val) / 'platforms').is_dir():
            return Path(val)
    for cand in (Path.home() / 'Android' / 'Sdk', Path.home() / 'Library' / 'Android' / 'sdk'):
        if (cand / 'platforms').is_dir():
            return cand
    return None


def substitute(root: Path, mapping: dict) -> int:
    """Replace placeholders in file contents and in file names."""
    touched = 0
    for path in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            continue
        if path.suffix in ('.jar', '.apk', '.so', '.png'):
            continue
        raw = path.read_bytes()
        new = raw
        for key, value in mapping.items():
            new = new.replace(key.encode(), value.encode())
        if new != raw:
            path.write_bytes(new)
            touched += 1
        new_name = path.name
        for key, value in mapping.items():
            new_name = new_name.replace(key, value)
        if new_name != path.name:
            path.rename(path.with_name(new_name))
    return touched


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', help='Project name, CamelCase, e.g. Moovit')
    parser.add_argument('output', help='Directory to create')
    parser.add_argument('--package', default='the target app',
                        help='Target app package name, for the README')
    parser.add_argument('--force', action='store_true', help='Overwrite an existing directory')
    args = parser.parse_args()

    if not NAME_RE.match(args.name):
        sys.exit(f'[!] name must be CamelCase letters/digits, got {args.name!r} '
                 '(it becomes a Java class name)')

    out = Path(args.output).expanduser().resolve()
    if out.exists():
        if not args.force:
            sys.exit(f'[!] {out} already exists (use --force to overwrite)')
        shutil.rmtree(out)
    if not TEMPLATE.is_dir():
        sys.exit(f'[!] template missing at {TEMPLATE}')

    shutil.copytree(TEMPLATE, out, ignore=JUNK)
    touched = substitute(out, {'__NAME__': args.name, '__PACKAGE__': args.package})

    gradlew = out / 'smali_generator' / 'gradlew'
    gradlew.chmod(gradlew.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    sdk = find_sdk()
    if sdk:
        (out / 'smali_generator' / 'local.properties').write_text(
            '# Written by new-patcher.py. Machine-local; gitignored.\n'
            f'sdk.dir={sdk}\n'
        )

    provider = f'com.smali_generator.InitProvider{args.name}'
    print(f'[+] Created {out}')
    print(f'[+] Substituted placeholders in {touched} file(s)')
    print(f'[+] Provider class: {provider}')
    print('[+] Manifest authority: <target package>.' + provider)
    print(f'[+] Android SDK: {sdk if sdk else "NOT FOUND -- set sdk.dir in smali_generator/local.properties"}')
    print()
    print('    Next:')
    print(f'      cd {out}')
    print('      python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt')
    print('      # write a finder in artifactory_generator/, a Hook in')
    print('      # smali_generator/app/src/main/java/com/smali_generator/patches/,')
    print('      # then run validate-artifactory.py before building.')


if __name__ == '__main__':
    main()
