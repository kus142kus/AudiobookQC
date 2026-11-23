"""Cheap, exhaustive local import-contract preflight BEFORE model download.

Specifically catches missing desktop functions (the failure in build #15).
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from audiobook_qc.desktop import create_project,list_projects,delete_saved_project,ClipPlayer
from audiobook_qc.desktop_web import Bridge,bundled_html
from audiobook_qc import worker
from audiobook_qc.events import AudioEventDetector
from audiobook_qc.linguistics import annotate_term_issues,acoustic_stress_candidates,intonation_candidates
from audiobook_qc.acoustics import inspect_wav
from audiobook_qc.evaluation import score_findings
from audiobook_qc.report import generate_report

for name in ('get_projects','get_project','pick_file','new_project','resume','pause','delete',
             'review','open_reports','save_report','load_clip','toggle_play','seek','set_speed','shutdown'):
    assert callable(getattr(Bridge,name,None)),name
for name in ('load_job','save_job','model_worker','cut_audio','all_issues','refresh_reports'):
    assert callable(getattr(worker,name,None)),name
for name in ('get_project','delete_saved_project'):
    assert callable(getattr(Bridge(), 'delete' if name=='delete_saved_project' else name)),name
html=bundled_html()
for id in ('projectView','newView','glossary','issues','partsGrid','timeline','createBtn'):
    assert f'id="{id}"' in html,id
assert 'http://127.0.0.1' not in html
print('AUDIOBOOK_QC_SOURCE_API_CONTRACT_OK',flush=True)
