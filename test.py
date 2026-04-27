"""
Dev sanity script. Run with `python test.py` after `pip install -e .`.
"""
from debugflow import flow_engine


def main():
    return "Nothing"


# NB: was previously `if __import__ == "__main__":` — a typo that meant the
# launch() call never ran. The intended check is `__name__`.
if __name__ == "__main__":
    flow_engine.launch("main")
