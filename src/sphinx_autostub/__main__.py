"""Command line interface, e.g. ``sphinx-autostub mypkg docs/api``."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from . import Renderer, find_stub_dir, format_param_mismatches, generate


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog='sphinx-autostub',
        description='Generate a Sphinx API reference from type stubs.')
    p.add_argument('package',
                   help='package name, or path to a directory of .pyi stubs')
    p.add_argument('output', help='directory receiving the .rst pages')
    p.add_argument('--style', choices=('google', 'numpy', 'rst'),
                   default='google',
                   help='docstring markup convention (default: google)')
    p.add_argument('--exclude', metavar='REGEX', action='append',
                   help='full-match pattern for names to leave undocumented; '
                        'may be repeated (replaces the default underscore '
                        'rule)')
    args = p.parse_args(argv)

    if os.path.isdir(args.package):
        stub_dir = args.package
        package = os.path.basename(os.path.normpath(args.package))
    else:
        stub_dir, package = find_stub_dir(args.package), args.package
        if stub_dir is None:
            sys.exit('No type stubs found for %r.' % args.package)

    renderer = Renderer(exclude=args.exclude, style=args.style)
    slugs = generate(stub_dir, args.output, package, renderer=renderer)
    print('Wrote %d pages to %s' % (len(slugs) + 1, args.output))

    if renderer.param_mismatches:
        print("\n%d documented parameters are not accepted by the stubs:" %
              len(renderer.param_mismatches))
        print('\n'.join(format_param_mismatches(renderer.param_mismatches)))


if __name__ == '__main__':
    main()
