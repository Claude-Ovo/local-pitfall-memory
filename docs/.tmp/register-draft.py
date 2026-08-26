"""Copy a generated draft folder into the JianYing library and register it in root_meta_info.json.
Usage: python register-draft.py <draft-folder> [--replace]   (JianYing must be closed)"""
import json, shutil, sys, time
from pathlib import Path

SRC = Path(sys.argv[1]).resolve(); NAME = SRC.name
LIB = Path(r"C:/Users/miku/AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft")
DST = LIB / NAME; ROOT = LIB / "root_meta_info.json"
replace = "--replace" in sys.argv
if DST.exists():
    if not replace:
        raise FileExistsError(f"{DST} exists (use --replace)")
    shutil.rmtree(DST)
shutil.copytree(SRC, DST)
root = json.loads(ROOT.read_text(encoding="utf-8")); store = root["all_draft_store"]
store[:] = [e for e in store if e["draft_name"] != NAME]
tmpl = next(e for e in store if e["draft_name"].startswith("Sensei-Demo-Styled"))
meta = json.loads((SRC / "draft_meta_info.json").read_text(encoding="utf-8"))
content = json.loads((SRC / "draft_content.json").read_text(encoding="utf-8"))
now = int(time.time() * 1_000_000); fold = f"{LIB.as_posix()}/{NAME}"
e = dict(tmpl); e.update({"draft_cover": f"{fold}\\draft_cover.jpg", "draft_fold_path": fold, "draft_id": meta["draft_id"],
                          "draft_json_file": f"{fold}\\draft_content.json", "draft_name": NAME, "tm_draft_create": now,
                          "tm_draft_modified": now, "tm_duration": content["duration"]})
shutil.copy2(ROOT, ROOT.with_name(f"root_meta_info.json.bak-{time.strftime('%Y%m%d%H%M%S')}"))
store.insert(0, e); ROOT.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")
print("registered", NAME, meta["draft_id"])
