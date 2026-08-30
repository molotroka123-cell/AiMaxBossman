
from __future__ import annotations
from pathlib import Path
import hashlib, os, shutil, uuid, json, re

def sha256_file(p:Path,chunk=1024*1024):
    h=hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b=f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()

class FileCommander:
    def __init__(self,store):
        self.s=store

    def allowed(self,p:Path):
        p=p.expanduser().resolve()
        roots=os.getenv("FILE_COMMANDER_ROOTS","").strip()
        if not roots: return p
        allowed=[Path(x).expanduser().resolve() for x in roots.split(os.pathsep) if x]
        if not any(p==r or r in p.parents for r in allowed): raise PermissionError("path outside FILE_COMMANDER_ROOTS")
        return p

    def scan(self,root:str,max_files=20000):
        base=self.allowed(Path(root)); out=[]; total=0
        for p in base.rglob("*"):
            if len(out)>=max_files: break
            if p.is_file() and not p.is_symlink():
                try:
                    st=p.stat(); out.append({"path":str(p),"name":p.name,"size":st.st_size,"mtime":st.st_mtime,"ext":p.suffix.lower()}); total+=st.st_size
                except OSError: pass
        return {"root":str(base),"count":len(out),"bytes":total,"files":out}

    def duplicates(self,root:str):
        data=self.scan(root); sizes={}
        for f in data["files"]:
            if f["size"]>0:sizes.setdefault(f["size"],[]).append(f)
        groups=[]
        for size,items in sizes.items():
            if len(items)<2: continue
            byhash={}
            for it in items:
                try: h=sha256_file(Path(it["path"]))
                except OSError: continue
                byhash.setdefault(h,[]).append(it["path"])
            groups.extend({"sha256":h,"size":size,"paths":ps} for h,ps in byhash.items() if len(ps)>1)
        return {"groups":groups,"reclaimable_bytes":sum(g["size"]*(len(g["paths"])-1) for g in groups)}

    def organize_plan(self,root:str):
        data=self.scan(root); ops=[]
        mapping={".pdf":"Documents/PDF",".docx":"Documents/Word",".xlsx":"Documents/Excel",".csv":"Documents/Data",
                 ".jpg":"Media/Images",".jpeg":"Media/Images",".png":"Media/Images",".webp":"Media/Images",
                 ".mp4":"Media/Video",".mov":"Media/Video",".stl":"3D",".3mf":"3D",".zip":"Archives"}
        base=Path(data["root"])
        for f in data["files"]:
            p=Path(f["path"])
            if p.parent != base: continue
            target_dir=mapping.get(f["ext"])
            if target_dir:
                dst=base/target_dir/p.name
                if dst!=p: ops.append({"op":"move","src":str(p),"dst":str(dst)})
        return {"operations":ops,"count":len(ops)}

    def apply(self,ops:list[dict],approve:bool=False):
        if not approve: return {"status":"PREVIEW_ONLY","operations":ops}
        batch=str(uuid.uuid4()); undo=[]
        for op in ops:
            if op.get("op")!="move": raise ValueError("only move supported")
            src=self.allowed(Path(op["src"])); dst=self.allowed(Path(op["dst"]))
            if not src.exists(): raise FileNotFoundError(src)
            dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists(): raise FileExistsError(dst)
            shutil.move(str(src),str(dst)); undo.append({"op":"move","src":str(dst),"dst":str(src)})
        self.s.kv_put("undo",batch,undo); self.s.audit("files.batch_applied",batch,{"count":len(ops)})
        return {"status":"APPLIED","batch_id":batch,"undo_operations":len(undo)}

    def undo(self,batch_id:str,approve:bool=False):
        ops=self.s.kv_get("undo",batch_id)
        if not approve:return {"status":"PREVIEW_ONLY","operations":ops}
        return self.apply(ops,True)


    def cleanup_summary(self,root:str):
        scan=self.scan(root)
        dups=self.duplicates(root)
        large=sorted(scan["files"],key=lambda x:x["size"],reverse=True)[:20]
        old=[f for f in scan["files"] if f["mtime"] < __import__("time").time()-180*86400]
        return {"count":scan["count"],"bytes":scan["bytes"],"duplicate_groups":len(dups["groups"]),
                "reclaimable_bytes":dups["reclaimable_bytes"],"large_files":large,"older_than_180d":len(old)}

    def rename_plan(self,root:str,pattern:str="{stem}",replace_spaces:bool=True):
        data=self.scan(root); ops=[]
        base=Path(data["root"])
        for f in data["files"]:
            p=Path(f["path"])
            if p.parent!=base: continue
            stem=p.stem.replace(" ","_") if replace_spaces else p.stem
            new=pattern.replace("{stem}",stem).replace("{ext}",p.suffix.lstrip("."))
            if "{ext}" not in pattern: new += p.suffix
            dst=p.with_name(new)
            if dst!=p: ops.append({"op":"move","src":str(p),"dst":str(dst)})
        return {"operations":ops,"count":len(ops)}

    def save_rule(self,name,match_ext,target_dir):
        rid=str(uuid.uuid4()); rule={"id":rid,"name":name,"match_ext":[x.lower() for x in match_ext],"target_dir":target_dir}
        self.s.kv_put("rules",rid,rule); return rule

    def rule_plan(self,root:str):
        base=Path(self.scan(root)["root"]); rules=[x["value"] for x in self.s.kv_list("rules")]; ops=[]
        for p in base.iterdir():
            if not p.is_file() or p.is_symlink(): continue
            for r in rules:
                if p.suffix.lower() in r["match_ext"]:
                    dst=base/r["target_dir"]/p.name
                    if dst!=p: ops.append({"op":"move","src":str(p),"dst":str(dst),"rule_id":r["id"]})
                    break
        return {"operations":ops,"count":len(ops)}

    def project_groups(self,root:str):
        data=self.scan(root)
        groups={}
        for f in data["files"]:
            stem=re.sub(r"([_-]?(final|copy|v\d+|\d{4}[-_]?\d{2}[-_]?\d{2}))+$","",Path(f["name"]).stem,flags=re.I).strip("_- ").lower()
            if len(stem)>=3: groups.setdefault(stem,[]).append(f["path"])
        return {"groups":[{"key":k,"paths":v} for k,v in groups.items() if len(v)>=2]}
