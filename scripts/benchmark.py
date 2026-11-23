"""Usage: python scripts/benchmark.py path/to/job-folder labels.csv

labels.csv: start,end,kind ; start/end in seconds; annotated by listening.
No network, no model needed. This is a measurement aid, not a validator.
"""
import csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from audiobook_qc.evaluation import score_findings

def main(job_dir,annotation):
    truth=[]
    with Path(annotation).open(encoding='utf-8-sig',newline='') as file:
        for row in csv.DictReader(file):
            truth.append({'kind':row['kind'],'start':float(row['start']),
                          'end':float(row.get('end') or row['start'])})
    findings=[]
    for f in sorted((Path(job_dir)/'parts').glob('part-*.json')):
        data=json.loads(f.read_text(encoding='utf-8'))
        findings.extend(data.get('issues',[]))
    result=score_findings(truth,findings)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('Usage: benchmark.py PROJECT_DIR labels.csv')
    main(*sys.argv[1:])
