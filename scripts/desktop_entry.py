from multiprocessing import freeze_support

if __name__ == '__main__':
    freeze_support()
    from bender_ai.entrypoint import run
    raise SystemExit(run())
