
from __future__ import annotations
from collections import defaultdict
from datetime import date
from typing import Any
import hashlib

INCOME_CATS = {"sales","revenue","services","interest","other_income"}

def tx_id(tx: dict) -> str:
    raw = "|".join(str(tx.get(k,"")) for k in ("date","amount","currency","description","counterparty"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

class AccountantEngine:
    def __init__(self, store):
        self.s = store

    def import_transactions(self, rows: list[dict]):
        added=0; duplicates=0
        for row in rows:
            item = {
                "id": row.get("id") or tx_id(row),
                "date": row["date"], "amount": float(row["amount"]),
                "currency": row.get("currency","CZK"),
                "description": row.get("description",""),
                "counterparty": row.get("counterparty",""),
                "category": row.get("category") or self.auto_category(row),
                "document_id": row.get("document_id"),
            }
            try: self.s.kv_get("transactions", item["id"]); duplicates += 1
            except KeyError: self.s.kv_put("transactions", item["id"], item); added += 1
        self.s.audit("accounting.transactions_imported", data={"added":added,"duplicates":duplicates})
        return {"added":added,"duplicates":duplicates}

    def auto_category(self,row):
        t=(str(row.get("description",""))+" "+str(row.get("counterparty",""))).lower()
        rules=[("rent",["rent","nájem","najem"]),("salary",["salary","mzda","payroll"]),
               ("software",["software","hosting","openai","github","cloud"]),
               ("marketing",["ads","advert","marketing","meta","google"]),
               ("sales",["sale","invoice paid","tržba","trzba"]),
               ("bank_fees",["fee","poplatek"])]
        for cat,words in rules:
            if any(w in t for w in words): return cat
        return "sales" if float(row.get("amount",0))>0 else "uncategorized"

    def list_transactions(self):
        return [x["value"] for x in self.s.kv_list("transactions")]

    def pnl(self, start=None, end=None):
        txs=[t for t in self.list_transactions() if (not start or t["date"]>=start) and (not end or t["date"]<=end)]
        income=sum(t["amount"] for t in txs if t["amount"]>0)
        expenses=-sum(t["amount"] for t in txs if t["amount"]<0)
        bycat=defaultdict(float)
        for t in txs: bycat[t["category"]]+=t["amount"]
        return {"start":start,"end":end,"income":round(income,2),"expenses":round(expenses,2),
                "profit":round(income-expenses,2),"margin_pct":round((income-expenses)/income*100,2) if income else None,
                "by_category":dict(sorted(bycat.items()))}

    def cashflow(self):
        months=defaultdict(float)
        for t in self.list_transactions(): months[t["date"][:7]] += t["amount"]
        return {"months":[{"month":m,"net":round(v,2)} for m,v in sorted(months.items())]}

    def health_score(self):
        p=self.pnl(); score=50; reasons=[]
        if p["profit"]>0: score+=20; reasons.append("profitable")
        else: score-=20; reasons.append("loss-making")
        unc=sum(1 for t in self.list_transactions() if t["category"]=="uncategorized")
        total=max(1,len(self.list_transactions()))
        if unc/total<0.1: score+=10
        else: reasons.append("many uncategorized transactions")
        if p["margin_pct"] is not None:
            if p["margin_pct"]>=20: score+=15
            elif p["margin_pct"]<5: score-=10
        score=max(0,min(100,score))
        return {"score":score,"reasons":reasons,"basis":"local ledger heuristics, not tax/accounting advice"}

    def owner_digest(self):
        p=self.pnl(); h=self.health_score()
        biggest=sorted([t for t in self.list_transactions() if t["amount"]<0],key=lambda x:x["amount"])[:5]
        return {"pnl":p,"health":h,"largest_expenses":biggest,
                "summary":f"Income {p['income']:.2f}; expenses {p['expenses']:.2f}; profit {p['profit']:.2f}; health {h['score']}/100."}

    def what_if(self, monthly_revenue_change=0.0, monthly_cost_change=0.0):
        p=self.pnl()
        projected_income=p["income"]+float(monthly_revenue_change)
        projected_expenses=p["expenses"]+float(monthly_cost_change)
        return {"projected_income":round(projected_income,2),"projected_expenses":round(projected_expenses,2),
                "projected_profit":round(projected_income-projected_expenses,2)}

    def reconcile(self, documents:list[dict]):
        txs=self.list_transactions(); matches=[]; unmatched=[]
        for d in documents:
            amount=float(d.get("amount",0)); dt=d.get("date")
            candidates=[t for t in txs if abs(abs(t["amount"])-abs(amount))<0.01 and (not dt or t["date"]==dt)]
            if len(candidates)==1: matches.append({"document":d,"transaction":candidates[0],"confidence":0.95})
            else: unmatched.append(d)
        return {"matches":matches,"unmatched":unmatched}


    def import_csv_text(self, text: str, delimiter=","):
        import csv, io
        reader=csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows=[]
        for r in reader:
            # tolerant aliases
            amount=r.get("amount") or r.get("Amount") or r.get("částka") or r.get("castka")
            dt=r.get("date") or r.get("Date") or r.get("datum")
            if amount is None or dt is None: 
                continue
            try: amount=float(str(amount).replace(" ","").replace(",","."))
            except ValueError: continue
            rows.append({
                "date":str(dt)[:10], "amount":amount,
                "currency":r.get("currency") or r.get("Currency") or "CZK",
                "description":r.get("description") or r.get("Description") or r.get("message") or "",
                "counterparty":r.get("counterparty") or r.get("Counterparty") or r.get("name") or ""
            })
        return self.import_transactions(rows)

    def anomalies(self):
        txs=self.list_transactions()
        if not txs: return {"anomalies":[]}
        expenses=[abs(t["amount"]) for t in txs if t["amount"]<0]
        if not expenses:return {"anomalies":[]}
        avg=sum(expenses)/len(expenses)
        threshold=max(avg*3, 10000)
        out=[{"type":"large_expense","transaction":t,"threshold":round(threshold,2)}
             for t in txs if t["amount"]<0 and abs(t["amount"])>=threshold]
        return {"anomalies":out}

    def period_compare(self, a_start, a_end, b_start, b_end):
        a=self.pnl(a_start,a_end); b=self.pnl(b_start,b_end)
        def pct(x,y):
            return None if y==0 else round((x-y)/abs(y)*100,2)
        return {"current":a,"previous":b,"delta":{
            "income_pct":pct(a["income"],b["income"]),
            "expenses_pct":pct(a["expenses"],b["expenses"]),
            "profit_pct":pct(a["profit"],b["profit"])
        }}

    def explain_cash(self):
        p=self.pnl()
        by=sorted(p["by_category"].items(), key=lambda kv: abs(kv[1]), reverse=True)
        negatives=[{"category":k,"net":v} for k,v in by if v<0][:5]
        positives=[{"category":k,"net":v} for k,v in by if v>0][:5]
        return {"headline":self.owner_digest()["summary"],"top_outflows":negatives,"top_inflows":positives,
                "anomalies":self.anomalies()["anomalies"]}
