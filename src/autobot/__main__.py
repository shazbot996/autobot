"""`python -m autobot`: print the version and point at the control layer."""

from autobot import __version__


def main() -> int:
    print(f"autobot {__version__}")
    print("Runtime control lives in the Makefile: run `make` for the commands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
