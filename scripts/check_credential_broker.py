"""Synthetic Keychain integration check across different signed caller binaries.

Never reads production items. Temporary signed callers are deleted on exit.
"""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

CLIENT = r'''
#import <Foundation/Foundation.h>
#include <unistd.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 4) return 2;
        NSString *operation = @(argv[2]);
        NSDictionary *request = @{@"operation": operation, @"provider": @"exa", @"parent": @(getpid()),
                                  @"interactive": @NO, @"fixture": @(argv[3]), @"key": @"fixture-native"};
        NSTask *task = [NSTask new];
        task.executableURL = [NSURL fileURLWithPath:@(argv[1])];
        NSPipe *input = [NSPipe pipe], *output = [NSPipe pipe];
        task.standardInput = input; task.standardOutput = output;
        task.standardError = [NSFileHandle fileHandleWithNullDevice];
        if (![task launchAndReturnError:NULL]) return 3;
        [input.fileHandleForWriting writeData:[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL]];
        [input.fileHandleForWriting closeFile];
        NSData *response = [output.fileHandleForReading readDataToEndOfFile];
        [task waitUntilExit];
        NSDictionary *reply = [NSJSONSerialization JSONObjectWithData:response options:0 error:NULL];
        BOOL ok = task.terminationStatus == 0;
        if ([operation isEqual:@"get"]) ok = ok && [reply[@"key"] isEqual:@"fixture-native"];
        printf("{\"ok\":%s,\"marker\":%d,\"helper_exit\":%d}\n",
               ok ? "true" : "false", MARKER, task.terminationStatus);
        return ok ? 0 : 1;
    }
}
'''


def check(helper, identity):
    token = uuid.uuid4().hex
    result = {'fixture_only': True, 'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix='aide-broker-test-') as directory:
        path = Path(directory)
        source = path / 'client.m'
        source.write_text(CLIENT)
        sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
        clients = []
        for marker in (1, 2):
            client = path / f'client-{marker}'
            subprocess.run(['xcrun', 'clang', '-isysroot', sdk, '-fobjc-arc', f'-DMARKER={marker}',
                            '-framework', 'Foundation', str(source), '-o', str(client)],
                           check=True, capture_output=True)
            subprocess.run(['codesign', '--force', '--sign', identity, '--identifier',
                            'com.milin.ai-desktop-assistant', str(client)], check=True, capture_output=True)
            clients.append(client)
        result['caller_binaries_differ'] = clients[0].read_bytes() != clients[1].read_bytes()

        def invoke(client, operation):
            completed = subprocess.run([str(client), str(helper), operation, token], capture_output=True, timeout=10)
            try:
                response = json.loads(completed.stdout)
                result[f'{client.name}_{operation}_exit'] = response['helper_exit']
                return completed.returncode == 0 and response['ok']
            except (ValueError, KeyError):
                return False

        try:
            result['write_caller_1'] = invoke(clients[0], 'set')
            result['read_caller_2_without_prompt'] = invoke(clients[1], 'get')
            result['update_caller_2'] = invoke(clients[1], 'set')
            result['read_caller_1_without_prompt'] = invoke(clients[0], 'get')
            # A normal Python parent is not a signed App, even with valid pipe IPC.
            payload = json.dumps({'operation': 'probe', 'provider': 'exa', 'parent': os.getpid()}).encode()
            rejected = subprocess.run([str(helper)], input=payload, capture_output=True, timeout=10)
            result['untrusted_caller_rejected'] = rejected.returncode != 0 and not rejected.stdout
        finally:
            result['fixture_deleted'] = invoke(clients[0], 'delete')
    result['passed'] = all(result[name] for name in (
        'caller_binaries_differ', 'write_caller_1', 'read_caller_2_without_prompt', 'update_caller_2',
        'read_caller_1_without_prompt', 'untrusted_caller_rejected', 'fixture_deleted'))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--identity', default='AI Desktop Assistant')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = check(args.helper.resolve(), args.identity)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['passed'] else 1)
