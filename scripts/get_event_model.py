"""Build-time only. Fetch LICENSED official YAMNet ONNX conversion & class map.

Never invoked by the installed application. Pin the exact repository revision,
compute hashes for runtime integrity, and retain the Apache-2.0 license.
"""
import hashlib
import json
from pathlib import Path
from huggingface_hub import snapshot_download

MODEL='audiomagic/yamnet-onnx'
REVISION='f25b741'
folder=Path('models/events')
folder.mkdir(parents=True,exist_ok=True)
snapshot_download(repo_id=MODEL,revision=REVISION,local_dir=folder,
    allow_patterns=['yamnet.onnx','yamnet_class_map.csv','LICENSE'],max_workers=2)
metadata={'model':MODEL,'revision':REVISION,'files':{}}
for name in ('yamnet.onnx','yamnet_class_map.csv'):
    file=folder/name
    if not file.exists() or file.stat().st_size==0:
        raise RuntimeError('Missing YAMNet model file: '+name)
    h=hashlib.sha256()
    with file.open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    metadata['files'][name]={'sha256':h.hexdigest(),'size':file.stat().st_size}
(folder/'manifest.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
print('AUDIOBOOK_QC_YAMNET_BUNDLE_OK',flush=True)
