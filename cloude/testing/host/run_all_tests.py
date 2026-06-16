import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
tests_dir = HERE / "tests"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(tests_dir))

import test_persistence
import test_jsonl_queue
import test_concurrency
import test_cell_prompt_extractor

def run_cell_prompt_extractor_tests():
    from test_cell_prompt_extractor import extract_cell_number
    assert extract_cell_number("explain cell 1") == 1
    assert extract_cell_number("explain cell1") == 1
    assert extract_cell_number("explain cell#2") == 2
    assert extract_cell_number("please explain (cell 3)") == 3
    assert extract_cell_number("check [cell 4] dependencies") == 4
    assert extract_cell_number("CELL 5") == 5
    assert extract_cell_number("what is in cell 0") is None
    assert extract_cell_number("no cell here") is None
    assert extract_cell_number("cell -2") is None
    print("test_cell_prompt_extractor assertions passed!")

def main():
    print("=== STARTING ALL TESTS ===")
    
    print("\n1. Running test_persistence...")
    test_persistence.test_atomic_write_and_read()
    print("test_persistence OK")
    
    print("\n2. Running test_jsonl_queue...")
    test_jsonl_queue.test_append_read_tail()
    print("test_jsonl_queue OK")
    
    print("\n3. Running test_concurrency...")
    test_concurrency.test_concurrent_writes()
    print("test_concurrency OK")
    
    print("\n4. Running test_cell_prompt_extractor...")
    run_cell_prompt_extractor_tests()
    print("test_cell_prompt_extractor OK")
    
    print("\n=== ALL TESTS PASSED SUCCESSFULLY ===")

if __name__ == "__main__":
    main()
