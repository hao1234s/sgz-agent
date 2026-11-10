"""Independent stdio calculation worker."""
import argparse
import sys
from ..core.rpc import serve_stream
from .service import AnalysisService


def main():
    parser = argparse.ArgumentParser(prog='sgz-analysis')
    parser.add_argument('command', choices=['serve'])
    parser.parse_args()
    serve_stream(AnalysisService(), sys.stdin, sys.stdout)


if __name__ == '__main__':
    main()
