"""Local macOS entry point: popover by default, explicit legacy menu fallback."""
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    legacy = '--legacy-menu' in arguments
    arguments = [argument for argument in arguments if argument != '--legacy-menu']
    if legacy:
        from .menu import main as run
    else:
        from .popover import main as run
        # A manually opened app should reveal its UI; diagnostics remain headless.
        if not any(flag in arguments for flag in ('--show', '--snapshot', '--help', '-h')):
            arguments.append('--show')
    return run(arguments)


if __name__ == '__main__':
    raise SystemExit(main())
