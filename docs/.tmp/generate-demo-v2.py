"""Build the JianYing draft 'LPM-Demo-v1' (~41 s) from D:\录像\2026-08-27 05-21-19.mp4 (take 2).
Template: the Sensei v6 draft (same canvas/fps/text style). Everything is native JianYing elements so she can edit.
Cut list (source seconds → timeline), see docs/demo-cutlist-v1.md for the human version.
"""
from __future__ import annotations
import json, shutil, subprocess, sys, uuid
from pathlib import Path

TEMPLATE = Path(r"C:\Users\miku\sensei\docs\collab\jianying\Sensei-Demo-Styled-v6\draft_content.json")
TEMPLATE_META = TEMPLATE.with_name("draft_meta_info.json")
SRC = Path(r"D:\录像\2026-08-27 05-21-19.mp4")
BGM = Path(r"C:\Users\miku\sensei\docs\collab\audio\bgm-pulsar-55s.mp3")
NAME = "LPM-Demo-v2"
OUT = Path(__file__).resolve().parents[1] / "collab" / "jianying" / NAME
FONT = "C:/Windows/Fonts/msyhbd.ttc"          # Microsoft YaHei Bold (CJK-safe); change in JianYing if it does not resolve
US = 1_000_000

def uid(): return uuid.uuid4().hex
def load(p): return json.loads(p.read_text(encoding="utf-8"))
def dump(p, v):
    with p.open("w", encoding="utf-8", newline="\n") as h:
        json.dump(v, h, ensure_ascii=False, indent=4); h.write("\n")

def probe(path):
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height:format=duration", "-of", "json", str(path)],
                                  capture_output=True, text=True, check=True).stdout)
    return int(float(j["format"]["duration"]) * US), int(j["streams"][0]["width"]), int(j["streams"][0]["height"])

def audio_dur(path):
    return int(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                     str(path)], capture_output=True, text=True, check=True).stdout) * US)

# ---- cut list: (src_in, src_out, crop_top_fraction or None, scale, tx, ty, caption) -------------------------
HEAD, TAIL = 2.0, 2.5
CUTS = [
    (8.5, 14.0, None, 0.92, 0, 0, "第二次遇到同一个坑"),
    (56.5, 60.5, None, 0.92, 0, 0, "Qoder 自己跑命令，把完整报错交给踩坑库"),
    (72.5, 75.0, None, 0.92, 0, 0, "Skill local-pitfall-memory activated"),
    (93.5, 100.5, 0.11, 1.0, 0, 0, "exact · 可引用 —— 本机已验证的修复，本地命中 < 1 秒"),
    (147.0, 149.0, 0.80, 2.0, 1.0, 0, "照修复卡改一行"),
    (156.5, 162.0, 0.80, 2.0, 1.0, 0, "一次过"),
    (253.5, 259.5, 0.68, 1.25, 0.25, 0, "digest：一键汇编本机踩坑表"),
]
HEAD_PRIMARY, HEAD_SECONDARY = "LOCAL PITFALL MEMORY", "同一个坑，第二次：本地命中，不再从头猜"
TAIL_PRIMARY, TAIL_SECONDARY = "local-pitfall-memory", "Qwen3-4B INT4 @ OpenVINO · 历史库与检索不出机 · 魔搭技能中心"

