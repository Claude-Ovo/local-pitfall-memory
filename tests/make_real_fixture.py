import json, sys
err = ("Traceback (most recent call last):\n"
       '  File "C:\\Users\\miku\\proj\\app.py", line 12, in <module>\n'
       "    import yaml\n"
       "ModuleNotFoundError: No module named 'yaml'")
json.dump({"error_text": err, "context": {"cwd": "C:/Users/miku/proj", "runtime": "python"},
           "root_cause": "PyYAML not installed in this venv", "fix_command": "pip install pyyaml",
           "verify_method": "python app.py exits 0"}, open(sys.argv[1], "w"))
print("ok")
