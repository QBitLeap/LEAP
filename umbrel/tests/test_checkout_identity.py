"""A checkout rename must not change protocol, app IDs or data bindings."""
import importlib.util,json,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class CheckoutIdentityTests(unittest.TestCase):
 def test_arbitrary_directory_keeps_public_identity_and_own_data(self):
  with tempfile.TemporaryDirectory() as td:
   alias=Path(td)/'arbitrary-folder';shutil.copytree(ROOT,alias,ignore=shutil.ignore_patterns('.git','.release','__pycache__'))
   spec=importlib.util.spec_from_file_location('renamed_package',alias/'scripts/prepare_app_package.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
   output=Path(td)/'package'
   module.prepare(output,'qbitleap-leap-main','main',5101,'ghcr.io/qbitleap/leap-indexer@sha256:'+'a'*64,'qbitleap-core-main')
   manifest=json.loads((output/'umbrel-app.yml').read_text());compose=json.loads((output/'docker-compose.yml').read_text())
   self.assertEqual(manifest['repo'],'https://github.com/QBitLeap/LEAP')
   self.assertEqual(manifest['dependencies'],['qbitleap-core-main'])
   self.assertEqual(manifest['id'],'qbitleap-leap-main')
   self.assertEqual(compose['services']['indexer']['environment']['INDEX_PROTOCOL'],'leap')
   self.assertEqual(compose['services']['indexer']['volumes'],['${APP_DATA_DIR}/data/index:/data'])
