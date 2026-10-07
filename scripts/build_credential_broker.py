"""Reuse the exact signed helper across App rebuilds, then seal it into the bundle."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def install(root, app, identity):
    # A caller certificate is essential to the helper's private IPC contract.
    if identity == '-':
        print('    ad-hoc 包不安装凭据助手；联网搜索需要证书签名构建。')
        return
    identities = run('security', 'find-identity', '-v', '-p', 'codesigning').stdout
    matches = re.findall(r'([A-Fa-f0-9]{40}) "([^"]+)"', identities)
    fingerprints = [fp for fp, name in matches if identity in (fp, name)]
    if len(fingerprints) != 1:
        raise RuntimeError('Cannot resolve an unambiguous signing certificate')
    fingerprint = fingerprints[0]
    source = root / 'native/search_credentials.m'
    compiler = run('xcrun', '--find', 'clang').stdout.strip()
    version = run(compiler, '--version').stdout
    sdk = run('xcrun', '--show-sdk-path').stdout.strip()
    sdk_version = run('xcrun', '--show-sdk-version').stdout.strip()
    arch = run('uname', '-m').stdout.strip()
    digest = hashlib.sha256(source.read_bytes() + Path(__file__).read_bytes() +
                            (fingerprint+version+sdk_version+arch).encode()).hexdigest()
    cache = root / 'build/credential-broker' / digest
    cache.mkdir(parents=True, exist_ok=True)
    helper = cache / 'AIDESearchCredentials'
    requirement = f'identifier "com.milin.ai-desktop-assistant.credentials" and certificate leaf = H"{fingerprint}"'
    if not helper.exists():
        candidate = cache / 'candidate'
        run(compiler, '-isysroot', sdk, '-mmacosx-version-min=11.0', '-O2', '-fobjc-arc',
            '-Wno-deprecated-declarations',
            '-framework', 'Foundation', '-framework', 'Security', str(source), '-o', str(candidate))
        run('codesign', '--force', '--options', 'runtime', '--timestamp=none',
            '--identifier', 'com.milin.ai-desktop-assistant.credentials', '--sign', identity, str(candidate))
        candidate.replace(helper)
    run('codesign', '--verify', '--strict', '-R', '='+requirement, str(helper))
    details = run('codesign', '-d', '--verbose=4', str(helper)).stderr
    cdhash = re.search(r'^CDHash=(\w+)$', details, re.MULTILINE).group(1)
    destination = app / 'Contents/Helpers'
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(helper, destination / helper.name)
    manifest = {'sha256': hashlib.sha256(helper.read_bytes()).hexdigest(), 'cdhash': cdhash,
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'certificate_sha1': fingerprint, 'protocol': 1}
    resources = app / 'Contents/Resources'
    resources.mkdir(parents=True, exist_ok=True)
    (resources / 'credentials.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'    稳定凭据助手 CDHash: {cdhash}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--identity', required=True)
    args = parser.parse_args()
    install(args.root, args.app, args.identity)
