import sys

from .cli import main

# See the launcher script: worker processes re-import the main module.
if __name__ == "__main__":
    sys.exit(main())
