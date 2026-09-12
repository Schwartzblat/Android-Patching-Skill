#!/usr/bin/env python3
"""Extract an APK/XAPK/APKM into a persistent work directory.

    apk-extract.py ./moovit.apkm
    apk-extract.py ./app.apk --work /tmp/work --force

Calls stitch's own apk_utils.extract_apk, so the layout here is byte-identical
to what Stitch.patch() produces internally -- including bundle handling, where
the largest inner APK becomes base.apk. That matters because the signature
finders are developed against this directory and then run, unchanged, inside a
real patch.

Stitch's own temp_path cannot be reused for this: Stitch.__init__ refuses to
start if it already exists, and __exit__ deletes it.

Layout produced:
    <work>/<apk stem>/extracted/     apktool output -- smali, resources, manifest
    <work>/<apk stem>/bundle/        split APKs, bundles only
"""
import argparse
import shutil
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('apk', help='APK / XAPK / APKM to extract')
    parser.add_argument('--work', default='./.patcher-work',
                        help='Work root (default ./.patcher-work)')
    parser.add_argument('--force', action='store_true', help='Re-extract if already present')
    args = parser.parse_args()

    try:
        from stitch.apk_utils import extract_apk, is_bundle
        from stitch.common import EXTRACTED_PATH
    except ImportError:
        sys.exit("[!] cannot import stitch -- activate your project venv, or run this\n    script with that venv's python: /path/to/.venv/bin/python <script>")

    apk = Path(args.apk).expanduser().resolve()
    if not apk.is_file():
        sys.exit(f'[!] no such file: {apk}')

    work = (Path(args.work).expanduser() / apk.stem).resolve()
    extracted = work / EXTRACTED_PATH

    if extracted.is_dir():
        if not args.force:
            print(f'[+] Already extracted: {extracted}')
            print('    (--force to re-extract)')
            report(apk, work, extracted)
            return
        shutil.rmtree(work)

    work.mkdir(parents=True, exist_ok=True)
    bundle = is_bundle(apk)
    print(f'[+] {"Bundle" if bundle else "Single APK"}: {apk.name}')
    print(f'[+] Extracting to {work} ...')
    extract_apk(apk, work)
    report(apk, work, extracted)


def report(apk: Path, work: Path, extracted: Path):
    smali_dirs = sorted(p.name for p in extracted.iterdir()
                        if p.is_dir() and p.name.startswith('smali'))
    n_smali = sum(1 for _ in extracted.rglob('*.smali'))
    print(f'[+] smali dirs: {", ".join(smali_dirs) or "none"}  ({n_smali} classes)')

    target = apk
    bundle_dir = work / 'bundle'
    if bundle_dir.is_dir():
        from stitch.apk_utils import main_apk_name
        target = bundle_dir / main_apk_name
        splits = sorted(p.name for p in bundle_dir.glob('*.apk'))
        print(f'[+] bundle base: {main_apk_name}  ({len(splits)} apk(s) total)')
    try:
        from androguard.core.apk import APK
        info = APK(str(target))
        print(f'[+] package: {info.get_package()}  versionName: {info.get_androidversion_name()}')
    except Exception as e:
        print(f'[-] could not read package info: {e}')

    print()
    print(f'    Work dir for the gate:  --work {work}')


if __name__ == '__main__':
    main()
