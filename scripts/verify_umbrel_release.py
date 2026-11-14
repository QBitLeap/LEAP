"""Prepare catalog-compatible Umbrel packages from this repository's published image."""
import argparse,importlib.util,json,os,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('version');p.add_argument('catalog',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
 if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9][a-z0-9.-]*)?',a.version):raise ValueError('Explicit image version required')
 receipt=json.loads((ROOT/'umbrel/.release'/a.version/'published-images.json').read_text())
 images=receipt['images']
 if len(images)!=1 or images[0]['component']!='leap-indexer' or images[0]['published'] is not True:raise ValueError('Published LEAP image required')
 image=images[0]['image'];revision=images[0]['sourceRevision']
 if not re.fullmatch('ghcr.io/qbitleap/leap-indexer@sha256:[0-9a-f]{64}',image):raise ValueError('Immutable stable LEAP registry identity required')
 if revision!=os.environ.get('GITHUB_SHA'):raise ValueError('Image must identify this workflow revision')
 a.output.mkdir(parents=True,exist_ok=False)
 spec=importlib.util.spec_from_file_location('leap_package',ROOT/'umbrel/scripts/prepare_app_package.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 for chain,port in (('main',5101),('testnet4',5111)):
  module.prepare(a.output/('qbitleap-leap-'+chain),'qbitleap-leap-'+chain,chain,port,image,'qbitleap-core-'+chain,
                 icon_url='https://raw.githubusercontent.com/QBitLeap/LEAP/'+revision+'/umbrel/icon.svg',package_version=a.version)
 lock=json.loads((a.catalog/'images.lock.json').read_text());lock['leap']=image
 sources=json.loads((a.catalog/'sources.lock.json').read_text());sources['apps']['leap']={'repository':'https://github.com/QBitLeap/LEAP.git','commit':revision,'packageCommit':revision,'path':'umbrel'}
 (a.output/'images.json').write_text(json.dumps(lock,indent=2)+'\n');(a.output/'sources.json').write_text(json.dumps(sources,indent=2)+'\n')
 print('Prepared exact public app IDs, Core dependencies, data paths and immutable image for isolated integration; no installed apps changed.')
if __name__=='__main__':main()