def main():
    if OUT.exists():
        if "--force" not in sys.argv:
            raise FileExistsError(f"{OUT} exists (use --force)")
        shutil.rmtree(OUT)
    t = load(TEMPLATE); meta = load(TEMPLATE_META)
    src_dur, w, h = probe(SRC)
    mats = t["materials"]
    # templates to clone
    vmat_t = mats["videos"][0]
    vseg_t = t["tracks"][0]["segments"][1]
    lower_t = next(m for m in mats["texts"] if json.loads(m["content"])["text"] == "HELPFUL. CLICKED.")
    lower_seg_t = t["tracks"][1]["segments"][6]
    headp_t = next(m for m in mats["texts"] if json.loads(m["content"])["text"] == "SENSEI")
    heads_t = next(m for m in mats["texts"] if json.loads(m["content"])["text"].startswith("LEARN IN THE TERMINAL"))
    headp_seg_t = t["tracks"][2]["segments"][0]; heads_seg_t = t["tracks"][3]["segments"][0]
    tailp_seg_t = t["tracks"][2]["segments"][1]; tails_seg_t = t["tracks"][3]["segments"][1]
    amat_t = next(m for m in mats["audios"] if "bgm" in m["name"].lower())
    aseg_t = t["tracks"][5]["segments"][0]
    carrier_seg_t = t["tracks"][0]["segments"][0]      # silent head carrier (scale 1, vol 0)
    canvas_t = next(c for c in mats["canvases"] if c["id"] in vseg_t["extra_material_refs"])

    for k in ("videos", "texts", "audios", "speeds", "canvases"):
        mats[k] = []
    tracks = []

    def text_material(template, text, size=None):
        m = json.loads(json.dumps(template)); c = json.loads(m["content"])
        c["text"] = text; c["styles"][0]["range"] = [0, len(text)]
        if size: c["styles"][0]["size"] = size
        c["styles"][0]["font"] = {"id": "", "path": FONT}
        m["content"] = json.dumps(c, ensure_ascii=False); m["id"] = uid(); mats["texts"].append(m); return m

    def video_material(crop_top):
        m = json.loads(json.dumps(vmat_t))
        m.update({"id": uid(), "material_name": SRC.name, "path": str(SRC), "duration": src_dur, "width": w, "height": h})
        m["material_id"] = m["id"]
        if crop_top:
            m["crop"] = {"upper_left_x": 0, "upper_left_y": crop_top, "upper_right_x": 1, "upper_right_y": crop_top,
                         "lower_left_x": 0, "lower_left_y": 1, "lower_right_x": 1, "lower_right_y": 1}
            m["crop_ratio"] = "free"; m["crop_scale"] = 1
        mats["videos"].append(m); return m

    def speed():
        s = {"curve_speed": None, "id": uid(), "mode": 0, "speed": 1, "type": "speed"}; mats["speeds"].append(s); return s
    def canvas():
        c = json.loads(json.dumps(canvas_t)); c["id"] = uid(); mats["canvases"].append(c); return c

    def vseg(mat, start, dur, src_start, scale, tx, ty, volume, template=vseg_t):
        s = json.loads(json.dumps(template))
        sp, cv = speed(), canvas()
        s.update({"id": uid(), "material_id": mat["id"], "target_timerange": {"start": start, "duration": dur},
                  "source_timerange": {"start": src_start, "duration": dur}, "volume": volume,
                  "last_nonzero_volume": 1.0, "extra_material_refs": [sp["id"], cv["id"]]})
        s["clip"]["scale"] = {"x": scale, "y": scale}; s["clip"]["transform"] = {"x": tx, "y": ty}
        return s

    def tseg(mat, start, dur, template, tx=None, ty=None):
        s = json.loads(json.dumps(template))
        s.update({"id": uid(), "material_id": mat["id"], "target_timerange": {"start": start, "duration": dur},
                  "extra_material_refs": []})
        if tx is not None: s["clip"]["transform"] = {"x": tx, "y": ty}
        return s

    # ---- video track ----
    full = video_material(None)
    crops = {}
    v_segments, l_segments = [], []
    cursor = int(HEAD * US)
    # head carrier: a frozen dark frame from the source (first 2 s of the take is an empty prompt = clean dark)
    v_segments.append(vseg(full, 0, cursor, int(0.5 * US), 1.0, 0, 0, 0.0, carrier_seg_t))
    for (si, so, crop, scale, tx, ty, caption) in CUTS:
        dur = int((so - si) * US)
        mat = full if crop is None else crops.setdefault(crop, video_material(crop))
        v_segments.append(vseg(mat, cursor, dur, int(si * US), scale, tx, ty, 0.0))
        cm = text_material(lower_t, caption, 5.0)
        cap_y = 0.62 if (crop and crop >= 0.75) else -0.40
        l_segments.append(tseg(cm, cursor, dur, lower_seg_t, -0.4, cap_y))
        cursor += dur
    tail_start = cursor; tail_dur = int(TAIL * US)
    v_segments.append(vseg(full, tail_start, tail_dur, int(0.5 * US), 1.0, 0, 0, 0.0, carrier_seg_t))
    total = tail_start + tail_dur

    tracks.append({"attribute": 0, "flag": 0, "id": uid(), "is_default_name": False, "name": "V1 CUT", "type": "video", "segments": v_segments})
    tracks.append({"attribute": 0, "flag": 0, "id": uid(), "is_default_name": False, "name": "LOWER THIRDS · EDIT HERE", "type": "text", "segments": l_segments})
    hp = text_material(headp_t, HEAD_PRIMARY, 16.0); hs = text_material(heads_t, HEAD_SECONDARY, 6.0)
    tp = text_material(headp_t, TAIL_PRIMARY, 11.0); ts = text_material(heads_t, TAIL_SECONDARY, 5.0)
    tracks.append({"attribute": 0, "flag": 0, "id": uid(), "is_default_name": False, "name": "HEAD/TAIL PRIMARY", "type": "text",
                   "segments": [tseg(hp, 0, int(HEAD * US), headp_seg_t), tseg(tp, tail_start, tail_dur, tailp_seg_t)]})
    tracks.append({"attribute": 0, "flag": 0, "id": uid(), "is_default_name": False, "name": "HEAD/TAIL SECONDARY", "type": "text",
                   "segments": [tseg(hs, 0, int(HEAD * US), heads_seg_t), tseg(ts, tail_start, tail_dur, tails_seg_t)]})
    # ---- BGM ----
    am = json.loads(json.dumps(amat_t)); aid = uid(); bdur = audio_dur(BGM)
    am.update({"id": aid, "local_material_id": aid, "music_id": aid, "name": BGM.name, "path": str(BGM), "duration": bdur})
    mats["audios"].append(am)
    a = json.loads(json.dumps(aseg_t)); sp = speed()
    a.update({"id": uid(), "material_id": aid, "target_timerange": {"start": 0, "duration": min(total, bdur)},
              "source_timerange": {"start": 0, "duration": min(total, bdur)}, "volume": 0.13, "extra_material_refs": [sp["id"]]})
    tracks.append({"attribute": 0, "flag": 0, "id": uid(), "is_default_name": False, "name": "BGM", "type": "audio", "segments": [a]})

    t["tracks"] = tracks; t["duration"] = total
    t["id"] = str(uuid.uuid4()).upper(); meta["draft_id"] = str(uuid.uuid4()).upper()
    OUT.mkdir(parents=True)
    dump(OUT / "draft_content.json", t); dump(OUT / "draft_meta_info.json", meta)
    print(OUT, f"total {total/US:.2f}s", f"segments {len(CUTS)}")

if __name__ == "__main__":
    main()
