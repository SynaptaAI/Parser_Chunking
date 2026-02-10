import logging

from .pipeline import run_default


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    run_default()


if __name__ == "__main__":
    main()
