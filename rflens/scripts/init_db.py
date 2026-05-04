from backend.db import init_db


def main() -> None:
    init_db()
    print("RF Lens database initialized.")


if __name__ == "__main__":
    main()
