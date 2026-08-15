from __future__ import annotations
from pathlib import Path
class ObjectStorage:
 name='base'
 def put_bytes(self,key,data,content_type):raise NotImplementedError
 def put_file(self,key,source,content_type):return self.put_bytes(key,Path(source).read_bytes(),content_type)
 def local_path(self,key):return None
 def get_bytes(self,key):raise NotImplementedError
 def signed_url(self,key,*,filename,expires=300):return None
 def healthcheck(self):return True
class LocalObjectStorage(ObjectStorage):
 name='local'
 def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
 def put_bytes(self,key,data,content_type):p=self.root/key;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return key
 def put_file(self,key,source,content_type):
  import shutil
  p=self.root/key;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,p);return key
 def local_path(self,key):
  p=(self.root/key).resolve();root=self.root.resolve()
  if root not in p.parents and p!=root:raise ValueError('invalid object key')
  return p
 def get_bytes(self,key):return self.local_path(key).read_bytes()
 def healthcheck(self):p=self.root/'.healthcheck';p.write_text('ok');p.unlink(missing_ok=True);return True
class S3ObjectStorage(ObjectStorage):
 name='s3'
 def __init__(self,*,bucket,region=None,endpoint_url=None,access_key=None,secret_key=None):
  import boto3
  self.bucket=bucket;self.client=boto3.client('s3',region_name=region,endpoint_url=endpoint_url,aws_access_key_id=access_key,aws_secret_access_key=secret_key)
 def put_bytes(self,key,data,content_type):self.client.put_object(Bucket=self.bucket,Key=key,Body=data,ContentType=content_type);return key
 def put_file(self,key,source,content_type):self.client.upload_file(str(source),self.bucket,key,ExtraArgs={'ContentType':content_type});return key
 def get_bytes(self,key):return self.client.get_object(Bucket=self.bucket,Key=key)['Body'].read()
 def healthcheck(self):self.client.head_bucket(Bucket=self.bucket);return True
 def signed_url(self,key,*,filename,expires=300):return self.client.generate_presigned_url('get_object',Params={'Bucket':self.bucket,'Key':key,'ResponseContentDisposition':f'attachment; filename="{filename}"'},ExpiresIn=expires)
def build_storage(settings,asset_dir):
 if settings.storage_backend=='s3':
  if not settings.s3_bucket:raise RuntimeError('S3_BUCKET is required when WORLDFORGE_STORAGE_BACKEND=s3')
  return S3ObjectStorage(bucket=settings.s3_bucket,region=settings.s3_region,endpoint_url=settings.s3_endpoint_url,access_key=settings.s3_access_key,secret_key=settings.s3_secret_key)
 return LocalObjectStorage(asset_dir)
