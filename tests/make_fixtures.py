import json, sys, os
err = (
    "node:internal/modules/cjs/loader:1958\n"
    "    throw err;\n"
    "    ^\n"
    "Error [ERR_REQUIRE_ESM]: require() of ES Module "
    r"C:\Users\miku\demo\server.js from C:\Users\miku\demo\index.js not supported."
    "\n"
    r"    at Object.<anonymous> (C:\Users\miku\demo\index.js:3:15)"
)
d = sys.argv[1] if len(sys.argv) > 1 else "."
json.dump({"error_text": err, "context": {"cwd": "C:/Users/miku/demo", "runtime": "node"}},
          open(os.path.join(d, "req.json"), "w"))
json.dump({"error_text": err, "context": {"cwd": "C:/Users/miku/demo", "runtime": "node"},
           "root_cause": "package.json lacks type:module while code uses ESM imports",
           "fix_command": "npm pkg set type=module",
           "verify_method": "node index.js exits 0"},
          open(os.path.join(d, "fix.json"), "w"))
print("fixtures written to", d)
