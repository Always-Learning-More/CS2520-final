import os
import sys

# allow "python3 -m unittest tests.test_engine" as well as discover -s tests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
