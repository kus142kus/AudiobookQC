"""CI-only download: model is bundled into the distributed application.

Never imported from app runtime. Fails closed on incomplete snapshot.
"""
from pathlib import Path
from huggingface_hub import snapshot_download

root = Path('models/medium')
root.mkdir(parents=True, exist_ok=True)
snapshot_download(repo_id='Systran/faster-whisper-medium', local_dir=str(root),
                  allow_patterns=['model.bin','config.json','tokenizer.json','preprocessor_config.json',
                                  'vocabulary.*','LICENSE','README.md'])
for name in ('model.bin','config.json','tokenizer.json'):
    file = root/name
    if not file.is_file() or file.stat().st_size == 0:
        raise RuntimeError(f'Model is incomplete: {name}')
if (root/'model.bin').stat().st_size < 100_000_000:
    raise RuntimeError('Model weights are suspiciously small')
print('Offline model bundled, bytes:',(root/'model.bin').stat().st_size)

# Download independent GigaAM model during CI; never at application runtime.
import hashlib
import urllib.request
import os

GIGA_BASE='https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM'
GIGA_DIR=Path('models/gigaam')
GIGA_DIR.mkdir(parents=True,exist_ok=True)
for filename in ('v3_e2e_ctc.ckpt','v3_e2e_ctc_tokenizer.model'):
    target=GIGA_DIR/filename
    if not target.is_file() or target.stat().st_size==0:
        temporary=target.with_suffix(target.suffix+'.part')
        urllib.request.urlretrieve(f'{GIGA_BASE}/{filename}',str(temporary))
        temporary.replace(target)
if (GIGA_DIR/'v3_e2e_ctc.ckpt').stat().st_size<100_000_000:
    raise RuntimeError('GigaAM checkpoint is suspiciously small')
hash_md5=hashlib.md5()
with (GIGA_DIR/'v3_e2e_ctc.ckpt').open('rb') as f:
    for block in iter(lambda:f.read(1024*1024),b''):
        hash_md5.update(block)
if hash_md5.hexdigest() != '367074d6498f426d960b25f49531cf68':
    raise RuntimeError('GigaAM checksum mismatch')
if (GIGA_DIR/'v3_e2e_ctc_tokenizer.model').stat().st_size<1000:
    raise RuntimeError('GigaAM tokenizer incomplete')
print('Independent GigaAM v3 weights included and checked')
