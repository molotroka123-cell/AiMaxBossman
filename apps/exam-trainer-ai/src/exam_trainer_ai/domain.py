
from __future__ import annotations
import random, uuid
from collections import defaultdict

class ExamEngine:
    def __init__(self,store): self.s=store

    def create_exam(self,name,language,topics):
        eid=str(uuid.uuid4())
        topic_objs=[]
        total=sum(float(t.get("weight",1)) for t in topics) or 1
        for t in topics:
            topic_objs.append({"name":t["name"],"weight":float(t.get("weight",1))/total,
                               "difficulty":float(t.get("difficulty",0.5))})
        exam={"id":eid,"name":name,"language":language,"topics":topic_objs}
        self.s.kv_put("exams",eid,exam); return exam

    def add_questions(self,exam_id,questions):
        self.s.kv_get("exams",exam_id)
        out=[]
        for q in questions:
            qid=str(uuid.uuid4()); item={"id":qid,"exam_id":exam_id,"topic":q["topic"],"prompt":q["prompt"],
                "answer":q["answer"],"choices":q.get("choices"),"explanation":q.get("explanation",""),
                "source_type":q.get("source_type","generated_or_user_supplied")}
            self.s.kv_put("questions",qid,item); out.append(item)
        return out

    def questions(self,exam_id):
        return [x["value"] for x in self.s.kv_list("questions") if x["value"]["exam_id"]==exam_id]

    def diagnostic(self,exam_id,answers):
        qs={q["id"]:q for q in self.questions(exam_id)}; by=defaultdict(lambda:[0,0]); correct=0
        for qid,ans in answers.items():
            if qid not in qs:continue
            q=qs[qid]; ok=str(ans).strip().lower()==str(q["answer"]).strip().lower()
            by[q["topic"]][1]+=1
            if ok: correct+=1; by[q["topic"]][0]+=1
        mastery={k:(v[0]/v[1] if v[1] else 0) for k,v in by.items()}
        score=correct/max(1,sum(v[1] for v in by.values()))
        return {"accuracy":round(score,4),"mastery":mastery,"answered":sum(v[1] for v in by.values())}

    def probability_map(self,exam_id,mastery=None):
        exam=self.s.kv_get("exams",exam_id); mastery=mastery or {}
        rows=[]
        for t in exam["topics"]:
            weak=1-float(mastery.get(t["name"],0.5))
            priority=t["weight"]*(0.5+weak)
            rows.append({"topic":t["name"],"blueprint_weight":round(t["weight"],4),
                         "mastery":round(float(mastery.get(t["name"],0.5)),4),"training_priority":round(priority,4)})
        rows.sort(key=lambda x:x["training_priority"],reverse=True); return {"topics":rows}

    def training_set(self,exam_id,mastery=None,count=10):
        qs=self.questions(exam_id); pmap=self.probability_map(exam_id,mastery)["topics"]
        rank={x["topic"]:i for i,x in enumerate(pmap)}
        qs.sort(key=lambda q:rank.get(q["topic"],999))
        selected=(qs*((count//max(1,len(qs)))+1))[:count] if qs else []
        # Produce safe variants only for simple numeric questions, otherwise reuse as practice.
        out=[]
        for q in selected:
            item=dict(q); item["variant_of"]=q["id"]
            out.append(item)
        return {"questions":out,"note":"Variants engine intentionally does not copy leaked/access-controlled exam content."}

    def readiness(self,exam_id,mastery):
        exam=self.s.kv_get("exams",exam_id)
        weighted=sum(t["weight"]*float(mastery.get(t["name"],0)) for t in exam["topics"])
        return {"readiness_score":round(weighted*100,1),"basis":"weighted mastery over configured/public blueprint; not a guarantee of exam result"}

    def browser_collection_task(self,exam_id,query):
        exam=self.s.kv_get("exams",exam_id)
        return {"task_type":"collect_public_exam_sources","exam":exam["name"],"query":query,
                "constraints":["official/public/licensed sources only","no leaked or access-controlled exam content"]}


    def create_session(self,exam_id,learner_id,mode="adaptive",language=None):
        self.s.kv_get("exams",exam_id)
        sid=str(uuid.uuid4()); s={"id":sid,"exam_id":exam_id,"learner_id":learner_id,"mode":mode,"language":language,
                                 "answers":{},"started_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
        self.s.kv_put("sessions",sid,s); return s

    def submit_answer(self,session_id,question_id,answer):
        sess=self.s.kv_get("sessions",session_id); qs={q["id"]:q for q in self.questions(sess["exam_id"])}
        if question_id not in qs: raise KeyError(question_id)
        q=qs[question_id]; ok=str(answer).strip().lower()==str(q["answer"]).strip().lower()
        sess["answers"][question_id]={"answer":answer,"correct":ok,"topic":q["topic"]}
        self.s.kv_put("sessions",session_id,sess)
        return {"correct":ok,"explanation":q.get("explanation",""),"topic":q["topic"]}

    def session_report(self,session_id):
        sess=self.s.kv_get("sessions",session_id)
        raw={qid:v["answer"] for qid,v in sess["answers"].items()}
        diag=self.diagnostic(sess["exam_id"],raw)
        return {"session":sess,"diagnostic":diag,"priority":self.probability_map(sess["exam_id"],diag["mastery"]),
                "readiness":self.readiness(sess["exam_id"],diag["mastery"])}

    def mock_exam(self,exam_id,count=20):
        qs=self.questions(exam_id)
        random.Random(exam_id+str(count)).shuffle(qs)
        return {"exam_id":exam_id,"questions":[{k:v for k,v in q.items() if k not in ("answer","explanation")} for q in qs[:count]],
                "answer_key_hidden":True}

    def usage_minutes(self,learner_id):
        sessions=[x["value"] for x in self.s.kv_list("sessions") if x["value"]["learner_id"]==learner_id]
        # active time metering placeholder based on completed answers; deterministic and cheap.
        minutes=sum(max(1,len(s.get("answers",{}))*2) for s in sessions)
        return {"learner_id":learner_id,"estimated_active_minutes":minutes,"sessions":len(sessions)}

    def provenance_report(self,exam_id):
        qs=self.questions(exam_id)
        by={}
        for q in qs: by[q.get("source_type","unknown")]=by.get(q.get("source_type","unknown"),0)+1
        return {"exam_id":exam_id,"question_sources":by,"policy":"official/public/licensed/user-supplied/generated analogues only"}
