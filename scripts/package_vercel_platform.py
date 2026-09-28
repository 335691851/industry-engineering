"""Compatibility entrypoint: new builds use the three-project Hobby package."""
from package_hobby import package

if __name__ == "__main__":
    package()
