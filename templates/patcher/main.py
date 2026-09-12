import argparse
from pathlib import Path

from stitch import Stitch
from stitch.common import ExternalModule

# Signature finders live in ./artifactory_generator/ and are registered here.
# from artifactory_generator.my_finder import MyFinder

PROVIDER_CLASS = 'com.smali_generator.InitProvider__NAME__'


def get_args():
    parser = argparse.ArgumentParser(description='Patch __NAME__ with the smali_generator hook module.')
    parser.add_argument('-p', '--apk-path', dest='apk_path', help='APK / XAPK / APKM path', required=True)
    parser.add_argument('-o', '--output', dest='output', help='Output APK path', required=False,
                        default='output.apk')
    parser.add_argument('-t', '--temp', dest='temp_path', help='Temp path for extracted content', required=False,
                        default='./temp')
    parser.add_argument('--arch', dest='arch', help='ABI whose libarthooks.so gets injected', required=False,
                        default='arm64-v8a',
                        choices=['arm64-v8a', 'armeabi-v7a', 'x86_64', 'x86'])
    parser.add_argument('-g', '--google-api-key', dest='api_key', help='Custom google api key', required=False,
                        default=None)
    parser.add_argument('--no-sign', dest='should_sign', help='Whether to sign the output APK',
                        action='store_false', required=False, default=True)
    parser.add_argument('--extra-artifacts', dest='extra_artifacts',
                        help='Extra artifacts for the artifactory, in the format "key:value"',
                        required=False, default=[], nargs='+')
    args, _ = parser.parse_known_args()
    return args


def main():
    args = get_args()
    extra_artifacts = {artifact.split(':', 1)[0]: artifact.split(':', 1)[1] for artifact in args.extra_artifacts}
    external_modules = [
        ExternalModule(Path(__file__).parent / './smali_generator', PROVIDER_CLASS)
    ]
    artifactory_list = [
        # MyFinder(args),
    ]
    with Stitch(
            apk_path=args.apk_path,
            output_apk=args.output,
            temp_path=args.temp_path,
            artifactory_list=artifactory_list,
            google_api_key=args.api_key,
            external_modules=external_modules,
            arch=args.arch,
            should_sign=args.should_sign,
            extra_artifacts=extra_artifacts,
    ) as stitch:
        stitch.patch()


if __name__ == '__main__':
    main()
