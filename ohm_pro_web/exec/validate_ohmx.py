"""Replay a past period through the executor (SHADOW) and the paper engine side by side."""
import sys, json, numpy as np, pandas as pd, psycopg2
sys.path.insert(0,'/root/ohm_pro_web'); sys.path.insert(0,'/root/ohm_pro_web/exec'); sys.path.insert(0,'/root/trade-execution-webhook')
import ohmpro as O, compute as C, ohmx as X
X.tg=lambda *a,**k: None; X.OLOG='/tmp/ohmx_validate.jsonl'
D0,D1,CAP=sys.argv[1],sys.argv[2],float(sys.argv[3])
conn=psycopg2.connect(**O.DB); cal=O.complete_dates(conn)
i0=int(np.searchsorted(cal,np.datetime64(D0))); i1=int(np.searchsorted(cal,np.datetime64(D1),'right'))-1
mkt,cands,bars=O.scan(conn,cal,i0,i1,load_from=pd.Timestamp(cal[i0])-pd.Timedelta(days=760),keep_bars=True)
O.earnings(conn,cands,cal)
byday={}
for c in cands:
    c['block']=O.gates(c,mkt) or ('stop more than 8% away' if (c['buy']-c['stop'])/c['buy']>O.MAXSTOP else '')
    byday.setdefault(c['i'],[]).append(c)
B=O.Bars(bars); DS=[str(pd.Timestamp(x).date()) for x in cal]
def gb(sym,since):
    x=B.b.get(sym)
    if x is None: return pd.DataFrame(columns=['d','o','h','l','c','e21'])
    df=pd.DataFrame(dict(d=[DS[int(k)] for k in x['i']],o=x['o'],h=x['h'],l=x['l'],c=x['c'],e21=x['e21']))
    return df[df.d>=str(since)].reset_index(drop=True)
TK={}
def tk(s):
    if s not in TK: TK[s]=C.tick_of(s)
    return TK[s]
b=X.new_book(CAP,'SHADOW',DS[i0]); paper=O.new_book(CAP); prev=None
for i in range(i0,i1+1):
    d=DS[i]; on=int(mkt['reg'][i]) not in (1,2) and bool(mkt['jr_on'][i])
    X.shadow_execute(b,d,gb); X.mark(b,d,gb)
    X.build_plan(b,d,[dict(sym=c['sym'],buy=c['buy'],stop=c['stop'],block=c['block'],r252=c['r252'],lpct=c['lpct'],typ=c['typ']) for c in byday.get(i,[])],on,gb,tk)
    O.step(paper,i,d,B,[c for c in byday.get(i,[])],mkt,prev,whole=True); prev=d
ex=[(x['sym'],x['fill_date']) for x in b['closed']]+[(s,p['fill_date']) for s,p in b['positions'].items()]
pa=[(x['sym'],x['fill_date']) for x in paper['closed']]+[(s,p['fill_date']) for s,p in paper['positions'].items()]
se,sp=set(ex),set(pa)
print('period',DS[i0],'->',DS[i1],'| buys-ON days',sum(1 for i in range(i0,i1+1) if int(mkt['reg'][i]) not in (1,2) and bool(mkt['jr_on'][i])))
print('executor: equity %.0f, trades %d, open %d'%(b['eq'],len(b['closed']),len(b['positions'])))
print('paper   : equity %.0f, trades %d, open %d'%(paper['eq'],len(paper['closed']),len(paper['positions'])))
print('same (stock, fill date):',len(se&sp),'| only executor:',sorted(se-sp)[:10],'| only paper:',sorted(sp-se)[:10])
pc={(x['sym'],x['fill_date']):x for x in paper['closed']}
diff=[(x['sym'],x['fill_date'],x['exit_date'],pc[(x['sym'],x['fill_date'])]['exit_date']) for x in b['closed'] if (x['sym'],x['fill_date']) in pc and pc[(x['sym'],x['fill_date'])]['exit_date']!=x['exit_date']]
print('same trade, different exit date:',len(diff),diff[:5])
