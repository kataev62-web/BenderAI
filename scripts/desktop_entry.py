from multiprocessing import freeze_support
from bender_ai.app import main

if __name__ == '__main__':
    freeze_support()
    raise SystemExit(main())
