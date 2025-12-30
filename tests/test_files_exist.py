import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Prefer data in the package folder, but accept top-level data/ if present
DATA_DIRS = [ROOT / 'migration_planning' / 'data', ROOT / 'data']

def test_data_files_exist():
    required = ['apps.csv', 'dependencies.csv', 'servers.csv', 'databases.csv']
    found = False
    for d in DATA_DIRS:
        if not d.exists():
            continue
        # check whether this directory contains ALL required files
        missing = [name for name in required if not (d / name).exists()]
        if not missing:
            found = True
            break
    assert found, f"None of expected data directories contain required files. Checked: {DATA_DIRS}"


def test_main_script_exists():
    script = ROOT / 'migration_planning' / 'scripts' / 'run_community_detection.py'
    assert script.exists(), f"Script not found: {script}"
