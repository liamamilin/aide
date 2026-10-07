"""Packaged-only checks emit safe booleans, never credentials."""
import argparse
import json
import sys

from ai_desktop.services.credential_broker import BrokerError, request


def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['parallel', 'exa'], default='parallel')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--fixture')
    parser.add_argument('--action', choices=['write', 'read', 'update', 'delete'], default='read')
    options = parser.parse_args(args)
    if not getattr(sys, 'frozen', False):
        print(json.dumps({'ok': False, 'error': 'packaged_only'}))
        return 2
    try:
        if options.fixture:
            action = options.action
            kwargs = {'fixture': options.fixture, 'interactive': False}
            if action in {'write', 'update'}:
                request('set', options.provider, key='fixture-v1' if action == 'write' else 'fixture-v2', **kwargs)
                value = request('get', options.provider, **kwargs)
                ok = value == ('fixture-v1' if action == 'write' else 'fixture-v2')
            elif action == 'delete':
                request('delete', options.provider, **kwargs)
                ok = request('get', options.provider, **kwargs) == ''
            else:
                ok = request('get', options.provider, **kwargs) == 'fixture-v1'
        else:
            if options.action != 'read':
                raise BrokerError('Real checks are read-only')
            ok = bool(request('get', options.provider, interactive=options.interactive))
        print(json.dumps({'ok': ok, 'provider': options.provider, 'fixture': bool(options.fixture)}))
        return 0 if ok else 1
    except BrokerError:
        print(json.dumps({'ok': False, 'provider': options.provider, 'error': 'credentials_unavailable'}))
        return 1
