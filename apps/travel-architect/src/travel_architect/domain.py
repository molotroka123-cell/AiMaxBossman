
from __future__ import annotations
import uuid
from datetime import date, timedelta

class TravelEngine:
    def __init__(self,store):self.s=store
    def create_trip(self,req):
        tid=str(uuid.uuid4()); item={"id":tid,**req}; self.s.kv_put("trips",tid,item);return item
    def add_offer(self,trip_id,offer):
        self.s.kv_get("trips",trip_id)
        oid=str(uuid.uuid4()); fees=offer.get("fees",{})
        base=float(offer.get("base_price",0)); extras=sum(float(v or 0) for v in fees.values())
        item={"id":oid,"trip_id":trip_id,**offer,"true_price":round(base+extras,2)}
        self.s.kv_put("offers",oid,item);return item
    def offers(self,trip_id):
        return [x["value"] for x in self.s.kv_list("offers") if x["value"]["trip_id"]==trip_id]
    def compare(self,trip_id):
        offers=self.offers(trip_id)
        ranked=sorted(offers,key=lambda x:(x["true_price"],-float(x.get("quality_score",0))))
        return {"offers":ranked,"best":ranked[0] if ranked else None}
    def date_candidates(self,start,end,flex_days=3,durations=None):
        s=date.fromisoformat(start); e=date.fromisoformat(end); durations=durations or [(e-s).days]
        rows=[]
        for shift in range(-flex_days,flex_days+1):
            for dur in durations:
                ds=s+timedelta(days=shift); rows.append({"start":ds.isoformat(),"end":(ds+timedelta(days=int(dur))).isoformat(),"duration":int(dur)})
        return rows
    def package_arbitrage(self,trip_id):
        offers=self.offers(trip_id)
        packages=[o for o in offers if o.get("kind")=="package"]; diy=[o for o in offers if o.get("kind")=="diy"]
        bp=min(packages,key=lambda x:x["true_price"]) if packages else None
        bd=min(diy,key=lambda x:x["true_price"]) if diy else None
        saving=None
        if bp and bd:saving=round(bp["true_price"]-bd["true_price"],2)
        return {"best_package":bp,"best_diy":bd,"diy_saves_vs_package":saving}
    def search_task(self,trip_id):
        trip=self.s.kv_get("trips",trip_id)
        return {"task_type":"travel_browser_search","trip":trip,
          "instructions":["compare package and DIY","capture full mandatory fees","respect robots/terms and authenticated boundaries","do not purchase"]}
    def create_watch(self,trip_id,max_price):
        wid=str(uuid.uuid4()); w={"id":wid,"trip_id":trip_id,"max_price":float(max_price),"active":True}
        self.s.kv_put("watches",wid,w);return w
    def evaluate_watches(self):
        hits=[]
        for row in self.s.kv_list("watches"):
            w=row["value"]
            if not w["active"]:continue
            best=self.compare(w["trip_id"])["best"]
            if best and best["true_price"]<=w["max_price"]: hits.append({"watch":w,"offer":best})
        return {"hits":hits}


    def score_offer(self,offer,trip=None):
        price=float(offer["true_price"]); quality=float(offer.get("quality_score",0))
        stops=float(offer.get("metadata",{}).get("stops",0))
        duration=float(offer.get("metadata",{}).get("travel_hours",0))
        risk=float(offer.get("metadata",{}).get("risk_score",0))
        # Higher is better. Price normalization intentionally simple/local.
        score=quality*20 - price/100 - stops*5 - max(duration-6,0)*0.5 - risk*10
        return round(score,3)

    def smart_compare(self,trip_id):
        trip=self.s.kv_get("trips",trip_id); rows=[]
        for o in self.offers(trip_id):
            x=dict(o); x["smart_score"]=self.score_offer(x,trip); rows.append(x)
        rows.sort(key=lambda x:x["smart_score"],reverse=True)
        return {"ranked":rows,"recommended":rows[0] if rows else None}

    def itinerary(self,trip_id):
        trip=self.s.kv_get("trips",trip_id); best=self.smart_compare(trip_id)["recommended"]
        days=(date.fromisoformat(trip["end"])-date.fromisoformat(trip["start"])).days
        return {"trip":trip,"selected_offer":best,"days":[{"day":i+1,"date":(date.fromisoformat(trip["start"])+timedelta(days=i)).isoformat(),
                "plan":[],"notes":[]} for i in range(max(days,1))]}

    def watch_snapshot(self):
        return {"watches":[x["value"] for x in self.s.kv_list("watches")],"evaluation":self.evaluate_watches()}

    def constraint_check(self,trip_id,offer_id):
        trip=self.s.kv_get("trips",trip_id)
        offer=self.s.kv_get("offers",offer_id)
        failures=[]
        if trip.get("budget") is not None and offer["true_price"]>float(trip["budget"]): failures.append("over_budget")
        constraints=trip.get("constraints") or {}
        max_stops=constraints.get("max_stops")
        if max_stops is not None and float(offer.get("metadata",{}).get("stops",0))>float(max_stops): failures.append("too_many_stops")
        return {"pass":not failures,"failures":failures}
