"""Validate every advertised reference design using real provider schemas, no cloud access."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints import build
from domain import Spec
from run_cloud import account_guard

terraform=shutil.which('terraform')
if not terraform:
    raise SystemExit('Terraform is required for provider validation.')
terraform=str(Path(terraform).resolve())
results=[]
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)
    cache=root/'provider-cache';cache.mkdir()
    def family(cloud):
        rows=[]
        workloads=['storage','container']+(['static_site','database','container_database','container_production'] if cloud=='aws' else [])
        for workload in workloads:
            s=Spec(cloud=cloud,workload='container' if workload.startswith('container') else workload,region={'aws':'us-east-1','google':'us-central1','azurerm':'eastus'}[cloud],name='testproject',project_id='my-project-123' if cloud=='google' else None,subscription_id='11111111-1111-1111-1111-111111111111' if cloud=='azurerm' else None,budget=100,public=workload!='storage',image='nginx:stable',port=80,database=workload=='container_database',environment='production' if workload=='container_production' else 'development',domain_name='app.example.com' if workload=='container_production' else None,certificate_arn='arn:aws:acm:us-east-1:111111111111:certificate/11111111-1111-1111-1111-111111111111' if workload=='container_production' else None).model_dump()
            code,summary=build(s)
            if cloud == "aws":
                code=account_guard(code, cloud, "111111111111", "arn:aws:iam::111111111111:policy/MakeCloudRuntimeBoundary-example")
            directory=root/(cloud+'-'+workload);directory.mkdir()
            (directory/'main.tf').write_text(code)
            env={**os.environ,'TF_PLUGIN_CACHE_DIR':str(cache),'TF_IN_AUTOMATION':'1','TF_INPUT':'0'}
            for key in list(env):
                if key.startswith(('AWS_','ARM_','GOOGLE_','OPENAI_','MAKECLOUD_','CLOUDSDK_')):env.pop(key,None)
            init=subprocess.run([terraform,'init','-backend=false','-input=false','-no-color'],cwd=directory,env=env,capture_output=True,text=True,timeout=300)
            if init.returncode:raise AssertionError(init.stdout+init.stderr)
            formatted=subprocess.run([terraform,'fmt','-no-color'],cwd=directory,env=env,capture_output=True,text=True,timeout=30)
            assert formatted.returncode==0,formatted.stdout+formatted.stderr
            validation=subprocess.run([terraform,'validate','-json'],cwd=directory,env=env,capture_output=True,text=True,timeout=60)
            report=json.loads(validation.stdout)
            if not report['valid']:raise AssertionError(cloud+' '+workload+' '+json.dumps(report))
            row={'cloud':cloud,'workload':workload,'valid':True,'warnings':report['warning_count']}
            rows.append(row)
            print(cloud+' '+workload+': real provider validation PASS',flush=True)
        return rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        for family_results in executor.map(family,sys.argv[1:] or ['aws','google','azurerm']):results+=family_results
if os.getenv('MAKECLOUD_VALIDATION_REPORT'):
    Path(os.environ['MAKECLOUD_VALIDATION_REPORT']).write_text(json.dumps(results,indent=2)+'\n')
print(f'{len(results)} configurations validated; no cloud plan or apply executed.')
