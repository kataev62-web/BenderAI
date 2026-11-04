from bender_ai.training_form import main
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Native BENDER test fixture')
    parser.add_argument('--title', default='BENDER AI Training')
    main(parser.parse_args().title)
