import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from audiobook_qc import server, worker


class ApiTests(unittest.TestCase):
    def test_local_server_upload_create_project(self):
        import wave
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(worker,"ROOT",root), patch.object(server,"UPLOADS",root/"uploads"):
                server.UPLOADS.mkdir(parents=True, exist_ok=True)
                host=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
                t=threading.Thread(target=host.serve_forever,daemon=True);t.start()
                try:
                    wav=root/"short.wav"
                    with wave.open(str(wav),'wb') as w:
                        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
                        w.writeframes(b'\x00\x00'*16000)
                    payloads=[("audio","short.wav",wav.read_bytes()),
                              ("text","book.txt",("Здесь начинается наша история. "*15).encode())]
                    loaded={}
                    for kind,name,data in payloads:
                        conn=http.client.HTTPConnection("127.0.0.1",host.server_port)
                        conn.request("POST","/api/upload?kind="+kind, body=data,
                                     headers={"X-File-Name":name,"Content-Length":str(len(data))})
                        response=conn.getresponse();value=json.loads(response.read());conn.close()
                        self.assertEqual(response.status,200,value)
                        loaded[kind]=value
                    conn=http.client.HTTPConnection("127.0.0.1",host.server_port)
                    create=json.dumps({"audio":loaded["audio"],"text":loaded["text"],
                                       "model":"small","part_minutes":20})
                    conn.request("POST","/api/projects",body=create,
                                 headers={"Content-Type":"application/json"})
                    response=conn.getresponse();data=json.loads(response.read());conn.close()
                    self.assertEqual(response.status,201,data)
                    pid=data["id"]
                    job=worker.load_job(pid)
                    self.assertEqual(len(job["parts"]),1)
                    self.assertGreater(job["duration"],.9)
                    self.assertTrue((root/"projects"/pid/"inputs"/"audio.wav").exists())
                    self.assertTrue((root/"projects"/pid/"inputs"/"text.txt").exists())
                finally:
                    host.shutdown();host.server_close()


if __name__=="__main__":unittest.main()
