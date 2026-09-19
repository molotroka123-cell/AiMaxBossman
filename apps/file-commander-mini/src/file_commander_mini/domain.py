
from __future__ import annotations
from pathlib import Path
import hashlib, os, uuid, json, re
import threading
from .safety import FilePolicy, identity, require_identity, move_no_replace, mkdir_no_follow, rmdir_verified

_MUTATION_LOCK = threading.RLock()

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
        self.policy = FilePolicy(store.dir)

    def allowed(self,p:Path):
        return self.policy.allowed(p)

    def scan(self,root:str,max_files=20000):
        base=self.allowed(Path(root)); out=[]; total=0
        if not base.exists(): raise FileNotFoundError("workspace does not exist")
        if not base.is_dir(): raise ValueError("workspace must be a directory")
        denied=0
        for directory, dirs, names in os.walk(base, followlinks=False):
            kept=[]
            for name in dirs:
                try: self.allowed(Path(directory)/name)
                except PermissionError: denied+=1
                else: kept.append(name)
            dirs[:]=kept
            for name in names:
                if len(out)>=max_files: break
                try:
                    p=self.allowed(Path(directory)/name)
                    if not p.is_file(): continue
                    st=p.stat()
                    if st.st_nlink > 1: denied+=1; continue
                    out.append({"path":str(p),"name":p.name,"size":st.st_size,"mtime":st.st_mtime,"ext":p.suffix.lower()}); total+=st.st_size
                except (PermissionError,FileNotFoundError): denied+=1
            if len(out)>=max_files: break
        return {"root":str(base),"count":len(out),"bytes":total,"files":out,
                "excluded":denied,"truncated":len(out)>=max_files}

    def duplicates(self,root:str):
        data=self.scan(root); sizes={}
        for f in data["files"]:
            if f["size"]>0:sizes.setdefault(f["size"],[]).append(f)
        groups=[]
        for size,items in sizes.items():
            if len(items)<2: continue
            byhash={}
            for it in items:
                try: h=identity(self.allowed(Path(it["path"])))['sha256']
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
        return self._preview(ops)

    def _preview(self, ops):
        if len(ops)>256: raise ValueError("a batch is limited to 256 files; use a smaller folder")
        measured=[]; sources=set(); targets=set()
        for op in ops:
            if not isinstance(op,dict) or op.get("op")!="move": raise ValueError("only move supported")
            src=self.allowed(Path(op["src"])); dst=self.allowed(Path(op["dst"]))
            if src==dst or src in sources or dst in targets: raise ValueError("duplicate or self move")
            if not src.is_file(): raise FileNotFoundError("source file does not exist")
            if src.stat().st_nlink > 1: raise PermissionError("hard-linked source files are prohibited")
            if dst.exists(): raise FileExistsError("destination already exists")
            expected=identity(src)
            if op.get("source_identity") is not None and op["source_identity"]!=expected:
                raise ValueError("source changed since preview; refresh the plan")
            measured.append({"op":"move","src":str(src),"dst":str(dst),"source_identity":expected})
            sources.add(src); targets.add(dst)
        if sources & targets: raise ValueError("dependent moves require separate previews")
        digest=hashlib.sha256(json.dumps(measured,sort_keys=True).encode()).hexdigest()
        self.s.kv_put("plans",digest,measured)
        return {"status":"PREVIEW_ONLY","operations":measured,"count":len(measured),"plan_id":digest}

    def apply(self,ops:list[dict],approve:bool=False):
        if not approve: return self._preview(ops)
        with _MUTATION_LOCK:
            return self._apply(ops)

    def _apply(self,ops):
        digest=hashlib.sha256(json.dumps(ops,sort_keys=True).encode()).hexdigest()
        try: planned=self.s.kv_get("plans",digest)
        except KeyError: raise ValueError("refresh preview before approving these exact operations")
        if planned!=ops: raise ValueError("plan was replaced")
        for item in self.s.kv_list("batches"):
            prior=item['value']
            if prior["status"] in {"APPLYING","ROLLING_BACK","UNKNOWN"}:
                raise ValueError(f"RECOVERY_REQUIRED: undo batch {prior['batch_id']} before applying again")
            if prior["plan_id"]==digest and prior["status"]=="APPLIED":
                for op in prior["operations"]:
                    if Path(op["src"]).exists(): raise ValueError("applied source changed; refresh state")
                    require_identity(self.allowed(Path(op["dst"])),op["source_identity"])
                return {"status":"APPLIED","batch_id":prior["batch_id"],"undo_operations":len(ops),"already_applied":True}
        # Validate the ENTIRE batch before any directory or file mutation.
        for op in ops:
            src=self.allowed(Path(op["src"])); dst=self.allowed(Path(op["dst"]))
            require_identity(src,op["source_identity"])
            if dst.exists(): raise FileExistsError("destination already exists")
            parent=dst.parent
            while not parent.exists(): parent=parent.parent
            if not parent.stat().st_mode & 0o222 or not src.parent.stat().st_mode & 0o222:
                raise PermissionError("source or destination parent is read-only")
            if src.stat().st_dev!=parent.stat().st_dev:
                raise ValueError("cross-device moves are not supported; choose a folder on the same volume")
        batch=str(uuid.uuid4())
        record={"batch_id":batch,"plan_id":digest,"status":"APPLYING","operations":ops,"created_dirs":[]}
        self.s.kv_put("batches",batch,record)  # write-ahead: survives a crash at any effect
        try:
            for op in ops:
                src=self.allowed(Path(op["src"])); dst=self.allowed(Path(op["dst"]))
                missing=[]; parent=dst.parent
                while not parent.exists(): missing.append(parent); parent=parent.parent
                for directory in reversed(missing):
                    self.allowed(directory)
                    intent={'path':str(directory),'identity':None}
                    record['created_dirs'].append(intent)
                    self.s.kv_put("batches",batch,record)
                    intent['identity']=mkdir_no_follow(directory,self.policy)
                    self.s.kv_put("batches",batch,record)
                move_no_replace(src,dst,op["source_identity"],self.policy)
                # Fresh Bossman-side state, not the move function's claim.
                if src.exists(): raise ValueError("source remained after move")
                require_identity(self.allowed(dst),op["source_identity"])
            record['status']='APPLIED'; self.s.kv_put("batches",batch,record)
            self.s.audit("files.batch_applied",batch,{"count":len(ops)})
            return {"status":"APPLIED","batch_id":batch,"undo_operations":len(ops)}
        except (OSError,ValueError) as exc:
            record['error']=str(exc)
            result=self._rollback(record)
            raise ValueError(f"{result['status']}: batch {batch}; {exc}") from exc

    def _rollback(self,record):
        record['status']='ROLLING_BACK'; self.s.kv_put("batches",record['batch_id'],record)
        errors=[]
        for op in reversed(record['operations']):
            try:
                src=self.allowed(Path(op['src'])); dst=self.allowed(Path(op['dst']))
                if src.exists() and not dst.exists():
                    require_identity(src,op['source_identity']); continue
                if not src.exists() and dst.exists():
                    move_no_replace(dst,src,op['source_identity'],self.policy); continue
                if src.exists() and dst.exists():
                    require_identity(src,op['source_identity']); require_identity(dst,op['source_identity'])
                    # The two identities include device+inode, so this is the
                    # original no-replace link interrupted before source unlink.
                    dst.unlink(); continue
                raise ValueError("both source and destination are missing")
            except (OSError,ValueError) as exc: errors.append(str(exc))
        for directory in reversed(record.get('created_dirs',[])):
            name=directory['path'] if isinstance(directory,dict) else directory
            expected=directory.get('identity') if isinstance(directory,dict) else None
            try: rmdir_verified(Path(name),expected,self.policy)
            except FileNotFoundError: pass
            except ValueError as exc: errors.append(str(exc))
            except OSError as exc:
                if Path(name).exists() and any(Path(name).iterdir()): continue
                errors.append(str(exc))
        record['status']='UNKNOWN' if errors else 'ROLLED_BACK'
        record['recovery_errors']=errors
        self.s.kv_put('batches',record['batch_id'],record)
        self.s.audit('files.batch_'+record['status'].lower(),record['batch_id'],{'errors':errors})
        return {'status':record['status'],'batch_id':record['batch_id'],'errors':errors}

    def undo(self,batch_id:str,approve:bool=False):
        with _MUTATION_LOCK:
            record=self.s.kv_get("batches",batch_id)
            if not approve:return {"status":"PREVIEW_ONLY","operations":record['operations']}
            if record['status']=='ROLLED_BACK': return {'status':'ROLLED_BACK','batch_id':batch_id,'already_undone':True}
            return self._rollback(record)


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
        return self._preview(ops)

    def save_rule(self,name,match_ext,target_dir):
        relative=Path(target_dir)
        if not target_dir or relative.is_absolute() or '..' in relative.parts or '\\' in target_dir:
            raise ValueError("rule target must be a relative folder inside the workspace")
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
        return self._preview(ops)

    def project_groups(self,root:str):
        data=self.scan(root)
        groups={}
        for f in data["files"]:
            stem=re.sub(r"([_-]?(final|copy|v\d+|\d{4}[-_]?\d{2}[-_]?\d{2}))+$","",Path(f["name"]).stem,flags=re.I).strip("_- ").lower()
            if len(stem)>=3: groups.setdefault(stem,[]).append(f["path"])
        return {"groups":[{"key":k,"paths":v} for k,v in groups.items() if len(v)>=2]}
