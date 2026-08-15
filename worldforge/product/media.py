from __future__ import annotations
import json,subprocess
from pathlib import Path
from PIL import Image
def probe_media(path,mime):
 path=Path(path);meta={'kind':'file'}
 if mime.startswith('image/'):
  try:
   with Image.open(path) as im:meta.update({'kind':'image','width':im.width,'height':im.height,'mode':im.mode})
  except Exception:meta['kind']='image'
  return meta
 if mime.startswith(('video/','audio/')):
  try:
   cp=subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_format','-show_streams',str(path)],capture_output=True,text=True,timeout=20);d=json.loads(cp.stdout or '{}');fmt=d.get('format',{});streams=d.get('streams',[]);meta.update({'kind':'video' if mime.startswith('video/') else 'audio','duration':round(float(fmt.get('duration',0) or 0),2),'bit_rate':int(float(fmt.get('bit_rate',0) or 0))})
   for s in streams:
    if s.get('codec_type')=='video':meta.update({'width':s.get('width'),'height':s.get('height'),'fps':s.get('avg_frame_rate')})
    if s.get('codec_type')=='audio':meta.update({'sample_rate':s.get('sample_rate'),'channels':s.get('channels')})
  except Exception:meta['kind']='video' if mime.startswith('video/') else 'audio'
  return meta
 if mime in {'application/json','text/plain','text/csv','application/xml'} or mime.startswith('text/'):
  try:txt=path.read_text(encoding='utf-8',errors='ignore');meta.update({'kind':'text','chars':len(txt),'lines':txt.count('\n')+1,'preview':txt[:1000]})
  except Exception:meta['kind']='text'
 return meta
def extract_video_frames(path,out_dir,count=3):
 path=Path(path);out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
 try:
  dur=float(probe_media(path,'video/mp4').get('duration',0) or 0)
  if dur<=0:return []
  rows=[]
  for i,t in enumerate([dur*(i+1)/(count+1) for i in range(count)],1):
   dest=out_dir/f'frame_{i}.jpg';subprocess.run(['ffmpeg','-y','-ss',str(t),'-i',str(path),'-frames:v','1','-vf',"scale='min(960,iw)':-2",str(dest)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
   if dest.exists():rows.append(str(dest))
  return rows
 except Exception:return []
